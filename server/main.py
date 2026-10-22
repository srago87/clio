# Clio — Copyright (C) 2026 Sean Rago
# Licensed under the GNU Affero General Public License v3.0 or later.
# See LICENSE in the project root, or <https://www.gnu.org/licenses/>.

import asyncio
import base64
import logging
import logging.handlers
import os
import secrets
import sys
import uuid
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from .agent import AgentSession, TMP_DIR, consolidate_sessions
from .delegate import delegation_manager
from .session import VoiceSession
from .stt import reset_last_transcript
from .terminal_session import terminal_session
from .tools import RESTART_CONTEXT_PATH
from .tts import synthesize, TTS_ENGINE, MODELS_DIR, PIPER_MODEL_NAME

SESSION_TOKEN = secrets.token_hex(16)

# How long Clio must have been quiet before it speaks up unprompted about a
# finished delegation — keeps it from talking over the start of a reply.
PROACTIVE_IDLE_SECONDS = 3.0

# How long a conversation survives after its phone connection drops. A
# reconnect within this window (same page load, so same client session id)
# reattaches to it with history intact; after that it's finalized as before.
RESUME_WINDOW_SECONDS = 300

# Live and recently dropped phone conversations, keyed by the client
# session id the phone generates once per page load. Each entry holds the
# AgentSession, its VoiceSession, the WebSocket currently attached (None
# while parked), and the expiry task that finalizes it if nobody returns.
_phone_sessions: dict[str, dict] = {}


class DiffConnectionManager:
    """Manages WebSocket connections for the live diff desktop view."""

    def __init__(self):
        self.connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.connections.remove(websocket)

    async def broadcast(self, payload: dict):
        dead = []
        for ws in self.connections:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.connections.remove(ws)


diff_manager = DiffConnectionManager()


class DisplayLink:
    """Single connection to Clio's display action surface. Unlike the phone,
    only one display runs per machine, so this holds at most one socket —
    and does request/response correlation over it, since the display is
    answering calls Clio's tool loop makes, not just receiving a broadcast."""

    def __init__(self):
        self._ws: WebSocket | None = None
        self._pending: dict[str, asyncio.Future] = {}

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self._ws = websocket

    def disconnect(self, websocket: WebSocket):
        if self._ws is websocket:
            self._ws = None
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(RuntimeError("document viewer disconnected"))
        self._pending.clear()

    @property
    def connected(self) -> bool:
        return self._ws is not None

    async def handle_message(self, message: dict):
        req_id = message.get("id")
        fut = self._pending.get(req_id)
        if not fut or fut.done():
            return
        if "error" in message:
            fut.set_exception(RuntimeError(message["error"]))
        else:
            fut.set_result(message.get("result"))

    async def call(self, method: str, args: dict | None = None, timeout: float = 10.0):
        if not self._ws:
            raise RuntimeError("the document viewer is not currently open")
        req_id = uuid.uuid4().hex[:8]
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._pending[req_id] = fut
        try:
            await self._ws.send_json({"id": req_id, "method": method, "args": args or {}})
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending.pop(req_id, None)


display_link = DisplayLink()

# So the display (a separate process) can authenticate the local-only
# link without scraping server.log. Regenerated every server start; not a
# long-lived secret, so plaintext-on-disk is fine for a same-machine token.
_SESSION_TOKEN_PATH = Path(__file__).parent / ".session_token"
_SESSION_TOKEN_PATH.write_text(SESSION_TOKEN)

# Set root logger to WARNING — silences all third-party noise (httpx, httpcore, anthropic, faster_whisper, etc.)
logging.getLogger().setLevel(logging.WARNING)

# Our own logger for clio output — INFO level, goes to file + stdout
_LOG_PATH = Path(__file__).parent.parent / "server.log"
_log_handler = logging.handlers.RotatingFileHandler(
    _LOG_PATH, maxBytes=1_000_000, backupCount=1
)
_log_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
_clio_logger = logging.getLogger("clio")
_clio_logger.setLevel(logging.INFO)
_clio_logger.propagate = False
_clio_logger.addHandler(_log_handler)
_clio_logger.addHandler(logging.StreamHandler(sys.stdout))

# Route print() calls through the clio logger so they go to file + stdout
class _PrintToLog:
    def write(self, msg):
        if msg.strip():
            _clio_logger.info(msg.rstrip())
    def flush(self):
        pass

