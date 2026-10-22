# Clio — Copyright (C) 2026 Sean Rago
# Licensed under the GNU Affero General Public License v3.0 or later.
# See LICENSE in the project root, or <https://www.gnu.org/licenses/>.

"""
Gmail access for Clio's email_* tools.

One-time setup (on the machine running Clio, needs a browser):
    1. In the Google Cloud project that owns server/credentials.json, enable
       the Gmail API and add your address as a test user on the OAuth consent
       screen.
    2. .venv/bin/python -m server.gmail auth
This writes server/gmail_token.json, which is reused and refreshed after that.

Scope is gmail.modify: read, label, archive, trash, draft and send — but never
permanent deletion. Trash is recoverable for 30 days.
"""

import base64
import html
import re
import sys
from email.message import EmailMessage
from email.utils import getaddresses
from pathlib import Path

import requests
from bs4 import BeautifulSoup

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

_SERVER_DIR = Path(__file__).parent
CREDENTIALS_FILE = _SERVER_DIR / "credentials.json"
TOKEN_FILE = _SERVER_DIR / "gmail_token.json"

BODY_LIMIT = 6000          # chars per message body returned to Clio
THREAD_MESSAGE_LIMIT = 10  # most recent messages returned from a thread
SEARCH_MAX = 50

_service = None


class GmailNotConnected(Exception):
    pass


# ── auth ──────────────────────────────────────────────────────────────────────

def _load_credentials(interactive: bool = False):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as e:
            if not interactive:
                raise GmailNotConnected(
                    f"Gmail token could not be refreshed ({e}). "
                    "Re-run: .venv/bin/python -m server.gmail auth"
                )
            creds = None

    if not creds or not creds.valid:
        if not interactive:
            raise GmailNotConnected(
                "Gmail isn't connected yet. Run on this machine: "
                ".venv/bin/python -m server.gmail auth"
            )
        if not CREDENTIALS_FILE.exists():
            raise GmailNotConnected(f"Missing OAuth client file: {CREDENTIALS_FILE}")
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
        creds = flow.run_local_server(port=0)

    TOKEN_FILE.write_text(creds.to_json())
    TOKEN_FILE.chmod(0o600)
    return creds


def _gmail():
    global _service
    if _service is None:
        from googleapiclient.discovery import build
        _service = build("gmail", "v1", credentials=_load_credentials(), cache_discovery=False)
    return _service


# ── parsing helpers ───────────────────────────────────────────────────────────

def _headers(payload: dict) -> dict:
    return {h["name"].lower(): h["value"] for h in payload.get("headers", [])}


# Marketing mail pads its preview text with invisible characters (combining
# grapheme joiner, zero-width spaces, BOMs, soft hyphens) to push the body out
# of the inbox preview. They carry nothing and cost tokens.
_INVISIBLE_RE = re.compile("[\u034f\u00ad\u200b-\u200d\u2060\ufeff]")


def _clean_snippet(snippet: str) -> str:
    text = _INVISIBLE_RE.sub("", html.unescape(snippet))
    return " ".join(text.split())


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")


def _find_part(payload: dict, mime: str):
    if payload.get("mimeType") == mime and payload.get("body", {}).get("data"):
        return payload["body"]["data"]
    for part in payload.get("parts", []) or []:
        found = _find_part(part, mime)
        if found:
            return found
    return None


def _attachment_names(payload: dict) -> list[str]:
    names = []
    for part in payload.get("parts", []) or []:
        if part.get("filename"):
            names.append(part["filename"])
        names.extend(_attachment_names(part))
    return names


