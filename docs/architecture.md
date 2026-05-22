# Clio Architecture

Technical reference for the Clio codebase.

---

## Overview

```
Phone (PWA)
    ↕ WebSocket (wss://)
FastAPI Backend (laptop)
    ├── faster-whisper  (speech-to-text)
    ├── Anthropic API   (Claude Sonnet agent loop)
    ├── piper-tts       (text-to-speech, streamed sentence by sentence)
    └── VoiceSession    (markdown log per connection)
```

---

## Request lifecycle

1. Phone records audio via `MediaRecorder` until silence is detected
2. Audio blob (WebM) is base64-encoded and sent over WebSocket as `{"type": "audio", "data": "..."}`
3. Backend writes audio to a temp file, transcribes with faster-whisper
4. Transcript is appended to conversation history
5. Backend opens a streaming request to the Claude API
6. Text tokens accumulate in a buffer; completed sentences are extracted with a regex
7. Each sentence is synthesized by piper-tts and sent to the phone as `{"type": "audio_chunk", "text": "...", "audio_url": "/audio/response_xxx.wav"}`
8. Phone plays chunks back-to-back via Web Audio API
9. On `tool_use` stop reason: stream pauses, tools execute, results are appended, loop continues
10. On `end_turn`: backend sends `{"type": "turn_end"}`, phone re-enables the mic

---

## WebSocket protocol

All messages are JSON.

### Phone → Backend

| type | fields | description |
|------|--------|-------------|
| `audio` | `data: base64` | WebM audio clip |
| `permission_response` | `tool_call_id`, `approved: bool` | Tool approval decision |

### Backend → Phone

| type | fields | description |
|------|--------|-------------|
| `status` | `state: string`, `label?: string` | Current state with optional description |
| `user_transcript` | `text` | Transcribed speech (shown immediately) |
| `audio_chunk` | `text`, `audio_url` | One synthesized sentence + WAV URL |
| `turn_end` | — | All chunks sent; mic re-enables after audio queue drains |
| `tool_result` | `tool`, `summary` | Brief display after each tool call |
| `permission_request` | `tool_call_id`, `tool`, `description` | Approval needed before executing |
| `close_bubble` | — | Signals end of assistant text before tool execution |
| `error` | `message` | Error description |

### Status states

| state | meaning |
|-------|---------|
| `transcribing` | Converting audio to text |
| `thinking` | Waiting for Claude API response |
| `executing` | Running a tool |
| `waiting_permission` | Waiting for phone approval |

Note: there is no `speaking` state sent to the phone — the user can already hear it. The status badge hides when idle and while muted.

---

## Agent loop (`laptop/agent.py`)

`AgentSession` is created per WebSocket connection and lives until the connection closes. It holds:
- `conversation` — the full message history for this session
- `scratchpad` — a string Clio updates via `update_scratchpad`, injected into the system prompt
- `_pending_permission` — an asyncio `Future` for the current permission request, if any

The agent loop in `_stream_agent_loop`:
1. Opens a streaming request to the Claude API
2. Runs a `synthesis_worker` task concurrently that drains a sentence queue and synthesizes audio
3. Feeds text tokens into the sentence queue as they stream
4. After the stream ends, calls `get_final_message()` to get the stop reason and content blocks
5. If `stop_reason == "tool_use"`: executes tools, appends results, loops
6. If `stop_reason == "end_turn"`: breaks

### Prompt caching

The system prompt is split into two blocks to maximize cache hits:

- **Stable block** (with `cache_control: ephemeral`): base instructions + soul.md + memory.md. Cached after the first call per session. Only busted when memory is updated.
- **Volatile block** (no cache): current date + scratchpad. Changes frequently, so not worth caching.

---

## Tools (`laptop/tools.py`)

Tools fall into two categories:

- **Auto-approved** — execute immediately without user interaction
- **Require approval** — send a `permission_request` to the phone; wait up to 60s for a response

### Permission flow

1. Agent calls `_ask_permission(tool_call_id, tool, description)`
2. Backend sends `permission_request` over WebSocket
3. Phone shows overlay card, speaks the request via `SpeechSynthesis`
4. User taps Approve or Deny (Approve is listed first, on the right)
5. Phone sends `permission_response`
6. Agent resumes with result

### edit_file fuzzy matching

`edit_file` tries two strategies:
1. Exact string match — fast path
2. Whitespace-normalized match — strips trailing whitespace from each line before comparing. Handles the common case where the model generates `old_string` with slightly different trailing spaces than the file on disk.

If both fail, returns an error indicating whether the first line was found (context mismatch) or not found at all.

