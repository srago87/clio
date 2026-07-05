import asyncio
import os
import re
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import anthropic
from fastapi import WebSocket

from .cost import SessionCostTracker, log_usage
from .stt import transcribe
from .tts import synthesize
from .session import VoiceSession, LOGS_DIR
from .tools import (
    TOOL_DEFINITIONS,
    AUTO_APPROVE,
    MEMORY_PATH,
    execute_tool,
    describe_tool_call,
    summarize_tool_result,
)

DEFAULT_MODEL = "claude-sonnet-4-6"
MEMORY_MODEL  = "claude-haiku-4-5-20251001"
AVAILABLE_MODELS = [
    ("claude-haiku-4-5-20251001", "Haiku"),
    ("claude-sonnet-4-6",         "Sonnet"),
    ("claude-opus-4-8",           "Opus"),
]

USER_NAME = os.environ.get("USER_NAME", "").strip()
CLIO_DIR = Path(__file__).parent.parent
WORK_DIR = CLIO_DIR.parent
TMP_DIR = Path(__file__).parent / "tmp"
SOUL_PATH = CLIO_DIR / "soul.md"

# Split on sentence-ending punctuation followed by whitespace
SENTENCE_END = re.compile(r'(?<=[.!?])\s+')

_user_name_line = f"The user's name is {USER_NAME}. Address them by name naturally in conversation." if USER_NAME else ""

BASE_SYSTEM_PROMPT = f"""You are Clio, a voice-controlled coding assistant. The user speaks to you \
from their phone and hears your responses read aloud.{(chr(10) + _user_name_line) if _user_name_line else ""} \
Use plain spoken language — no markdown, no bullet points, no code blocks. Keep responses \
concise; aim for 1-3 sentences unless a longer explanation is needed. You work in the \
user's coding environment at {WORK_DIR}. Your own source code lives at {CLIO_DIR}.

When using tools, say nothing until the task is fully complete. No announcements, no \
narration, no intermediate summaries. One sentence when done: what changed or what you found.

## Tool usage

Use get_current_time whenever the answer depends on knowing the current time. Never guess.

Use update_scratchpad to track your current task, files modified, and next steps — rewrite \
it fully each time. After every write_file, edit_file, or bash_command that changes system \
state, log what ran and what changed before continuing to the next step. Before touching a \
file you've modified earlier this session, re-read your scratchpad first — it's your ground \
truth for what's already been done.

Use update_memory for facts worth keeping across future sessions: user preferences, project \
decisions, anything worth recalling later.

When editing files: use write_file to replace an entire file, edit_file only for small \
targeted changes (a few lines). If the change touches more than ~20 lines, use write_file \
with the full new content.

For long-running processes (dev servers, watchers, build processes): use run_background — \
it returns a job ID immediately. Use check_job to read output, stop_job to kill it. Never \
use bash_command for a process that runs indefinitely.

Use search_code to find a function, class, or pattern before editing. Use find_files to \
locate files by name or extension. Use read_file with start_line/end_line to read just the \
relevant section of a large file.

## Before reading or editing

Before making any claim about what a file contains, what a function does, or whether a \
feature exists — read the relevant files first. Never answer from memory alone when the \
code is accessible.

Before editing any file, read the relevant section to confirm what's already there. Never \
add content without verifying it doesn't already exist. After any change that could affect \
runtime behavior — config files, environment variables, installed packages — confirm the \
change reaches the running process before declaring success.

## Before rebooting the server

Before calling restart_server, update memory.md with what was just worked on — the task, \
relevant context, and what comes next. The scratchpad doesn't survive reboots; memory does.

## Task classification

Short tasks — a targeted edit, quick bug fix, simple question, or anything completable in \
one or two steps. Just do it.

Long tasks — multiple files, a new feature, significant refactoring. For these:
1. Ask clarifying questions one at a time until you have a complete picture. Don't assume. \
Let each answer inform the next question.
2. Write a plan to your scratchpad and state it clearly before touching any files.
3. End with an explicit question — "Ready to start?" — and stop. Wait for the user to confirm.
4. Only after confirmation do you begin execution.

## After completing a task

Give a brief spoken summary in past tense — "I added", "I updated", "I changed" — covering \
which files changed and what the change does. If a server restart is required, say so. \
If not, don't mention it. Never use future tense in a completion summary."""


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


