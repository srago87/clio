# Clio — Copyright (C) 2026 Sean Rago
# Licensed under the GNU Affero General Public License v3.0 or later.
# See LICENSE in the project root, or <https://www.gnu.org/licenses/>.

"""
computer.py — real-desktop screenshot + input injection for Clio.

Scoped to non-browser GUI apps only. Browser tasks should go through the
browser_* tools (Playwright) instead — selector-based control is strictly
better than pixel coordinates when it's available; this module exists for
everything else on the real desktop.

Screenshot: the XDG desktop portal (org.freedesktop.portal.Screenshot). GNOME
Shell's own org.gnome.Shell.Screenshot D-Bus interface denies calls from
arbitrary processes now (verified empirically 2026-09-25) — the portal is the
route that still works on GNOME/Mutter under Wayland.

Input injection: ydotool, which injects events at the kernel level via
/dev/uinput — works under Wayland, unlike xdotool (X11-only via XTest, dead
on this machine). Talks to a ydotoold daemon that start.sh launches with a
fixed, user-owned socket path (opening /dev/uinput needs root once, at
daemon startup).

Coordinate mapping verified empirically 2026-09-25: a screenshot's pixel
coordinates and ydotool's absolute mousemove coordinates line up 1:1 on this
machine (no HiDPI/fractional-scaling offset) — a click at a screenshot's
(x, y) lands at that same (x, y).
"""

import base64
import os
import subprocess
from pathlib import Path

_SCREENSHOT_PATH = Path(__file__).parent / "tmp" / "computer_screenshot.png"
_YDOTOOL_SOCKET = Path(__file__).parent / "tmp" / "ydotool.sock"

_CLICK_CODES = {"left": "0xC0", "right": "0xC1", "middle": "0xC2"}


def _capture_portal_screenshot() -> Path:
    """Ask the XDG portal for a screenshot, return the path it wrote to."""
    # Imported here, not at module load: dbus/gi are Linux-only system bindings,
    # and tools.py imports this module on every platform.
    import dbus
    from dbus.mainloop.glib import DBusGMainLoop
    from gi.repository import GLib

    DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()
    portal = bus.get_object("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop")
    screenshot_iface = dbus.Interface(portal, "org.freedesktop.portal.Screenshot")

    result = {}
    loop = GLib.MainLoop()

    def on_response(response_code, results):
        result["code"] = int(response_code)
        result["results"] = dict(results)
        loop.quit()

    handle = screenshot_iface.Screenshot("", {"interactive": False})
    bus.add_signal_receiver(
        on_response,
        signal_name="Response",
        dbus_interface="org.freedesktop.portal.Request",
        path=str(handle),
    )

    GLib.timeout_add_seconds(20, loop.quit)
    loop.run()

    if result.get("code") != 0:
        raise RuntimeError(f"Screenshot portal call failed or timed out: {result or 'no response'}")

    uri = str(result["results"]["uri"])
    return Path(uri.removeprefix("file://"))


def computer_screenshot() -> dict:
    """Capture the real desktop and return it as an image block for Claude to view."""
    src_path = _capture_portal_screenshot()
    _SCREENSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    src_path.replace(_SCREENSHOT_PATH)  # into Clio's own tmp dir, not left in ~/Pictures
    b64 = base64.standard_b64encode(_SCREENSHOT_PATH.read_bytes()).decode("utf-8")
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": b64,
        },
    }


def _ydotool(*args: str) -> None:
    if not _YDOTOOL_SOCKET.exists():
        raise RuntimeError("ydotoold is not running (no socket found) — computer control is unavailable.")
    env = {**os.environ, "YDOTOOL_SOCKET": str(_YDOTOOL_SOCKET)}
    subprocess.run(["ydotool", *args], check=True, capture_output=True, text=True, timeout=10, env=env)


def computer_click(x: int, y: int, button: str = "left") -> str:
    code = _CLICK_CODES.get(button)
    if code is None:
        return f"Error: unknown button '{button}' — use left, right, or middle."
    _ydotool("mousemove", "-a", "-x", str(x), "-y", str(y))
    _ydotool("click", code)
    return f"Clicked {button} at ({x}, {y})"


def computer_type(text: str) -> str:
    _ydotool("type", "--", text)
    return f"Typed: {text}"
