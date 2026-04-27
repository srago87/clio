import asyncio
import base64
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .agent import AgentSession, TMP_DIR
from .session import VoiceSession

app = FastAPI()

PHONE_DIR = Path(__file__).parent.parent / "phone"
app.mount("/static", StaticFiles(directory=str(PHONE_DIR)), name="static")


@app.get("/")
async def serve_index():
    return FileResponse(str(PHONE_DIR / "index.html"))


@app.get("/apple-touch-icon.png")
@app.get("/apple-touch-icon-precomposed.png")
async def serve_touch_icon():
    return FileResponse(str(PHONE_DIR / "icon-192.png"), media_type="image/png")


@app.get("/audio/{filename}")
async def serve_audio(filename: str):
    # Sanitize filename to prevent path traversal
    safe_name = Path(filename).name
    audio_path = TMP_DIR / safe_name
    if not audio_path.exists():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Audio not found")
    return FileResponse(str(audio_path), media_type="audio/wav")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    voice_session = VoiceSession()
    agent = AgentSession(websocket, voice_session)
    processing_task: asyncio.Task | None = None

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

    except WebSocketDisconnect:
        pass
    finally:
        if processing_task and not processing_task.done():
            processing_task.cancel()
        voice_session.end()
