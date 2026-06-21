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
from .session import VoiceSession
from .stt import reset_last_transcript
from .tts import synthesize, TTS_ENGINE, MODELS_DIR, PIPER_MODEL_NAME

SESSION_TOKEN = secrets.token_hex(16)


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
    "Hi, I'm Clio — a voice-controlled coding assistant that lives in your development environment. "
    "I can read and write code, run commands, search the web, and help you build things. "
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


@app.get("/audio/{filename}")
async def serve_audio(filename: str):
    # Sanitize filename to prevent path traversal
    safe_name = Path(filename).name
    audio_path = TMP_DIR / safe_name
    if not audio_path.exists():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Audio not found")
    return FileResponse(str(audio_path), media_type="audio/wav")


async def _send_greeting(websocket: WebSocket):
    """Synthesize and stream the intro or greeting audio on first connect."""
    is_first_run = not FIRST_RUN_FLAG.exists()
    text = INTRO_TEXT if is_first_run else GREETING_TEXT
    wav_path = TMP_DIR / f"greeting_{uuid.uuid4().hex[:8]}.wav"
    await asyncio.to_thread(synthesize, text, str(wav_path))
    await websocket.send_json({
        "type": "audio_chunk",
        "text": text,
        "audio_url": f"/audio/{wav_path.name}",
    })
    await websocket.send_json({"type": "turn_end"})
    if is_first_run:
        FIRST_RUN_FLAG.touch()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    token = websocket.query_params.get("token", "")
    if token != SESSION_TOKEN:
        await websocket.close(code=4403)
        return

    await websocket.accept()
    reset_last_transcript()  # clear cross-session priming so hallucinations get filtered
    voice_session = VoiceSession()
    agent = AgentSession(websocket, voice_session, diff_manager=diff_manager)
    processing_task: asyncio.Task | None = None

    # Ask the user whether to save this session to memory
    await websocket.send_json({"type": "memory_prompt"})

    # Send intro (first run) or short greeting (returning)
    asyncio.create_task(_send_greeting(websocket))

    try:
        async for message in websocket.iter_json():
            msg_type = message.get("type")

            if msg_type == "audio":
                # Cancel previous exchange if still running
                if processing_task and not processing_task.done():
                    processing_task.cancel()
                audio_bytes = base64.b64decode(message["data"])
                processing_task = asyncio.create_task(
                    agent.process_audio(audio_bytes)
                )

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

    except WebSocketDisconnect:
        pass
    finally:
        if processing_task and not processing_task.done():
            processing_task.cancel()
        agent.log_cost()
        voice_session.end()
        # If memory is disabled, delete the session log so nothing is consolidated later
        if not agent.memory_enabled:
            voice_session.log_path.unlink(missing_ok=True)