def _body_text(payload: dict) -> str:
    plain = _find_part(payload, "text/plain")
    if plain:
        text = _decode(plain)
    else:
        html = _find_part(payload, "text/html")
        if not html:
            return ""
        soup = BeautifulSoup(_decode(html), "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        text = soup.get_text("\n")
    lines = [line.rstrip() for line in text.splitlines()]
    # collapse runs of blank lines
    out, blank = [], False
    for line in lines:
        if not line.strip():
            if not blank:
                out.append("")
            blank = True
        else:
            out.append(line)
            blank = False
    text = "\n".join(out).strip()
    if len(text) > BODY_LIMIT:
        text = text[:BODY_LIMIT] + f"\n[… truncated, {len(text) - BODY_LIMIT} more chars]"
    return text


def _format_message(msg: dict) -> str:
    payload = msg.get("payload", {})
    h = _headers(payload)
    lines = [
        f"Message ID: {msg['id']}   Thread ID: {msg['threadId']}",
        f"From: {h.get('from', '')}",
        f"To: {h.get('to', '')}",
    ]
    if h.get("cc"):
        lines.append(f"Cc: {h['cc']}")
    lines += [
        f"Date: {h.get('date', '')}",
        f"Subject: {h.get('subject', '(no subject)')}",
        f"Labels: {', '.join(msg.get('labelIds', []))}",
    ]
    attachments = _attachment_names(payload)
    if attachments:
        lines.append(f"Attachments: {', '.join(attachments)}")
    if h.get("list-unsubscribe"):
        lines.append("Unsubscribe available: yes (use email_unsubscribe)")
    lines += ["", _body_text(payload) or "(no text body)"]
    return "\n".join(lines)


# ── tools ─────────────────────────────────────────────────────────────────────

def search(query: str, max_results: int = 15) -> str:
    max_results = max(1, min(int(max_results or 15), SEARCH_MAX))
    svc = _gmail()
    resp = svc.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
    ids = [m["id"] for m in resp.get("messages", [])]
    if not ids:
        return f"No messages match: {query}"

    rows = []
    for mid in ids:
        m = svc.users().messages().get(
            userId="me", id=mid, format="metadata",
            metadataHeaders=["From", "Subject", "Date"],
        ).execute()
        h = _headers(m.get("payload", {}))
        unread = "UNREAD " if "UNREAD" in m.get("labelIds", []) else ""
        rows.append(
            f"- [{m['id']}] {unread}{h.get('date', '')}\n"
            f"  From: {h.get('from', '')}\n"
            f"  Subject: {h.get('subject', '(no subject)')}\n"
            f"  {_clean_snippet(m.get('snippet', ''))}"
        )
    more = " (more exist — narrow the query or raise max_results)" if resp.get("nextPageToken") else ""
    return f"{len(rows)} message(s) for `{query}`{more}:\n" + "\n".join(rows)


def read(message_id: str = "", thread_id: str = "") -> str:
    svc = _gmail()
    if thread_id:
        t = svc.users().threads().get(userId="me", id=thread_id, format="full").execute()
        msgs = t.get("messages", [])
        skipped = max(0, len(msgs) - THREAD_MESSAGE_LIMIT)
        parts = [_format_message(m) for m in msgs[-THREAD_MESSAGE_LIMIT:]]
        head = f"Thread {thread_id}: {len(msgs)} message(s)"
        if skipped:
            head += f", showing the latest {THREAD_MESSAGE_LIMIT}"
        return head + "\n\n" + "\n\n────────\n\n".join(parts)
    if message_id:
        m = svc.users().messages().get(userId="me", id=message_id, format="full").execute()
        return _format_message(m)
    return "Error: give message_id or thread_id."


def list_labels() -> str:
    labels = _gmail().users().labels().list(userId="me").execute().get("labels", [])
    system = sorted(l["name"] for l in labels if l["type"] == "system")
    user = sorted(l["name"] for l in labels if l["type"] == "user")
    return "System labels: " + ", ".join(system) + "\nYour labels: " + (", ".join(user) or "(none)")


def _label_ids(names: list[str], create: bool) -> list[str]:
    if not names:
        return []
    svc = _gmail()
    existing = svc.users().labels().list(userId="me").execute().get("labels", [])
    by_name = {l["name"].lower(): l["id"] for l in existing}
    by_name.update({l["id"].lower(): l["id"] for l in existing})
    ids = []
    for name in names:
        key = name.strip().lower()
        if key in by_name:
            ids.append(by_name[key])
        elif create:
            new = svc.users().labels().create(userId="me", body={"name": name.strip()}).execute()
            ids.append(new["id"])
        else:
            raise ValueError(f"No label named '{name}'")
    return ids


def modify(message_ids: list[str], add_labels: list[str] | None = None,
           remove_labels: list[str] | None = None) -> str:
    if not message_ids:
        return "Error: no message_ids given."
    add = _label_ids(add_labels or [], create=True)
    remove = _label_ids(remove_labels or [], create=False)
    if not add and not remove:
        return "Error: nothing to add or remove."
    _gmail().users().messages().batchModify(
        userId="me", body={"ids": message_ids, "addLabelIds": add, "removeLabelIds": remove},
    ).execute()
    return (f"Updated {len(message_ids)} message(s)."
            + (f" Added: {', '.join(add_labels)}." if add_labels else "")
            + (f" Removed: {', '.join(remove_labels)}." if remove_labels else ""))


def trash(message_ids: list[str]) -> str:
    if not message_ids:
        return "Error: no message_ids given."
    svc = _gmail()
    for mid in message_ids:
        svc.users().messages().trash(userId="me", id=mid).execute()
    return f"Moved {len(message_ids)} message(s) to Trash (recoverable for 30 days)."


def _build_mime(to: str, subject: str, body: str, cc: str = "",
                reply_to_message_id: str = "") -> tuple[dict, str]:
    """Returns (raw-message body for the API, thread_id or '')."""
    msg = EmailMessage()
    thread_id = ""
    if reply_to_message_id:
        orig = _gmail().users().messages().get(
            userId="me", id=reply_to_message_id, format="metadata",
            metadataHeaders=["From", "Reply-To", "Subject", "Message-ID", "References"],
        ).execute()
        h = _headers(orig.get("payload", {}))
        thread_id = orig["threadId"]
        if not to:
            to = h.get("reply-to") or h.get("from", "")
        if not subject:
            subj = h.get("subject", "")
            subject = subj if subj.lower().startswith("re:") else f"Re: {subj}"
        if h.get("message-id"):
            msg["In-Reply-To"] = h["message-id"]
            msg["References"] = (h.get("references", "") + " " + h["message-id"]).strip()
    if not to:
        raise ValueError("No recipient (to) given.")
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["Subject"] = subject or "(no subject)"
    msg.set_content(body)
    raw = {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}
    if thread_id:
        raw["threadId"] = thread_id
    return raw, thread_id


def draft(to: str = "", subject: str = "", body: str = "", cc: str = "",
          reply_to_message_id: str = "") -> str:
    raw, thread_id = _build_mime(to, subject, body, cc, reply_to_message_id)
    d = _gmail().users().drafts().create(userId="me", body={"message": raw}).execute()
    where = f" in thread {thread_id}" if thread_id else ""
    return f"Draft saved{where} (draft ID {d['id']}). It's in Gmail's Drafts folder."


def send(to: str = "", subject: str = "", body: str = "", cc: str = "",
         reply_to_message_id: str = "") -> str:
    raw, thread_id = _build_mime(to, subject, body, cc, reply_to_message_id)
    m = _gmail().users().messages().send(userId="me", body=raw).execute()
    return f"Sent (message ID {m['id']})" + (" as a reply in the thread." if thread_id else ".")


def unsubscribe(message_id: str) -> str:
    m = _gmail().users().messages().get(
        userId="me", id=message_id, format="metadata",
        metadataHeaders=["List-Unsubscribe", "List-Unsubscribe-Post", "From"],
    ).execute()
    h = _headers(m.get("payload", {}))
    header = h.get("list-unsubscribe", "")
    if not header:
        return "This message has no unsubscribe header. Open it and look for an unsubscribe link instead."
    targets = [t.strip().strip("<>") for t in header.split(",")]
    https = [t for t in targets if t.lower().startswith("https://")]
    mailto = [t for t in targets if t.lower().startswith("mailto:")]

    # RFC 8058 one-click: a POST with this exact body, no page visit needed.
    if https and "list-unsubscribe=one-click" in h.get("list-unsubscribe-post", "").lower():
        r = requests.post(https[0], data="List-Unsubscribe=One-Click",
                          headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=20)
        if r.ok:
            return f"Unsubscribed from {h.get('from', 'sender')} (one-click, HTTP {r.status_code})."
        return f"One-click unsubscribe returned HTTP {r.status_code}. Try the link manually: {https[0]}"

    if mailto:
        addr, _, query = mailto[0][len("mailto:"):].partition("?")
        params = dict(p.partition("=")[::2] for p in query.split("&") if p)
        from urllib.parse import unquote
        send(to=unquote(addr), subject=unquote(params.get("subject", "unsubscribe")),
             body=unquote(params.get("body", "unsubscribe")))
        return f"Sent an unsubscribe email to {unquote(addr)} for {h.get('from', 'sender')}."

    if https:
        return (f"No one-click option. The unsubscribe page is {https[0]} — "
                "open it with the browser tools if you want to finish it there.")
    return f"Unrecognized unsubscribe header: {header}"


def recipients_summary(to: str, cc: str) -> str:
    addrs = [a or n for n, a in getaddresses([to or "", cc or ""]) if a or n]
    return ", ".join(addrs)


if __name__ == "__main__":
    if sys.argv[1:] == ["auth"]:
        _load_credentials(interactive=True)
        print(f"Gmail connected. Token saved to {TOKEN_FILE}")
        print(list_labels())
    else:
        print("Usage: python -m server.gmail auth")