sys.stdout = _PrintToLog()
sys.stderr = _PrintToLog()

app = FastAPI()


@app.on_event("startup")
async def preflight_checks():
    errors = []

    if not os.environ.get("ANTHROPIC_API_KEY"):
        errors.append(
            "ANTHROPIC_API_KEY is not set — add it to config.sh and restart."
        )

    if TTS_ENGINE == "kokoro":
        for fname in ("kokoro-v1.0.onnx", "voices-v1.0.bin"):
            if not (MODELS_DIR / fname).exists():
                errors.append(
                    f"Kokoro model file missing: {MODELS_DIR / fname}\n"
                    "  Run ./install.sh to download it."
                )
    else:
        model_path = MODELS_DIR / f"{PIPER_MODEL_NAME}.onnx"
        if not model_path.exists():
            errors.append(
                f"Piper model not found: {model_path}\n"
                "  Run ./install.sh to download it."
            )

    if errors:
        for err in errors:
            print(f"[startup] ERROR: {err}")
        sys.exit(1)


PHONE_DIR = Path(__file__).parent.parent / "phone"
CLIO_DIR = Path(__file__).parent.parent
FIRST_RUN_FLAG = CLIO_DIR / ".first_run_complete"

INTRO_TEXT = (
    "Hi, I'm Clio. I talk with you by voice, and you can watch what I do on your screen as I work. "
    "I can read and write code, run commands, search the web, open documents for us to look at "
    "together, and help you think things through. "
    "Just press the button and tell me what you need. "
    "You can give me multi-step instructions, ask follow-up questions, or just think out loud — I'll keep up."
)

GREETING_TEXT = "Hey, I'm back. What are we working on?"

app.mount("/static", StaticFiles(directory=str(PHONE_DIR)), name="static")


@app.get("/")
async def serve_index():
    html = (PHONE_DIR / "index.html").read_text()
    script = f'<script>window.CLIO_TOKEN="{SESSION_TOKEN}";</script>'
    html = html.replace("</head>", f"{script}\n</head>", 1)
    return HTMLResponse(html)


@app.get("/apple-touch-icon.png")
@app.get("/apple-touch-icon-precomposed.png")
async def serve_touch_icon():
    return FileResponse(str(PHONE_DIR / "icon-192.png"), media_type="image/png")


@app.get("/diff")
async def serve_diff():
    html = (PHONE_DIR / "diff.html").read_text()
    script = f'<script>window.CLIO_TOKEN="{SESSION_TOKEN}";</script>'
    html = html.replace("</head>", f"{script}\n</head>", 1)
    return HTMLResponse(html)


@app.websocket("/ws/diff")
async def diff_websocket_endpoint(websocket: WebSocket):
    token = websocket.query_params.get("token", "")
    if token != SESSION_TOKEN:
        await websocket.close(code=4403)
        return
    await diff_manager.connect(websocket)
    try:
        # Keep connection alive; client sends nothing, just receives
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        diff_manager.disconnect(websocket)


@app.websocket("/ws/display")
async def display_websocket_endpoint(websocket: WebSocket):
    token = websocket.query_params.get("token", "")
    if token != SESSION_TOKEN:
        await websocket.close(code=4403)
        return
    await display_link.connect(websocket)
    try:
        async for message in websocket.iter_json():
            await display_link.handle_message(message)
    except WebSocketDisconnect:
        pass
    finally:
        display_link.disconnect(websocket)


@app.websocket("/ws/terminal")
async def terminal_websocket_endpoint(websocket: WebSocket):
    token = websocket.query_params.get("token", "")
    if token != SESSION_TOKEN:
        await websocket.close(code=4403)
        return
    await websocket.accept()
    await terminal_session.subscribe(websocket)
    try:
        async for message in websocket.iter_json():
            kind = message.get("type")
            if kind == "input":
                terminal_session.write_as_human(message.get("data", ""))
            elif kind == "interrupt":
                terminal_session.request_interrupt()
            elif kind == "resize":
                terminal_session.resize(message.get("cols", 80), message.get("rows", 24))
    except WebSocketDisconnect:
        pass
    finally:
        terminal_session.unsubscribe(websocket)


