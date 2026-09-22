import asyncio
import os
import signal
import struct

import pytest

from server.terminal_session import TerminalSession


def _kill_session(session: TerminalSession):
    """Test-only cleanup: hard-kill whatever shell a test spawned so it
    doesn't linger past the test. Uses the real pid directly (waitpid
    happens naturally once the event loop sees EOF and _teardown runs)."""
    if session._pid is not None:
        try:
            os.killpg(session._pid, signal.SIGKILL)
        except OSError:
            pass


# ── #3: concurrent bash_command calls must not clobber each other's _pending ──

def test_concurrent_commands_do_not_clobber_each_other():
    async def scenario():
        session = TerminalSession()
        try:
            results = await asyncio.gather(
                session.run_command_as_clio("sleep 0.3; echo first"),
                session.run_command_as_clio("echo second"),
            )
            return results
        finally:
            _kill_session(session)
            await asyncio.sleep(0.1)

    results = asyncio.run(scenario())
    assert "first" in results[0]
    assert "second" in results[1]
    # Each call got its own output, not a mix of the other's.
    assert "second" not in results[0]
    assert "first" not in results[1]


def test_concurrent_commands_serialize_not_interleave():
    """The second call's command should only be typed into the shell after
    the first call has fully finished (lock held for the whole lifecycle),
    not the moment the first call starts waiting."""
    async def scenario():
        session = TerminalSession()
        order = []

        async def first():
            await session.run_command_as_clio("sleep 0.2")
            order.append("first-done")

        async def second():
            await asyncio.sleep(0.05)  # ensure this starts while first is still running
            await session.run_command_as_clio("echo hi")
            order.append("second-done")

        try:
            await asyncio.gather(first(), second())
        finally:
            _kill_session(session)
            await asyncio.sleep(0.1)
        return order

    order = asyncio.run(scenario())
    assert order == ["first-done", "second-done"]


# ── #9: PTY child is reaped, not left a zombie ────────────────────────────────

def test_shell_exit_is_reaped_not_zombied():
    async def scenario():
        session = TerminalSession()
        await session.ensure_started()
        pid = session._pid
        os.write(session._master_fd, b"exit\n")
        # Give the event loop a chance to see EOF and run _teardown().
        for _ in range(50):
            await asyncio.sleep(0.05)
            if session._pid is None:
                break
        return pid

    pid = asyncio.run(scenario())
    assert pid is not None
    # If _teardown() reaped it, waitpid on it now raises ChildProcessError
    # (no such child) rather than blocking or succeeding again.
    with pytest.raises(ChildProcessError):
        os.waitpid(pid, 0)


# ── #10: resize() must not crash on out-of-range/invalid values ──────────────

def test_resize_survives_out_of_range_values():
    session = TerminalSession()
    session._master_fd = 0  # any valid-looking fd; struct.pack fails before ioctl is reached
    # Out of range for an unsigned short (0-65535) — raises struct.error.
    session.resize(cols=999999, rows=999999)  # should not raise


def test_resize_survives_wrong_type():
    session = TerminalSession()
    session._master_fd = 0
    # struct.pack also raises struct.error (not TypeError) for a non-int —
    # same exception class as the out-of-range case above.
    session.resize(cols="not a number", rows=24)  # should not raise


def test_resize_noop_when_not_started():
    session = TerminalSession()
    session.resize(cols=80, rows=24)  # no master_fd yet — should just return
