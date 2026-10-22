# Clio — Copyright (C) 2026 Sean Rago
# Licensed under the GNU Affero General Public License v3.0 or later.
# See LICENSE in the project root, or <https://www.gnu.org/licenses/>.

"""
browser.py — Playwright-based browser controller for Clio.

Manages a single browser session at a time. Visible (headed) by default so
the user can watch what Clio is doing; headless is opt-in. On Linux, headed
mode requires a DISPLAY environment variable to be set (e.g. via Xvfb or a
real display).

Playwright's sync API is bound to the thread that started it (greenlet), but
the agent runs tools via asyncio.to_thread, which hands each call to whatever
pool thread is free — so every public function here is pinned to one
dedicated browser thread via @_on_browser_thread.
"""

import base64
import functools
import os
import platform
from concurrent.futures import ThreadPoolExecutor
from typing import Optional


# Module-level state — one browser session at a time
_playwright = None
_browser = None
_page = None
_headless: bool = False

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="clio-browser")


def _on_browser_thread(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return _executor.submit(fn, *args, **kwargs).result()
    return wrapper


def _require_page():
    if _page is None:
        raise RuntimeError("No browser session open. Call browser_open first.")
    return _page


@_on_browser_thread
def browser_open(url: str = "", headless: bool = False) -> str:
    global _playwright, _browser, _page, _headless

    if _browser is not None:
        try:
            _browser.close()
        except Exception:
            pass
    if _playwright is not None:
        try:
            _playwright.stop()
        except Exception:
            pass

    _headless = headless

    from playwright.sync_api import sync_playwright
    _playwright = sync_playwright().start()

    launch_kwargs = {"headless": headless}
    if not headless and platform.system() == "Linux":
        os.environ.setdefault("DISPLAY", ":0")

    _browser = _playwright.chromium.launch(**launch_kwargs)
    _page = _browser.new_page()

    if url:
        _page.goto(url, wait_until="domcontentloaded", timeout=30000)
        return f"Browser opened {'headless' if headless else 'visible'} and navigated to {url}"
    return f"Browser opened {'headless' if headless else 'visible'}"


@_on_browser_thread
def browser_navigate(url: str) -> str:
    page = _require_page()
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    return f"Navigated to {url} — title: {page.title()}"


@_on_browser_thread
def browser_click(selector: str) -> str:
    page = _require_page()

    try:
        page.click(selector, timeout=5000)
        return f"Clicked: {selector}"
    except Exception:
        pass

    try:
        page.get_by_text(selector, exact=False).first.click(timeout=5000)
        return f"Clicked element with text: {selector}"
    except Exception:
        pass

    try:
        page.get_by_role("button", name=selector).click(timeout=5000)
        return f"Clicked button: {selector}"
    except Exception:
        pass

    return f"Error: could not find element to click: {selector}"


@_on_browser_thread
def browser_type(selector: str, text: str) -> str:
    page = _require_page()

    try:
        page.fill(selector, text, timeout=5000)
        return f"Typed into {selector}"
    except Exception:
        pass

    try:
        page.get_by_placeholder(selector).fill(text, timeout=5000)
        return f"Typed into placeholder: {selector}"
    except Exception:
        pass

    try:
        page.get_by_label(selector).fill(text, timeout=5000)
        return f"Typed into label: {selector}"
    except Exception:
        pass

    return f"Error: could not find input field: {selector}"


@_on_browser_thread
def browser_screenshot() -> dict:
    """Take a screenshot. Returns a dict with base64 PNG for Claude to analyze."""
    page = _require_page()
    png_bytes = page.screenshot(type="png", full_page=False)
    b64 = base64.standard_b64encode(png_bytes).decode("utf-8")
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": b64,
        },
    }


@_on_browser_thread
def browser_get_content() -> str:
    """Return readable text content of the current page."""
    page = _require_page()

    text = page.evaluate("""() => {
        const remove = ['script', 'style', 'nav', 'footer', 'header', 'aside', 'noscript'];
        remove.forEach(tag => {
            document.querySelectorAll(tag).forEach(el => el.remove());
        });
        return document.body ? document.body.innerText : '';
    }""")

    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    cleaned = "\n".join(lines)
    if len(cleaned) > 40000:
        cleaned = cleaned[:40000] + "\n[truncated]"

    return f"URL: {page.url}\nTitle: {page.title()}\n\n{cleaned}"


@_on_browser_thread
def browser_get_elements() -> str:
    """Return interactive elements on the page: buttons, links, inputs."""
    page = _require_page()

    elements = page.evaluate("""() => {
        const results = [];
        document.querySelectorAll('button, [role="button"]').forEach(el => {
            const text = el.innerText || el.getAttribute('aria-label') || '';
            if (text.trim()) results.push({type: 'button', label: text.trim().slice(0, 80)});
        });
        document.querySelectorAll('a[href]').forEach(el => {
            const text = el.innerText || el.getAttribute('aria-label') || '';
            const href = el.getAttribute('href') || '';
            if (text.trim()) results.push({type: 'link', label: text.trim().slice(0, 80), href: href.slice(0, 100)});
        });
        document.querySelectorAll('input, textarea, select').forEach(el => {
            const label = el.getAttribute('placeholder') || el.getAttribute('aria-label') || el.getAttribute('name') || el.type || '';
            results.push({type: 'input', label: label.slice(0, 80)});
        });
        return results.slice(0, 100);
    }""")

    if not elements:
        return "No interactive elements found."

    lines = []
    for el in elements:
        kind = el.get("type", "?")
        label = el.get("label", "")
        href = el.get("href", "")
        if kind == "link" and href:
            lines.append(f"[{kind}] {label} → {href}")
        else:
            lines.append(f"[{kind}] {label}")

    return "\n".join(lines)


@_on_browser_thread
def browser_close() -> str:
    global _playwright, _browser, _page

    closed = False
    if _browser is not None:
        try:
            _browser.close()
            closed = True
        except Exception:
            pass
        _browser = None

    if _playwright is not None:
        try:
            _playwright.stop()
        except Exception:
            pass
        _playwright = None

    _page = None
    return "Browser closed." if closed else "No browser session was open."