@app.get("/audio/{filename}")
async def serve_audio(filename: str):
    # Sanitize filename to prevent path traversal
    safe_name = Path(filename).name
    audio_path = TMP_DIR / safe_name
    if not audio_path.exists():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Audio not found")
    return FileResponse(str(audio_path), media_type="audio/wav")


async def _send_static_greeting(agent: AgentSession, text: str):
    wav_path = TMP_DIR / f"greeting_{uuid.uuid4().hex[:8]}.wav"
    await asyncio.to_thread(synthesize, text, str(wav_path))
    await agent.send({
        "type": "audio_chunk",
        "text": text,
        "audio_url": f"/audio/{wav_path.name}",
    })
    await agent.send({"type": "turn_end"})


async def _send_greeting(agent: AgentSession):
    """Speak the intro (first run), a context-aware resumption (restart with
    context available), or a short generic greeting (returning, no context)."""
    is_first_run = not FIRST_RUN_FLAG.exists()
    if is_first_run:
        await _send_static_greeting(agent, INTRO_TEXT)
        FIRST_RUN_FLAG.touch()
        return

    has_restart_context = (
        RESTART_CONTEXT_PATH.exists() and bool(RESTART_CONTEXT_PATH.read_text().strip())
    )
    if has_restart_context:
        try:
            await agent.generate_greeting()
            return
        except Exception as e:
            print(f"[greeting] ERROR generating restart greeting: {e!r}")
            # Fall through to the generic greeting below

    await _send_static_greeting(agent, GREETING_TEXT)


def _finalize_phone_session(entry: dict):
    agent: AgentSession = entry["agent"]
    voice_session: VoiceSession = entry["voice_session"]
    if entry["task"] and not entry["task"].done():
        entry["task"].cancel()
    agent.log_cost()
    voice_session.end()
    # If memory is disabled, delete the session log so nothing is consolidated later
    if not agent.memory_enabled:
        voice_session.log_path.unlink(missing_ok=True)


