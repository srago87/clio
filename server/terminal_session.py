# Clio — Copyright (C) 2026 Sean Rago
# Licensed under the GNU Affero General Public License v3.0 or later.
# See LICENSE in the project root, or <https://www.gnu.org/licenses/>.

"""One real, persistent, shared shell session — the terminal pane's backend.

Both Clio (via bash_command) and the human (typing directly into the terminal
pane) drive the *same* PTY, one at a time. There's exactly one reader of the
PTY's master fd (registered with the event loop), which feeds three things on
every chunk of output: the scrollback buffer (for replaying history to a
newly (re)connected display), every currently-connected WebSocket (live,
always, regardless of whose turn it is), and — if one is armed — the sentinel
watch a pending bash_command call is waiting on. See the "Terminal pane v2"
plan for the full design and why this isn't built on pexpect.
"""

import asyncio
import fcntl
import os
import pty
import re
import signal
import struct
import termios
import uuid
from pathlib import Path

_WORK_DIR = Path(__file__).parent.parent.parent

SCROLLBACK_LIMIT = 200_000  # chars kept for replay to a newly connected client
COMMAND_OUTPUT_LIMIT = 10_000  # chars returned to Claude per bash_command call
COMMAND_TIMEOUT = 120  # seconds before a command is treated as hung, not interrupted