MEMORY_EXTRACTION_PROMPT = """\
You are a memory extraction assistant. Given a conversation exchange, extract any facts \
worth remembering for future sessions — decisions made, preferences expressed, project \
context, bugs fixed, features built, or anything the user explicitly wants remembered.

If nothing noteworthy happened (e.g. it was just small talk or a trivial question), \
output exactly: NO_UPDATE

Otherwise, output a brief, plain-English summary of what's worth keeping — 1-4 sentences. \
Do NOT output the full memory file — just the new facts from this exchange."""

MEMORY_SIZE_LIMIT = 10 * 1024  # 10KB

SUMMARIZATION_THRESHOLD = 150_000
RECENT_EXCHANGES_TO_KEEP = 10      # number of recent user/assistant pairs to leave untouched


async def consolidate_sessions(current_log_path=None):
    """
    Background task: find all unconsolidated session logs, summarize them into memory.md,
    mark each as consolidated, then compress memory if it exceeds MEMORY_SIZE_LIMIT.
    Also compresses memory unconditionally at session start if it exceeds the limit.
    """
    from .session import VoiceSession

    client = anthropic.AsyncAnthropic()

    # Always check memory size at session start, regardless of whether there are logs to consolidate
    if MEMORY_PATH.exists():
        current_size = len(MEMORY_PATH.read_bytes())
        if current_size > MEMORY_SIZE_LIMIT:
            print(f"[memory] memory exceeds limit on connect ({current_size} bytes), compressing")
            await compress_memory(client)

    logs = VoiceSession.get_unconsolidated_logs(exclude_path=current_log_path)
    if not logs:
        return

    print(f"[memory] consolidating {len(logs)} session log(s)")
    combined = []
    for log in logs:
        try:
            text = log.read_text().strip()
            if text:
                combined.append(f"=== {log.name} ===\n{text}")
        except OSError:
            pass

    if not combined:
        for log in logs:
            VoiceSession.mark_consolidated(log)
        return

    all_logs_text = "\n\n".join(combined)

    try:
        response = await client.messages.create(
            model=MEMORY_MODEL,
            max_tokens=1024,
            system=(
                "You are a memory extraction assistant. Given one or more voice session logs, "
                "extract facts worth remembering for future sessions — decisions made, preferences "
                "expressed, project context, bugs fixed, features built, or anything the user "
                "explicitly wanted remembered. If nothing noteworthy happened across all sessions, "
                "output exactly: NO_UPDATE\n\n"
                "Otherwise output a concise plain-English summary of what's worth keeping."
            ),
            messages=[{"role": "user", "content": all_logs_text}],
        )
        log_usage("consolidation-extract", response.usage, MEMORY_MODEL)
        extracted = response.content[0].text.strip()

        if extracted != "NO_UPDATE":
            current_memory = MEMORY_PATH.read_text().strip() if MEMORY_PATH.exists() else ""
            merge_response = await client.messages.create(
                model=MEMORY_MODEL,
                max_tokens=4096,
                system=(
                    "You are a memory management assistant. Merge new facts into the existing "
                    "memory file naturally — update existing sections where relevant, add new facts "
                    "where appropriate. Return the complete updated memory file. Preserve all "
                    "existing content unless a new fact directly supersedes it."
                ),
                messages=[{
                    "role": "user",
                    "content": f"Current memory:\n{current_memory}\n\nNew facts:\n{extracted}",
                }],
            )
            log_usage("consolidation-merge", merge_response.usage, MEMORY_MODEL)
            updated_memory = merge_response.content[0].text.strip()
            MEMORY_PATH.write_text(updated_memory + "\n")
            print(f"[memory] memory updated ({len(updated_memory)} bytes)")

            # Compress if over size limit
            if len(updated_memory.encode()) > MEMORY_SIZE_LIMIT:
                await compress_memory(client, updated_memory)

    except Exception as e:
        print(f"[memory] consolidation error: {e!r}")
        return

    # Mark all logs as consolidated regardless of whether there was anything to save
    for log in logs:
        VoiceSession.mark_consolidated(log)
    print(f"[memory] marked {len(logs)} log(s) as consolidated")


