import base64
from email import message_from_bytes
from unittest.mock import MagicMock

import pytest

from server import gmail
from server.tools import AUTO_APPROVE, describe_tool_call, execute_tool


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


@pytest.fixture
def svc(monkeypatch):
    service = MagicMock()
    monkeypatch.setattr(gmail, "_gmail", lambda: service)
    return service


def _users(svc):
    return svc.users.return_value


# ── approval policy ───────────────────────────────────────────────────────────

def test_reads_and_drafts_auto_approved_changes_are_not():
    for name in ("email_search", "email_read", "email_list_labels", "email_draft"):
        assert name in AUTO_APPROVE
    for name in ("email_modify", "email_trash", "email_unsubscribe", "email_send"):
        assert name not in AUTO_APPROVE


def test_send_description_shows_full_body():
    desc = describe_tool_call("email_send", {
        "to": "Pat <pat@example.com>", "subject": "Lunch", "body": "Friday works.\n\nSean",
    })
    assert "pat@example.com" in desc
    assert "Lunch" in desc
    assert "Friday works.\n\nSean" in desc


def test_modify_description_names_triage_actions():
    desc = describe_tool_call("email_modify", {
        "message_ids": ["a", "b", "c"], "remove_labels": ["INBOX", "UNREAD"],
    })
    assert desc == "archive + mark read 3 emails"


def test_not_connected_surfaces_setup_command(monkeypatch, tmp_path):
    monkeypatch.setattr(gmail, "_service", None)
    monkeypatch.setattr(gmail, "TOKEN_FILE", tmp_path / "missing.json")
    result = execute_tool("email_search", {"query": "is:unread"})
    assert result.startswith("Error:")
    assert "python -m server.gmail auth" in result


# ── parsing ───────────────────────────────────────────────────────────────────

def test_body_prefers_plain_text():
    payload = {"mimeType": "multipart/alternative", "parts": [
        {"mimeType": "text/plain", "body": {"data": _b64("plain version")}},
        {"mimeType": "text/html", "body": {"data": _b64("<p>html version</p>")}},
    ]}
    assert gmail._body_text(payload) == "plain version"


def test_body_falls_back_to_html_without_scripts():
    payload = {"mimeType": "text/html", "body": {"data": _b64(
        "<html><style>p{}</style><script>x()</script><p>Hello</p><p>World</p></html>"
    )}}
    text = gmail._body_text(payload)
    assert "Hello" in text and "World" in text
    assert "x()" not in text and "p{}" not in text


def test_body_truncated():
    payload = {"mimeType": "text/plain", "body": {"data": _b64("a" * (gmail.BODY_LIMIT + 500))}}
    text = gmail._body_text(payload)
    assert "truncated, 500 more chars" in text


# ── actions ───────────────────────────────────────────────────────────────────

def test_snippet_unescaped_and_padding_stripped():
    raw = "Don&#39;t miss &amp; save \u034f \u034f\u200b \ufeff\u00ad   today"
    assert gmail._clean_snippet(raw) == "Don't miss & save today"


def test_modify_resolves_label_names_and_creates_missing(svc):
    labels = _users(svc).labels.return_value
    labels.list.return_value.execute.return_value = {"labels": [
        {"id": "INBOX", "name": "INBOX", "type": "system"},
        {"id": "Label_1", "name": "Receipts", "type": "user"},
    ]}
    labels.create.return_value.execute.return_value = {"id": "Label_2"}

    gmail.modify(["m1"], add_labels=["receipts", "Travel"], remove_labels=["INBOX"])

    labels.create.assert_called_once_with(userId="me", body={"name": "Travel"})
    body = _users(svc).messages.return_value.batchModify.call_args.kwargs["body"]
    assert body == {"ids": ["m1"], "addLabelIds": ["Label_1", "Label_2"], "removeLabelIds": ["INBOX"]}


def test_reply_threads_and_fills_recipient_and_subject(svc):
    messages = _users(svc).messages.return_value
    messages.get.return_value.execute.return_value = {
        "threadId": "t1",
        "payload": {"headers": [
            {"name": "From", "value": "Pat <pat@example.com>"},
            {"name": "Subject", "value": "Lunch?"},
            {"name": "Message-ID", "value": "<abc@example.com>"},
        ]},
    }
    messages.send.return_value.execute.return_value = {"id": "sent1"}

    result = gmail.send(body="Friday works.", reply_to_message_id="orig1")

    body = messages.send.call_args.kwargs["body"]
    assert body["threadId"] == "t1"
    mime = message_from_bytes(base64.urlsafe_b64decode(body["raw"]))
    assert mime["To"] == "Pat <pat@example.com>"
    assert mime["Subject"] == "Re: Lunch?"
    assert mime["In-Reply-To"] == "<abc@example.com>"
    assert "reply" in result


def test_send_without_recipient_errors(svc):
    with pytest.raises(ValueError):
        gmail.send(body="hi")


def test_unsubscribe_one_click_posts(svc, monkeypatch):
    _users(svc).messages.return_value.get.return_value.execute.return_value = {"payload": {"headers": [
        {"name": "From", "value": "News <news@example.com>"},
        {"name": "List-Unsubscribe", "value": "<mailto:u@example.com>, <https://example.com/u?id=1>"},
        {"name": "List-Unsubscribe-Post", "value": "List-Unsubscribe=One-Click"},
    ]}}
    post = MagicMock(return_value=MagicMock(ok=True, status_code=200))
    monkeypatch.setattr(gmail.requests, "post", post)

    result = gmail.unsubscribe("m1")

    assert post.call_args.args[0] == "https://example.com/u?id=1"
    assert post.call_args.kwargs["data"] == "List-Unsubscribe=One-Click"
    assert "Unsubscribed" in result


def test_unsubscribe_page_only_returns_url_without_visiting(svc, monkeypatch):
    _users(svc).messages.return_value.get.return_value.execute.return_value = {"payload": {"headers": [
        {"name": "List-Unsubscribe", "value": "<https://example.com/prefs>"},
    ]}}
    post = MagicMock()
    monkeypatch.setattr(gmail.requests, "post", post)

    result = gmail.unsubscribe("m1")

    post.assert_not_called()
    assert "https://example.com/prefs" in result
