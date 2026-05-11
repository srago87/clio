import asyncio
import re
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import anthropic
from fastapi import WebSocket

from .stt import transcribe
from .tts import synthesize
from .session import VoiceSession
from .tools import (
    TOOL_DEFINITIONS,
    AUTO_APPROVE,
    MEMORY_PATH,
    execute_tool,
    describe_tool_call,
    summarize_tool_result,
)

MODEL = "claude-sonnet-4-6"
CLIO_DIR = Path(__file__).parent.parent
WORK_DIR = CLIO_DIR.parent
TMP_DIR = Path(__file__).parent / "tmp"
SOUL_PATH = CLIO_DIR / "soul.md"

# Split on sentence-ending punctuation followed by whitespace
SENTENCE_END = re.compile(r'(?<=[.!?])\s+')

BASE_SYSTEM_PROMPT = f"""You are Clio, a voice-controlled coding assistant. The user speaks to you \
from their phone and hears your responses read aloud, so:

- Use plain spoken language — no markdown, no bullet points, no code blocks
- Keep responses concise; aim for 1-3 sentences unless a longer explanation is needed
- When you take actions (editing files, running commands), briefly say what you did
- You work in the user's coding environment at {WORK_DIR}
- Your own source code lives at {CLIO_DIR}

You have tools to read files, list directories, search code, find files, write files, \
edit files, run shell commands, run background processes, delete files, update your memory, \
update your scratchpad, search the web, and get the current time.

Use get_current_time whenever: the user asks what time it is, asks about schedules \
or timing, or when an accurate answer depends on knowing the current time. \
Do not guess or use stale information — call the tool.

Use update_scratchpad to track your current task, files modified, and next steps \
within this session — rewrite it fully each time. It resets when the session ends.

Use update_memory to remember facts worth keeping across future sessions: \
user preferences, project decisions, and anything else worth recalling later.

When editing files: use write_file to replace an entire file, and edit_file only for \
small targeted changes (a few lines). Never try to edit_file with a large old_string — \
if the change touches more than ~20 lines, use write_file with the full new content instead.

Use search_code to find a function, class, or pattern across a codebase before editing. \
Use find_files to locate files by name or extension. \
Use read_file with start_line/end_line to read just the relevant section of a large file.

For long-running processes (dev servers, watchers, npm run dev, python -m http.server): \
use run_background instead of bash_command — it returns a job ID immediately. \
Use check_job to read output, stop_job to kill it. Never use bash_command for a process \
that runs indefinitely.

Before starting any coding task, classify it:

Short tasks — a targeted edit, quick bug fix, simple question, or anything completable \
in one or two steps. Just do it without preamble.

Long tasks — anything involving multiple files, a new project, a new feature, or \
significant refactoring. For these:
1. Ask clarifying questions one at a time — stack, scope, constraints, design preferences \
— until you have a complete picture. Do not assume. Let each answer inform the next question.
2. Once you have enough to proceed, write a complete plan to your scratchpad and state it \
to the user before touching any files.
3. Do not write or edit any code until the plan is confirmed."""


def build_stable_prompt() -> str:
    """Stable content: base instructions + soul + memory. Suitable for caching."""
    parts = [BASE_SYSTEM_PROMPT]
    soul = SOUL_PATH.read_text().strip() if SOUL_PATH.exists() else ""
    if soul:
        parts.append(f"## Your soul:\n{soul}")
    memory = MEMORY_PATH.read_text().strip() if MEMORY_PATH.exists() else ""
    if memory:
        parts.append(f"## Your memory from previous sessions:\n{memory}")
    return "\n\n".join(parts)


def build_volatile_prompt(scratchpad: str = "") -> str:
    """Volatile content: current date + scratchpad. Not cached."""
    parts = [f"## Current date:\n{datetime.now().strftime('%A, %B %d, %Y')}"]
    if scratchpad:
        parts.append(f"## Your scratchpad for this session:\n{scratchpad}")
    return "\n\n".join(parts)