async def compress_memory(client: anthropic.AsyncAnthropic, current_text: str = None, cost: "SessionCostTracker | None" = None):
    """Summarize memory.md down when it exceeds the size limit."""
    try:
        if current_text is None:
            current_text = MEMORY_PATH.read_text().strip() if MEMORY_PATH.exists() else ""
        if not current_text:
            return

        print(f"[memory] compressing memory ({len(current_text.encode())} bytes)")
        response = await client.messages.create(
            model=MEMORY_MODEL,
            max_tokens=2048,
            system=(
                "You are a memory compression assistant. The memory file has grown too large. "
                "Summarize and compress it — keep the most important facts, preferences, and "
                "decisions, but eliminate redundancy and verbose phrasing. Aim for under 6KB. "
                "Return only the compressed memory content."
            ),
            messages=[{"role": "user", "content": current_text}],
        )
        if cost is not None:
            cost.record("mem-compress", response.usage, MEMORY_MODEL)
        else:
            log_usage("mem-compress", response.usage, MEMORY_MODEL)
        compressed = response.content[0].text.strip()
        MEMORY_PATH.write_text(compressed + "\n")
        print(f"[memory] compressed to {len(compressed.encode())} bytes")
    except Exception as e:
        print(f"[memory] compression error: {e!r}")


