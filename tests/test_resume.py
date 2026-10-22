import asyncio
from unittest.mock import MagicMock

from server.agent import AgentSession


class FakeSocket:
    def __init__(self, fail: bool = False):
        self.sent: list[dict] = []
        self.fail = fail

    async def send_json(self, message: dict):
        if self.fail:
            raise RuntimeError("socket is dead")
        self.sent.append(message)


def _session(ws) -> AgentSession:
    return AgentSession(ws, MagicMock())


def test_messages_are_numbered_in_order():
    ws = FakeSocket()
    agent = _session(ws)

    async def run():
        await agent.send({"type": "status", "state": "thinking"})
        await agent.send({"type": "turn_end"})

    asyncio.run(run())
    assert [m["seq"] for m in ws.sent] == [1, 2]
    assert ws.sent[1]["type"] == "turn_end"


def test_reconnect_replays_only_what_the_phone_missed():
    first = FakeSocket()
    agent = _session(first)
    second = FakeSocket()

    async def run():
        await agent.send({"type": "user_transcript", "text": "hi"})
        agent.detach(first)
        # The turn keeps going while the phone is away
        await agent.send({"type": "audio_chunk", "text": "Hello."})
        await agent.send({"type": "turn_end"})
        await agent.attach(second, last_seq=1)

    asyncio.run(run())
    assert [m["type"] for m in first.sent] == ["user_transcript"]
    assert [m["type"] for m in second.sent] == ["audio_chunk", "turn_end"]
    assert [m["seq"] for m in second.sent] == [2, 3]


def test_sends_into_a_half_open_socket_are_replayed():
    # The server hasn't noticed the socket died, so sends "succeed" or raise —
    # either way the phone's last_seq tells the server what to resend.
    dead = FakeSocket(fail=True)
    agent = _session(dead)
    fresh = FakeSocket()

    async def run():
        await agent.send({"type": "audio_chunk", "text": "Lost?"})
        await agent.attach(fresh, last_seq=0)

    asyncio.run(run())
    assert [m["text"] for m in fresh.sent] == ["Lost?"]


def test_detaching_a_stale_socket_keeps_the_current_one():
    old, new = FakeSocket(), FakeSocket()
    agent = _session(old)

    async def run():
        await agent.attach(new, last_seq=0)
        agent.detach(old)  # the old connection's cleanup runs after the takeover
        await agent.send({"type": "turn_end"})

    asyncio.run(run())
    assert agent.websocket is new
    assert [m["type"] for m in new.sent] == ["turn_end"]