---

## Background jobs (`laptop/jobs.py`)

`BackgroundJobManager` is a module-level singleton. Each job is a `subprocess.Popen` with stdout/stderr piped to a temp log file in `laptop/tmp/`.

- Jobs survive phone disconnects (WebSocket reconnects create a new `AgentSession` but the job manager persists at process level)
- `check_job` tails the log file — no blocking, no pipes to drain
- `stop_job` sends `SIGTERM`, waits up to 5s, then `SIGKILL`
- Log files are deleted when a job is stopped

---

## Speech-to-text (`laptop/stt.py`)

Uses faster-whisper with the `base` model by default. Key settings:

| Setting | Value | Reason |
|---------|-------|--------|
| `beam_size` | 1 | Speed over accuracy |
| `vad_filter` | True | Strip silence before transcription |
| `condition_on_previous_text` | False | Reduce hallucinations |
| `NO_SPEECH_THRESHOLD` | 0.75 | Discard segments Whisper is uncertain about |

Post-processing: strips hallucinated leading "Hello" and "Hello? Yes" phrases; strips leading punctuation artifacts; applies a name substitution for common mishearings (e.g. "Cleo" → "Clio"); re-capitalizes the first letter of whatever remains.

---

## Text-to-speech (`laptop/tts.py`)

Uses piper-tts with the `en_US-lessac-medium` voice. Sentences are synthesized one at a time and served as WAV files from `laptop/tmp/`. The phone fetches each WAV via HTTP and plays them sequentially.

WAV files older than 5 minutes are deleted at the start of each turn.

---

## Silence detection (phone-side)

Web Audio API `AnalyserNode` polls RMS level every 80ms.

| Constant | Value | Description |
|----------|-------|-------------|
| `SILENCE_THRESHOLD` | 0.015 | RMS below this = silence |
| `SILENCE_DURATION_MS` | 1000 | ms of silence before auto-send |
| `MIN_SPEECH_DURATION_MS` | 400 | Ignore clips shorter than this |
| `LEVEL_CHECK_INTERVAL_MS` | 80 | Poll interval |

---

## Streaming audio playback (phone-side)

- Each `audio_chunk` message triggers a fetch of the WAV URL
- Chunks are queued and played back-to-back via Web Audio API
- Audio is routed through an `AnalyserNode` that drives a live amplitude-driven glow animation using `requestAnimationFrame`
- `turn_end` signals no more chunks; mic re-enables after the queue drains
- iOS safety net: each chunk has a `duration + 800ms` timeout fallback for silent `onended`

---

## Mic mute (phone-side)

Tapping the mic button after initial setup toggles mute. When muted:

- `stopMicTracks()` disconnects the `MediaStreamSource` node and stops all tracks — this turns off the OS mic indicator light
- The `levelCheckInterval` is cleared so VAD stops polling
- The session glow and status badge are cleared

On unmute, `restartMicTracks()` calls `getUserMedia` again, rebuilds the mic pipeline, and resumes the VAD loop — without recreating the `AudioContext`.

---

## Session logs

Each WebSocket connection generates a markdown log in `laptop/logs/`:

```
# Claude Voice Session
**Date**: 2026-04-27
**Time**: 18:29:55

---

**[18:30:06] You**
Hello

**[18:30:06] Claude**
Hey! Good to hear from you. What are we working on today?

---

*Session ended: 18:31:00 | Exchanges: 1*
```

Logs are written on disconnect. They are gitignored and stay local.

---

## Networking

Clio uses Tailscale for phone access. `start.sh`:
1. Reads your Tailscale IP
2. Provisions a TLS cert via `tailscale cert`
3. Starts uvicorn with SSL
4. Pushes the URL to your phone via ntfy.sh (if configured)

The self-signed cert enables `wss://` WebSocket connections, required for PWA mic access on mobile browsers.

---

## Future work

### Interrupt-while-speaking

Currently the mic re-enables only after the full audio queue drains (`turn_end` + queue empty). A better UX would let the user speak mid-response and have Clio stop immediately to listen — the same way a real conversation works.

What this would require:
- Phone detects voice activity during playback and sends an `interrupt` message over the WebSocket
- Backend cancels the in-flight Claude stream and the `synthesis_worker` task, discards queued audio chunks
- Backend sends a `stop_audio` message; phone clears the playback queue and stops current audio
- Mic pipeline restarts and normal VAD flow resumes

The phone-side VAD already runs continuously (see Silence detection), so detecting speech during playback is straightforward. The harder part is cleanly cancelling the async synthesis worker and stream without corrupting conversation state.
