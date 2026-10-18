# Clio — Copyright (C) 2026 Sean Rago
# Licensed under the GNU Affero General Public License v3.0 or later.
# See LICENSE in the project root, or <https://www.gnu.org/licenses/>.

"""A native terminal window onto Clio's shared shell session.

Runs inside a real terminal emulator (Ptyxis etc. — launched by
terminal_session.ensure_window() whenever Clio runs a bash_command and no
window is attached) and speaks the /ws/terminal protocol: scrollback replay on connect, live output after
that, human input forwarded only while it's the human's turn (enforced
server-side), bare Esc interrupts whatever Clio is running.

    .venv/bin/python server/terminal_viewer.py
"""

import asyncio
import json
import os
import re
import shutil
import signal
import sys
import termios
import tty
from pathlib import Path

from websockets.asyncio.client import connect

LOCAL_URL = "ws://127.0.0.1:8766/ws/terminal"
TOKEN_PATH = Path(__file__).parent / ".session_token"

# bash_command brackets every command with printed sentinel markers so the
# server can slice out its output (see terminal_session.run_command_as_clio).
# They're machinery, not something the user needs to read — strip both the
# echoed wrapper and the printed markers so the window shows just the command
# and its output. A marker split across two chunks slips through; cosmetic only.
_MARKER_NOISE = re.compile(
    r"printf '__CLIO_START_\w+__\\n'; "
    r"|; printf '\\n__CLIO_DONE_\w+__%d\\n' \$\?"
    r"|__CLIO_START_\w+__\r?\n"
    r"|\r?\n__CLIO_DONE_\w+__\d+\r?\n"
)


def _set_title(text: str) -> None:
    sys.stdout.write(f"\x1b]0;{text}\x07")
    sys.stdout.flush()


async def _run() -> None:
    token = TOKEN_PATH.read_text().strip()
    async with connect(f"{LOCAL_URL}?token={token}", max_size=None) as ws:
        loop = asyncio.get_running_loop()

        async def send_resize() -> None:
            size = shutil.get_terminal_size()
            await ws.send(json.dumps({"type": "resize", "cols": size.columns, "rows": size.lines}))

        await send_resize()
        loop.add_signal_handler(signal.SIGWINCH, lambda: asyncio.ensure_future(send_resize()))

        stdin_fd = sys.stdin.fileno()

        def on_stdin() -> None:
            data = os.read(stdin_fd, 4096).decode("utf-8", errors="replace")
            if data == "\x1b":
                # Bare Esc (not an arrow-key sequence): stop Clio's command if
                # one is running, and still pass the key through if it's the
                # human's turn (vim etc.) — the server drops whichever doesn't apply.
                asyncio.ensure_future(ws.send(json.dumps({"type": "interrupt"})))
            asyncio.ensure_future(ws.send(json.dumps({"type": "input", "data": data})))

        loop.add_reader(stdin_fd, on_stdin)

        async for raw in ws:
            message = json.loads(raw)
            if message.get("type") == "output":
                sys.stdout.write(_MARKER_NOISE.sub("", message.get("data", "")))
                sys.stdout.flush()
            elif message.get("type") == "owner":
                if message.get("owner") == "clio":
                    _set_title("Clio terminal — Clio is running a command (Esc to stop)")
                else:
                    _set_title("Clio terminal — your turn")


def main() -> None:
    _set_title("Clio terminal")
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    tty.setraw(fd)
    try:
        asyncio.run(_run())
    except (OSError, KeyboardInterrupt) as e:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        print(f"\r\nClio terminal disconnected: {e}\r")
        return
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
    print("\r\nClio server closed the terminal session.\r")


if __name__ == "__main__":
    main()