def _extract_sentences(buffer: str) -> tuple[list[str], str]:
    """Split completed sentences out of buffer, return (sentences, remainder)."""
    parts = SENTENCE_END.split(buffer)
    if len(parts) <= 1:
        return [], buffer
    return [p.strip() for p in parts[:-1] if p.strip()], parts[-1]


def _cleanup_old_audio():
    """Delete response WAV files older than 5 minutes."""
    cutoff = time.time() - 300
    for f in TMP_DIR.glob("response_*.wav"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
        except OSError:
            pass


class AgentSession:
    def __init__(self, websocket: WebSocket, voice_session: VoiceSession):
        self.websocket = websocket
        self.voice_session = voice_session
        self.conversation: list = []
        self.scratchpad: str = ""
        self._pending_permission: Optional[asyncio.Future] = None
        self._pending_tool_id: Optional[str] = None
        TMP_DIR.mkdir(exist_ok=True)

    # ── outbound helpers ──────────────────────────────────────────────────

    async def _send(self, payload: dict):
        await self.websocket.send_json(payload)

    async def _status(self, state: str, label: str = ""):
        payload = {"type": "status", "state": state}
        if label:
            payload["label"] = label
        await self._send(payload)

    # ── permission relay ──────────────────────────────────────────────────

    async def handle_permission_response(self, tool_call_id: str, approved: bool):
        if self._pending_permission and self._pending_tool_id == tool_call_id:
            self._pending_permission.set_result(approved)

    async def _ask_permission(self, tool_call_id: str, tool: str, description: str) -> bool:
        loop = asyncio.get_event_loop()
        self._pending_permission = loop.create_future()
        self._pending_tool_id = tool_call_id

        await self._send({
            "type": "permission_request",
            "tool_call_id": tool_call_id,
            "tool": tool,
            "description": description,
        })

        try:
            return await asyncio.wait_for(self._pending_permission, timeout=60.0)
        except asyncio.TimeoutError:
            return False
        finally:
            self._pending_permission = None
            self._pending_tool_id = None

    # ── main entry point ──────────────────────────────────────────────────

    async def process_audio(self, audio_bytes: bytes):
        """Full pipeline: audio → STT → Claude streaming agent loop → TTS chunks → phone."""
        client = anthropic.AsyncAnthropic()

        # Clean up stale audio files from previous turns
        await asyncio.to_thread(_cleanup_old_audio)

        try:
            # 1. Transcribe
            await self._status("transcribing", "Transcribing…")
            print(f"[agent] transcribing {len(audio_bytes)} bytes")
            transcript = await asyncio.to_thread(self._transcribe, audio_bytes)
            print(f"[agent] transcript: {repr(transcript)}")
            if not transcript:
                await self._send({"type": "turn_end"})
                return

            await self._send({"type": "user_transcript", "text": transcript})

            # 2. Streaming agent loop — sends audio_chunk messages as sentences arrive
            await self._status("thinking", "Thinking…")
            self.conversation.append({"role": "user", "content": transcript})
            print(f"[agent] streaming Claude API")
            response_text = await self._stream_agent_loop(client)
            print(f"[agent] turn complete ({len(response_text)} chars)")

            # 3. Log + signal end of turn so phone re-enables mic
            self.voice_session.add_exchange(transcript, response_text)
            await self._send({"type": "turn_end"})

        except Exception as e:
            print(f"[agent] ERROR: {e!r}")
            await self._send({"type": "error", "message": str(e)})

    # ── streaming agent loop ──────────────────────────────────────────────

    async def _stream_agent_loop(self, client: anthropic.AsyncAnthropic) -> str:
        full_text = ""

        while True:
            text_buffer = ""
            turn_text = ""
            sentence_queue: asyncio.Queue[Optional[str]] = asyncio.Queue()

            stream_start = time.time()
            first_sentence_logged = False

            async def synthesis_worker():
                """Drain sentence queue concurrently with token streaming."""
                nonlocal first_sentence_logged
                while True:
                    sentence = await sentence_queue.get()
                    if sentence is None:
                        break
                    if not first_sentence_logged:
                        first_sentence_logged = True
                        print(f"[agent] time to first sentence: {(time.time()-stream_start)*1000:.0f}ms")
                    await self._send_audio_chunk(sentence)

            worker = asyncio.create_task(synthesis_worker())

            async with client.messages.stream(
                model=MODEL,
                max_tokens=8192,
                system=[
                    {
                        "type": "text",
                        "text": build_stable_prompt(),
                        "cache_control": {"type": "ephemeral"},
                    },
                    {
                        "type": "text",
                        "text": build_volatile_prompt(self.scratchpad),
                    },
                ],
                tools=TOOL_DEFINITIONS,
                messages=self.conversation,
            ) as stream:
                async for text in stream.text_stream:
                    text_buffer += text
                    turn_text += text

                    sentences, text_buffer = _extract_sentences(text_buffer)
                    for sentence in sentences:
                        if sentence:
                            await sentence_queue.put(sentence)

                # Flush any trailing text that didn't end with sentence punctuation
                remainder = text_buffer.strip()
                if remainder:
                    await sentence_queue.put(remainder)

                await sentence_queue.put(None)  # signal worker to stop
                await worker                    # wait for all synthesis to finish

                response = await stream.get_final_message()

            full_text += turn_text

            self.conversation.append({
                "role": "assistant",
                "content": response.content,
            })

            if response.stop_reason == "end_turn":
                break

            if response.stop_reason == "tool_use":
                await self._send({"type": "close_bubble"})
                tool_results = await self._handle_tool_calls(response.content)
                self.conversation.append({
                    "role": "user",
                    "content": tool_results,
                })
                await self._status("thinking", "Thinking…")
            else:
                break

        return full_text

    async def _send_audio_chunk(self, text: str):
        """Synthesize one sentence and push it to the phone immediately."""
        wav_path = await asyncio.to_thread(self._synthesize, text)
        print(f"[agent] chunk: {wav_path.name} — {repr(text[:60])}")
        await self._send({
            "type": "audio_chunk",
            "text": text,
            "audio_url": f"/audio/{wav_path.name}",
        })

    # ── tool execution ────────────────────────────────────────────────────

    async def _handle_tool_calls(self, content) -> list:
        """Execute all tool_use blocks, requesting permission as needed."""
        tool_results = []

        for block in content:
            if block.type != "tool_use":
                continue

            # close_connection — drop the WebSocket so the client reconnects
            if block.name == "close_connection":
                await self._send({
                    "type": "tool_result",
                    "tool": block.name,
                    "summary": "Connection closed",
                })
                await self.websocket.close()
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": "Connection closed.",
                })
                return tool_results

            # Scratchpad is session state — handle here rather than in execute_tool
            if block.name == "update_scratchpad":
                self.scratchpad = block.input.get("content", "")
                await self._send({
                    "type": "tool_result",
                    "tool": block.name,
                    "summary": "Scratchpad updated",
                })
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": "Scratchpad updated.",
                })
                continue

            description = describe_tool_call(block.name, block.input)

            if block.name not in AUTO_APPROVE:
                approved = await self._ask_permission(block.id, block.name, description)

                if not approved:
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": "The user denied this action.",
                        "is_error": True,
                    })
                    continue

            await self._status("executing", description)
            result = await asyncio.to_thread(execute_tool, block.name, block.input)

            await self._send({
                "type": "tool_result",
                "tool": block.name,
                "summary": summarize_tool_result(block.name, result),
            })

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result,
            })

        return tool_results

    # ── sync helpers (run in thread) ──────────────────────────────────────

    def _transcribe(self, audio_bytes: bytes) -> str:
        with tempfile.NamedTemporaryFile(
            suffix=".webm", dir=TMP_DIR, delete=False
        ) as f:
            f.write(audio_bytes)
            tmp_path = f.name
        try:
            return transcribe(tmp_path)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def _synthesize(self, text: str) -> Path:
        wav_path = TMP_DIR / f"response_{uuid.uuid4().hex[:8]}.wav"
        synthesize(text, str(wav_path))
        return wav_path