async def _expire_phone_session(sid: str):
    try:
        await asyncio.sleep(RESUME_WINDOW_SECONDS)
        print(f"[ws] session {sid} not resumed within {RESUME_WINDOW_SECONDS}s, ending it")
    except asyncio.CancelledError:
        # A reconnect cancels this after reclaiming the entry (it's still
        # attached, so leave it). Server shutdown also cancels it — then the
        # session is still parked and must be finalized so a memory-disabled
        # session's log doesn't survive the shutdown.
        entry = _phone_sessions.get(sid)
        if entry is None or entry["ws"] is not None:
            raise
        print(f"[ws] server shutting down, ending parked session {sid}")
    entry = _phone_sessions.pop(sid, None)
    if entry:
        _finalize_phone_session(entry)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    token = websocket.query_params.get("token", "")
    if token != SESSION_TOKEN:
        await websocket.close(code=4403)
        return

    await websocket.accept()
    sid = websocket.query_params.get("sid", "") or uuid.uuid4().hex
    try:
        last_seq = int(websocket.query_params.get("last_seq", "0"))
    except ValueError:
        last_seq = 0
    client = websocket.client
    print(f"[ws] connect sid={sid} from {client.host if client else '?'} "
          f"ua={websocket.headers.get('user-agent', '')!r}")
    entry = _phone_sessions.get(sid)
    resumed = entry is not None

    if resumed:
        if entry["expiry"]:
            entry["expiry"].cancel()
            entry["expiry"] = None
        stale_ws = entry["ws"]
        entry["ws"] = websocket
        agent: AgentSession = entry["agent"]
        voice_session: VoiceSession = entry["voice_session"]
        # The server may not have noticed the old socket died yet (half-open
        # TCP after a network blip) — this connection takes over regardless.
        if stale_ws is not None:
            print(f"[ws] session {sid} taken over by a new connection")
            try:
                await stale_ws.close(code=4000)
            except Exception:
                pass
        print(f"[ws] session {sid} resumed ({len(agent.conversation)} messages of history)")
        # Unnumbered, so the phone knows its seq counter is still valid before replay.
        # If this socket dies mid-replay, the receive loop below notices and parks.
        try:
            await websocket.send_json({"type": "hello", "resumed": True})
            await agent.attach(websocket, last_seq)
        except Exception as e:
            print(f"[ws] session {sid} replay failed: {e!r}")
    else:
        reset_last_transcript()  # clear cross-session priming so hallucinations get filtered
        voice_session = VoiceSession()
        agent = AgentSession(websocket, voice_session, diff_manager=diff_manager, display_link=display_link)
        # "task" is the in-flight turn. It belongs to the session, not the
        # connection, so a dropped socket doesn't kill it — the phone gets
        # whatever it missed replayed when it reconnects.
        entry = _phone_sessions[sid] = {
            "agent": agent, "voice_session": voice_session, "ws": websocket,
            "expiry": None, "task": None,
        }
        print(f"[ws] session {sid} started")
        try:
            await websocket.send_json({"type": "hello", "resumed": False})
        except Exception:
            pass

        # Ask the user whether to save this session to memory
        await agent.send({"type": "memory_prompt"})

        # Send intro (first run), context-aware resumption (restart), or short greeting (returning)
        asyncio.create_task(_send_greeting(agent))

    def turn_running() -> bool:
        return entry["task"] is not None and not entry["task"].done()

    async def deliver_completions():
        """Turn finished delegations into proactive turns, one at a time, only
        once Clio is idle. If the user starts talking mid-announcement, the
        turn is cancelled like any other and the notice is retried later."""
        while True:
            notice = await delegation_manager.completions.get()
            started = False
            try:
                while True:
                    while turn_running():
                        await asyncio.wait({entry["task"]})
                    await asyncio.sleep(PROACTIVE_IDLE_SECONDS)
                    if turn_running():
                        continue
                    task = entry["task"] = asyncio.create_task(agent.process_event(notice))
                    started = True
                    await asyncio.wait({task})
                    if not task.cancelled():
                        break
                    started = False
            except asyncio.CancelledError:
                # Once started, the turn outlives this connection and is replayed
                if not started:
                    delegation_manager.completions.put_nowait(notice)
                raise

    completions_task = asyncio.create_task(deliver_completions())

    try:
        # receive_json rather than iter_json: iter_json swallows the
        # disconnect, and its close code is exactly what we want to log.
        while True:
            message = await websocket.receive_json()
            msg_type = message.get("type")

            if msg_type == "audio":
                # The phone resends an utterance it never got an ack for; if the
                # first copy did arrive, just ack again rather than re-running it.
                audio_id = message.get("id")
                if audio_id and audio_id == agent.last_audio_id:
                    await agent.send({"type": "audio_ack", "id": audio_id})
                    continue
                agent.last_audio_id = audio_id
                await agent.send({"type": "audio_ack", "id": audio_id})
                # Cancel previous exchange if still running
                if turn_running():
                    entry["task"].cancel()
                audio_bytes = base64.b64decode(message["data"])
                entry["task"] = asyncio.create_task(agent.process_audio(audio_bytes))

            elif msg_type == "permission_response":
                tool_call_id = message.get("tool_call_id", "")
                approved = message.get("approved", False)
                await agent.handle_permission_response(tool_call_id, approved)

            elif msg_type == "memory_prompt_response":
                agent.memory_enabled = message.get("enabled", False)
                print(f"[agent] memory_enabled={agent.memory_enabled}")
                # Only consolidate past sessions if user opted into memory
                if agent.memory_enabled:
                    asyncio.create_task(consolidate_sessions(current_log_path=voice_session.log_path))

            elif msg_type == "set_model":
                model_id = message.get("model", "")
                if not agent.set_model(model_id):
                    print(f"[agent] ignoring unknown model: {model_id!r}")

            elif msg_type == "client_log":
                # The phone reports why its previous connection closed — its
                # console isn't visible from here, so this is the only record.
                print(f"[ws] client reports: {message.get('text', '')!r}")

    except WebSocketDisconnect as e:
        print(f"[ws] session {sid} disconnected: code={e.code} reason={e.reason!r}")
    except Exception as e:
        print(f"[ws] session {sid} connection error: {e!r}")
    finally:
        completions_task.cancel()
        agent.detach(websocket)
        # An in-flight turn keeps running; parking ends it if nobody returns.
        if _phone_sessions.get(sid) is entry and entry["ws"] is websocket:
            entry["ws"] = None
            entry["expiry"] = asyncio.create_task(_expire_phone_session(sid))
            print(f"[ws] session {sid} parked for {RESUME_WINDOW_SECONDS}s awaiting reconnect")