class TerminalSession:
    def __init__(self):
        self._master_fd: int | None = None
        self._pid: int | None = None
        self.owner = "human"  # "human" | "clio" — who may currently type
        self._scrollback = ""
        self._clients: set = set()
        self._pending: dict | None = None  # {"pattern", "future", "buffer"} while a bash_command call is in flight
        self._interrupt_event = asyncio.Event()
        self._start_lock = asyncio.Lock()
        # Guards the whole run_command_as_clio lifecycle (owner + self._pending),
        # not just start-up — terminal_session is a module-level singleton shared
        # by every AgentSession, so two concurrent bash_command calls without this
        # would both set self._pending and the second would clobber the first.
        self._command_lock = asyncio.Lock()

    # ── lifecycle ────────────────────────────────────────────────────────

    async def ensure_started(self):
        if self._master_fd is not None:
            return
        async with self._start_lock:
            if self._master_fd is not None:
                return
            self._spawn()
            loop = asyncio.get_running_loop()
            loop.add_reader(self._master_fd, self._on_readable)

    def _spawn(self):
        pid, fd = pty.fork()
        if pid == 0:
            # Child: become the shell, in the work dir, with a real TERM so
            # color/interactive output (ls --color, git, progress bars) renders
            # the way it would in an actual terminal.
            os.chdir(str(_WORK_DIR))
            shell = os.environ.get("SHELL", "/bin/bash")
            env = dict(os.environ)
            env["TERM"] = "xterm-256color"
            os.execvpe(shell, [shell], env)
            os._exit(1)  # pragma: no cover — only reached if execvpe fails
        self._pid = pid
        self._master_fd = fd
        os.set_blocking(fd, False)

    def _on_readable(self):
        try:
            chunk = os.read(self._master_fd, 65536)
        except OSError:
            chunk = b""
        if not chunk:
            # Shell exited (e.g. someone typed `exit`) — tear down and let the
            # next ensure_started() respawn a fresh one.
            self._teardown()
            return
        text = chunk.decode("utf-8", errors="replace")
        self._scrollback = (self._scrollback + text)[-SCROLLBACK_LIMIT:]
        if self._pending is not None:
            self._pending["buffer"] += text
            match = self._pending["pattern"].search(self._pending["buffer"])
            if match and not self._pending["future"].done():
                self._pending["future"].set_result((self._pending["buffer"][:match.start()], match.group(1)))
        asyncio.ensure_future(self._broadcast({"type": "output", "data": text}))

    def _teardown(self):
        loop = asyncio.get_running_loop()
        if self._master_fd is not None:
            try:
                loop.remove_reader(self._master_fd)
            except (ValueError, OSError):
                pass
            try:
                os.close(self._master_fd)
            except OSError:
                pass
        if self._pid is not None:
            # EOF on the master fd means the child is gone or about to be —
            # reap it so it doesn't sit around as a zombie under this
            # long-running server process until the next respawn (and the
            # one after that, ...).
            try:
                os.waitpid(self._pid, 0)
            except OSError:
                pass
        self._master_fd = None
        self._pid = None
        self.owner = "human"

    # ── WebSocket clients ────────────────────────────────────────────────

    async def subscribe(self, websocket):
        await self.ensure_started()
        self._clients.add(websocket)
        if self._scrollback:
            await websocket.send_json({"type": "output", "data": self._scrollback})
        await websocket.send_json({"type": "owner", "owner": self.owner})

    def unsubscribe(self, websocket):
        self._clients.discard(websocket)

    async def _broadcast(self, message: dict):
        dead = []
        for ws in self._clients:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)

    async def _set_owner(self, owner: str):
        self.owner = owner
        await self._broadcast({"type": "owner", "owner": owner})

    async def write_system_line(self, text: str) -> None:
        """Append a formatted line to the same scrollback and live broadcast
        as the interactive session — not real PTY input/output, for things
        like run_background/check_job/stop_job/list_jobs results, which don't
        run in the shared shell but should still show up in the same terminal
        history. Routing these through here (instead of a separate client-side
        mechanism) means they persist and replay correctly even if the
        terminal pane isn't the one currently focused when they happen."""
        formatted = f"\r\n{text}\r\n"
        self._scrollback = (self._scrollback + formatted)[-SCROLLBACK_LIMIT:]
        await self._broadcast({"type": "output", "data": formatted})

    # ── human input ──────────────────────────────────────────────────────

    def write_as_human(self, data: str):
        if self.owner != "human" or self._master_fd is None:
            return
        os.write(self._master_fd, data.encode("utf-8", errors="replace"))

    def resize(self, cols: int, rows: int):
        if self._master_fd is None:
            return
        try:
            fcntl.ioctl(self._master_fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        except (OSError, struct.error):
            # struct.error: client-supplied rows/cols out of range or the
            # wrong type — not an OSError subclass, so it needs its own arm
            # or a malformed resize message crashes this connection's loop.
            pass

    def request_interrupt(self):
        self._interrupt_event.set()

    # ── Clio's side (bash_command) ──────────────────────────────────────

    async def run_command_as_clio(self, command: str, cwd: str | None = None) -> str:
        await self.ensure_started()
        async with self._command_lock:
            await self._set_owner("clio")
            try:
                full_command = f"cd {_shell_quote(cwd)} && {command}" if cwd else command
                token = uuid.uuid4().hex[:12]
                pattern = re.compile(rf"__CLIO_DONE_{token}__(\d+)")
                start_marker = f"__CLIO_START_{token}__"
                loop = asyncio.get_running_loop()
                future = loop.create_future()
                self._pending = {"pattern": pattern, "future": future, "buffer": ""}
                self._interrupt_event.clear()

                # Bracket the command's real output with markers *printed as output*,
                # not typed as input — the shell's prompt, echo, and any shell-integration
                # escape sequences (VTE/iTerm2-style OSC 133/666 sequences observed on
                # this machine) all happen before the start marker prints, so slicing
                # between the two markers cleanly excludes all of that noise regardless
                # of how much of it there is. Far more robust than assuming the echo is
                # exactly one line.
                os.write(
                    self._master_fd,
                    f"printf '{start_marker}\\n'; {full_command}; printf '\\n__CLIO_DONE_{token}__%d\\n' $?\n".encode(),
                )

                interrupt_wait = asyncio.ensure_future(self._interrupt_event.wait())
                done, _pending = await asyncio.wait(
                    [future, interrupt_wait], timeout=COMMAND_TIMEOUT, return_when=asyncio.FIRST_COMPLETED
                )

                if future in done:
                    interrupt_wait.cancel()
                    captured, exit_code = future.result()
                    output = _extract_output(captured, start_marker)
                    if len(output) > COMMAND_OUTPUT_LIMIT:
                        output = output[:COMMAND_OUTPUT_LIMIT] + "\n\n[truncated]"
                    if exit_code != "0":
                        output = f"{output}\n[exit code {exit_code}]" if output else f"[exit code {exit_code}]"
                    return output or "(no output)"

                # Either the human interrupted (Esc) or the command just ran long.
                if self._master_fd is not None:
                    try:
                        pgid = os.tcgetpgrp(self._master_fd)
                        os.killpg(pgid, signal.SIGINT)
                        await asyncio.sleep(0.3)
                        if os.tcgetpgrp(self._master_fd) == pgid:
                            # Still the same foreground process group — SIGINT
                            # didn't stop it (ignored/blocked). Escalate to a
                            # signal it can't catch or ignore, then give it a
                            # moment to actually die before handing control back.
                            os.killpg(pgid, signal.SIGKILL)
                            await asyncio.sleep(0.2)
                    except OSError:
                        pass
                else:
                    await asyncio.sleep(0.3)  # let the prompt settle before handing control back
                partial = _extract_output(self._pending["buffer"], start_marker) if self._pending else ""
                reason = "Interrupted by the user" if interrupt_wait.done() else "Timed out"
                return f"{reason} before finishing. Output so far:\n{partial}" if partial else f"{reason} before finishing, no output yet."
            finally:
                self._pending = None
                await self._set_owner("human")


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


# CSI sequences (\x1b[...letter), OSC sequences (\x1b]...BEL or \x1b]...ST — this
# machine's shell emits VTE/iTerm2-style shell-integration OSC 133/666 sequences
# around every prompt), and charset-select sequences (\x1b(B and similar).
_ANSI_RE = re.compile(r"\x1b(\[[0-9;?]*[a-zA-Z]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[()][A-Za-z0-9])")


def _extract_output(captured: str, start_marker: str) -> str:
    """Only text printed as the command's own output should reach Claude — not
    the shell prompt, our own command echoed back, or any shell-integration
    escape sequences the terminal adds around them. The start marker is printed
    by the shell (via printf) rather than typed by us, so it always lands right
    after all of that noise regardless of how much there is; slicing after it
    (rather than assuming the echo is exactly one line) is what makes this work
    on shells with heavy prompt integration, not just a plain bash prompt."""
    # rfind, not find: the marker also appears once inside the echo of the line
    # we typed (which literally contains the text "printf '<marker>'"). Only the
    # *last* occurrence is the marker actually being printed as output.
    idx = captured.rfind(start_marker)
    after = captured[idx + len(start_marker):] if idx != -1 else captured
    cleaned = _ANSI_RE.sub("", after).replace("\r\n", "\n").replace("\r", "")
    return cleaned.strip("\n")


terminal_session = TerminalSession()
