import asyncio

import pytest

from server.main import DisplayLink, LOCAL_ONLY_PORT, SESSION_TOKEN, terminal_websocket_endpoint


# ── #6: DisplayLink.disconnect() must not fail a newer socket's in-flight
#        requests when a stale (already-replaced) socket's disconnect fires ──

def test_disconnect_from_stale_socket_leaves_new_sockets_pending_alone():
    async def scenario():
        link = DisplayLink()
        old_ws, new_ws = object(), object()

        link._ws = old_ws
        fut_old = asyncio.get_running_loop().create_future()
        link._pending["req-old"] = fut_old

        # The new socket takes over (a reconnect), then the old socket's
        # delayed disconnect() finally fires.
        link._ws = new_ws
        fut_new = asyncio.get_running_loop().create_future()
        link._pending["req-new"] = fut_new

        link.disconnect(old_ws)

        assert link._ws is new_ws  # not cleared by the stale disconnect
        assert not fut_old.done()  # not failed either — untouched
        assert not fut_new.done()
        assert "req-new" in link._pending

    asyncio.run(scenario())


def test_disconnect_from_current_socket_fails_pending_requests():
    async def scenario():
        link = DisplayLink()
        ws = object()
        link._ws = ws
        fut = asyncio.get_running_loop().create_future()
        link._pending["req-1"] = fut

        link.disconnect(ws)

        assert link._ws is None
        assert fut.done()
        with pytest.raises(RuntimeError):
            fut.result()
        assert link._pending == {}

    asyncio.run(scenario())


# ── #2: /ws/terminal must reject connections not on the local-only listener ──

class _FakeWebSocket:
    def __init__(self, token: str, server_port: int | None):
        self.query_params = {"token": token}
        self.scope = {"server": (None, server_port)} if server_port is not None else {}
        self.closed_code: int | None = None
        self.accepted = False

    async def close(self, code: int | None = None):
        self.closed_code = code

    async def accept(self):
        self.accepted = True

    async def iter_json(self):
        return
        yield  # pragma: no cover — makes this an async generator


def test_terminal_ws_rejects_public_listener_even_with_valid_token():
    ws = _FakeWebSocket(SESSION_TOKEN, server_port=8765)  # not LOCAL_ONLY_PORT
    asyncio.run(terminal_websocket_endpoint(ws))
    assert ws.closed_code == 4403
    assert not ws.accepted


def test_terminal_ws_rejects_bad_token_regardless_of_listener():
    ws = _FakeWebSocket("wrong-token", server_port=LOCAL_ONLY_PORT)
    asyncio.run(terminal_websocket_endpoint(ws))
    assert ws.closed_code == 4403
    assert not ws.accepted


def test_terminal_ws_accepts_local_listener_with_valid_token(monkeypatch):
    # Don't touch the real module-level terminal_session (a real PTY-backed
    # singleton) — swap in a stub just to prove the request gets past the
    # gate and into the normal handling path.
    class _StubSession:
        async def subscribe(self, websocket):
            self.subscribed = websocket

        def unsubscribe(self, websocket):
            self.unsubscribed = websocket

    stub = _StubSession()
    monkeypatch.setattr("server.main.terminal_session", stub)

    ws = _FakeWebSocket(SESSION_TOKEN, server_port=LOCAL_ONLY_PORT)
    asyncio.run(terminal_websocket_endpoint(ws))
    assert ws.closed_code is None
    assert ws.accepted
    assert stub.subscribed is ws
    assert stub.unsubscribed is ws