class AgentSession:
    def __init__(self, websocket: WebSocket, voice_session: VoiceSession, diff_manager=None):
        self.websocket = websocket
        self.voice_session = voice_session
        self.diff_manager = diff_manager
        self.conversation: list = []
        self.scratchpad: str = ""
        self.memory_enabled: bool = False
        self.model: str = DEFAULT_MODEL
        self._pending_permission: Optional[asyncio.Future] = None
        self._pending_tool_id: Optional[str] = None
        self.cost = SessionCostTracker()
        self._tokens_at_last_summarization: int = 0
        TMP_DIR.mkdir(exist_ok=True)

    def set_model(self, model_id: str) -> bool:
        valid = {m for m, _ in AVAILABLE_MODELS}
        if model_id in valid:
            self.model = model_id
            print(f"[agent] model set to {model_id}")
            return True
        return False

    def log_cost(self) -> None:
        print(self.cost.report())

    # ── outbound helpers ──────────────────────────────────────────────────

    async def _send(self, payload: dict):
        await self.websocket.send_json(payload)

    async def _status(self, state: str, label: str = ""):
        payload = {"type": "status", "state": state}
        if label:
            payload["label"] = label
        await self._send(payload)

    async def _send_cost_update(self):
        try:
            await self._send({"type": "cost_update", "session_usd": self.cost.total_usd})
        except Exception:
            pass

    # ── permission relay ──────────────────────────────────────────────────

    async def handle_permission_response(self, tool_call_id: str, approved: bool):
        if self._pending_permission and self._pending_tool_id == tool_call_id:
            self._pending_permission.set_result(approved)

    async def _ask_permission(self, tool_call_id: str, tool: str, description: str) -> bool:
        loop = asyncio.get_running_loop()
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

        # Snapshot conversation length so we can roll back if this turn is cancelled
        # mid-execution (e.g. user speaks while tools are running). Without this,
        # a cancelled turn can leave an assistant tool_use block with no matching
        # tool_result, which causes an API error on the next call.
        checkpoint = len(self.conversation)

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
            print(f"[agent] streaming Claude API ({self.model})")
            response_text = await self._stream_agent_loop(client)
            print(f"[agent] turn complete ({len(response_text)} chars)")

            # 3. Log + signal end of turn so phone re-enables mic
            if self.memory_enabled:
                self.voice_session.add_exchange(transcript, response_text)
            await self._send({"type": "turn_end"})

            # 4. Compress conversation history if token count is approaching the limit
            await self._maybe_summarize_conversation(client)

            # 5. Async memory extraction (non-blocking, best-effort)
            if self.memory_enabled:
                asyncio.create_task(
                    self._extract_and_save_memory(client, transcript, response_text)
                )

        except asyncio.CancelledError:
            del self.conversation[checkpoint:]
            raise

        except Exception as e:
            print(f"[agent] ERROR: {e!r}")
            await self._send({"type": "error", "message": str(e)})

    # ── memory extraction ─────────────────────────────────────────────────

    async def _extract_and_save_memory(
        self,
        client: anthropic.AsyncAnthropic,
        user_text: str,
        assistant_text: str,
    ):
        """Non-blocking: extract noteworthy facts from this exchange and merge into memory."""
        try:
            exchange_summary = f"User: {user_text}\n\nAssistant: {assistant_text}"
            response = await client.messages.create(
                model=MEMORY_MODEL,
                max_tokens=512,
                system=MEMORY_EXTRACTION_PROMPT,
                messages=[{"role": "user", "content": exchange_summary}],
            )
            self.cost.record("mem-extract", response.usage, MEMORY_MODEL)
            await self._send_cost_update()
            extracted = response.content[0].text.strip()
            if extracted == "NO_UPDATE":
                return

            # Merge extracted facts into the existing memory file
            current_memory = MEMORY_PATH.read_text().strip() if MEMORY_PATH.exists() else ""
            merge_response = await client.messages.create(
                model=MEMORY_MODEL,
                max_tokens=4096,
                system=(
                    "You are a memory management assistant. You will be given the current memory file "
                    "and new facts extracted from a recent conversation. Merge the new facts into the "
                    "memory file naturally — update existing sections where relevant, add new facts where "
                    "appropriate. Return the complete updated memory file. Preserve all existing content "
                    "unless a new fact directly supersedes it."
                ),
                messages=[{
                    "role": "user",
                    "content": f"Current memory:\n{current_memory}\n\nNew facts:\n{extracted}",
                }],
            )
            self.cost.record("mem-merge", merge_response.usage, MEMORY_MODEL)
            await self._send_cost_update()
            updated_memory = merge_response.content[0].text.strip()
            MEMORY_PATH.write_text(updated_memory + "\n")
            print(f"[agent] memory updated ({len(updated_memory)} bytes)")

            # Compress if over size limit
            if len(updated_memory.encode()) > MEMORY_SIZE_LIMIT:
                await compress_memory(client, updated_memory, self.cost)
                await self._send_cost_update()

        except Exception as e:
            print(f"[agent] memory extraction error: {e!r}")

    # ── conversation summarization ────────────────────────────────────────

    async def _maybe_summarize_conversation(self, client: anthropic.AsyncAnthropic):
        """Compress older conversation history when token count crosses the threshold."""
        if self.cost.total_input_tokens - self._tokens_at_last_summarization < SUMMARIZATION_THRESHOLD:
            return

        # Need at least enough messages to have something to summarize
        messages_to_keep = RECENT_EXCHANGES_TO_KEEP * 2  # user + assistant per exchange
        if len(self.conversation) <= messages_to_keep:
            return

        older = self.conversation[:-messages_to_keep]
        recent = self.conversation[-messages_to_keep:]

        # Only summarize if there's meaningful content to compress
        if not older:
            return

        # Ensure the split doesn't fall between an assistant tool_use and its tool_result.
        # If recent starts with a user message that has tool_result blocks, the corresponding
        # tool_use is in `older` and would be compressed away. The API merges consecutive
        # user messages (summary_block + recent[0]), making the tool_result appear orphaned
        # in messages[0].content[1] → 400 BadRequestError. Fix by pulling the preceding
        # assistant message into recent so the tool_use/tool_result pair stays together.
        while recent and older:
            first = recent[0]
            content = first.get("content", "")
            is_tool_result_message = (
                first.get("role") == "user"
                and isinstance(content, list)
                and any(
                    (isinstance(b, dict) and b.get("type") == "tool_result")
                    or (hasattr(b, "type") and b.type == "tool_result")
                    for b in content
                )
            )
            if not is_tool_result_message:
                break
            recent = [older[-1]] + recent
            older = older[:-1]

        if not older:
            return

        print(f"[agent] summarizing {len(older)} messages (token count: {self.cost.total_input_tokens})")

        # Build a text representation of the older exchanges for summarization
        history_text = []
        for msg in older:
            role = msg["role"].capitalize()
            content = msg["content"]
            if isinstance(content, str):
                history_text.append(f"{role}: {content}")
            elif isinstance(content, list):
                # Extract text from content blocks
                parts = [b.text if hasattr(b, "text") else str(b) for b in content if hasattr(b, "text") or isinstance(b, str)]
                if parts:
                    history_text.append(f"{role}: {' '.join(parts)}")

        try:
            response = await client.messages.create(
                model=MEMORY_MODEL,
                max_tokens=1024,
                system=(
                    "You are a conversation summarizer. Given a portion of a conversation between "
                    "a user and a voice coding assistant, produce a concise summary that captures "
                    "the key tasks attempted, decisions made, files modified, and outcomes. "
                    "Write in past tense. Be specific about file names and technical details. "
                    "This summary will replace the original exchanges in the conversation history."
                ),
                messages=[{"role": "user", "content": "\n\n".join(history_text)}],
            )
            self.cost.record("summarize", response.usage, MEMORY_MODEL)
            await self._send_cost_update()
            summary = response.content[0].text.strip()

            # Replace older messages with a single summary block
            summary_block = {
                "role": "user",
                "content": f"[Earlier conversation summary]\n{summary}",
            }
            self.conversation = [summary_block] + recent
            self._tokens_at_last_summarization = self.cost.total_input_tokens

            # Notify the phone UI so the user can see compression happened
            await self._send({
                "type": "tool_result",
                "tool": "summarize_conversation",
                "summary": f"Compressed {len(older)} older messages into a summary block",
            })

            print(f"[agent] conversation compressed: {len(older)} messages → 1 summary block")

        except Exception as e:
            print(f"[agent] summarization error: {e!r}")

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
                model=self.model,
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
                self.cost.record("turn", response.usage, self.model)
                await self._send_cost_update()

            full_text += turn_text

            self.conversation.append({
                "role": "assistant",
                "content": response.content,
            })

            if response.stop_reason == "end_turn":
                break

            if response.stop_reason == "tool_use":
                await self._send({"type": "close_bubble"})
                tool_results = await self._handle_tool_calls(response.content, client)
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

    async def _handle_tool_calls(self, content, client: anthropic.AsyncAnthropic) -> list:
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

            await self._status("working", description)
            result = await asyncio.to_thread(execute_tool, block.name, block.input)

            # Compress memory if an explicit update_memory call pushed it over the limit
            if block.name == "update_memory":
                updated = MEMORY_PATH.read_text() if MEMORY_PATH.exists() else ""
                if len(updated.encode()) > MEMORY_SIZE_LIMIT:
                    await compress_memory(client, updated, self.cost)
                    await self._send_cost_update()

            await self._send({
                "type": "tool_result",
                "tool": block.name,
                "summary": summarize_tool_result(block.name, result),
            })

            # File diff result — broadcast to diff viewers and extract text for Claude
            if isinstance(result, dict) and "_diff" in result:
                if result["_diff"] and self.diff_manager:
                    asyncio.create_task(self.diff_manager.broadcast({
                        "type": "file_diff",
                        "path": result["path"],
                        "old": result["old"],
                        "new": result["new"],
                    }))
                tool_result_content = result["text"]
            # browser_screenshot returns a dict (image content block) — wrap it
            elif isinstance(result, dict):
                tool_result_content = [result]
            else:
                tool_result_content = result

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": tool_result_content,
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
