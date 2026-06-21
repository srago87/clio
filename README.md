# Clio

A voice-controlled coding assistant you talk to from your phone. Speak a request, Clio transcribes it, runs a Claude API agent loop with real tool use, and reads the response back sentence by sentence as it arrives.

> **Demo:** "Add a retry loop to the fetch function in api.js" → Clio finds the file, reads the relevant section, edits it, and confirms what it did — all by voice.

---

## Vision

The coding focus is intentional, not the end goal. A computer fundamentally reads, writes, and executes on files — everything else is an abstraction built on those three operations. Starting here means building on the most verifiable foundation: either the code works or it doesn't.

The broader goal is a general-purpose ambient assistant — something reachable from anywhere, that you can talk to naturally and ask to handle anything you'd normally have to sit down at a computer for. The architecture already supports this. Extending into new domains is mostly a question of adding tools and context.

---

## What it does

- **Voice in, voice out** — speak from your phone, hear responses in real time via streaming TTS
- **Real tool use** — reads, writes, and edits files; runs shell commands; searches the web; manages background processes; fetches URLs; controls a browser
- **Phone-side approval** — destructive actions (writing files, running commands) require a tap to approve before executing
- **Model selector** — switch between Haiku, Sonnet, and Opus from the phone UI; memory pipeline always uses Haiku regardless of selection
- **Live cost counter** — session spend shown in the header, ticking up in real time as API calls complete
- **Mic mute toggle** — tap the mic button to mute; the OS mic indicator light goes off and Clio stays quiet until you unmute
- **Live status badge** — shows what Clio is doing with a label and elapsed timer (e.g. "read agent.py (3s)")
- **Amplitude-driven glow** — the speaking glow pulses in real time with the audio amplitude during playback
- **Persistent memory** — Clio maintains a memory file across sessions, automatically extracting and consolidating facts from each conversation, with compression when the file grows too large
- **Customizable personality** — edit `soul.md` to change how Clio thinks and speaks
- **PWA** — installable on your phone's home screen, works over your local network via Tailscale

---

## Architecture

```
Phone (PWA)
    ↕ WebSocket (wss://)
FastAPI Backend (server)
    ├── faster-whisper  (speech-to-text)
    ├── Anthropic API   (Claude agent loop — Sonnet by default, Haiku for memory)
    ├── Kokoro / Piper  (text-to-speech, streamed sentence by sentence)
    └── VoiceSession    (markdown log per connection)

Tailscale → server:8765 (HTTPS/WSS via Tailscale cert)
```

The agent loop streams Claude's response token by token, splits it into sentences, and synthesizes + sends each sentence to the phone as it completes — so audio starts playing within a couple of seconds of Claude beginning to respond.

---

## Requirements

- **Python 3.11+**
- **Anthropic API key** — [console.anthropic.com](https://console.anthropic.com)
- **Networking** — one of:
  - **Tailscale** *(recommended)* — peer-to-peer, audio stays on your local network
  - **Cloudflare Tunnel** *(easier setup)* — install [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/), audio routes through Cloudflare
- **ntfy.sh** — optional, for push notifications to your phone when Clio starts
- A phone with a modern browser (iOS Safari or Android Chrome)

---

## Setup

### Guided install (recommended)

```bash
git clone https://github.com/srago87/clio
cd clio
./install.sh
```

The installer walks through each step interactively: dependencies, model download, networking, API key, and optional push notifications.

### Manual install

### 1. Clone and create a virtual environment

On Debian/Ubuntu, install the venv package first:
```bash
sudo apt update && sudo apt install python3-venv
```

Then clone and set up:
```bash
git clone https://github.com/srago87/clio
cd clio
python3 -m venv .venv && source .venv/bin/activate
pip install -r server/requirements.txt
```

### 2. Download models

**Whisper (speech-to-text):**
```bash
# The small model downloads automatically on first run.
# To use a different model, set WHISPER_MODEL in config.sh (e.g. WHISPER_MODEL="base" for speed or "medium" for accuracy).
```

**Piper (text-to-speech):**
```bash
cd server/models
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json
```

### 3. Configure

```bash
cp config.sh.example config.sh
cp memory.example.md memory.md
cp soul.example.md soul.md
```

`memory.md` is Clio's persistent memory — it reads and updates this file itself across sessions. `soul.md` defines its personality and is loaded into the system prompt every turn. Both are yours to edit.

Edit `config.sh` and set your networking option:

**Option A — Tailscale (for real use):**
[Tailscale](https://tailscale.com) is a free VPN app that creates a private network between your devices. Install it on your server and your phone ([tailscale.com/download](https://tailscale.com/download)), sign in with the same account on both, and make sure both show as connected in the Tailscale app before proceeding. Then set `TUNNEL_MODE=tailscale` and fill in your `TAILSCALE_HOST`. `start.sh` provisions a TLS cert automatically via `tailscale cert`. This gives you a stable URL, a working PWA, and keeps your audio traffic on your local network. This is the only option that works well for repeated daily use.

**Option B — Cloudflare Tunnel (just trying it out):**
Install [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/), then set `TUNNEL_MODE=cloudflare`. No Tailscale or cert setup needed — good for a quick test without committing to Tailscale. The URL changes on each restart, so PWA installation won't persist and you'll need to open a new URL in the browser every time you start Clio.

You can also optionally set an ntfy.sh topic in `config.sh` to receive a push notification on your phone when Clio starts.

### 4. Set your API key

Get your API key from [console.anthropic.com](https://console.anthropic.com) → API Keys → Create Key. Copy the key — it's only shown once.

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

To persist it across terminal sessions, add it to your shell profile. Replace `sk-ant-...` with your actual key:

```bash
echo 'export ANTHROPIC_API_KEY=sk-ant-...' >> ~/.bashrc && source ~/.bashrc
```

If you're using zsh, use `~/.zshrc` instead of `~/.bashrc`.

### 5. Start

```bash
./start.sh
```

The URL is printed to the terminal. If you configured ntfy.sh, it's also pushed to your phone. Open it in Safari (iOS) or Chrome (Android).

To restart a running instance: `./restart.sh`

---

## Phone setup

Open the URL in your phone's browser. On first visit, accept the TLS certificate warning (Tailscale issues a valid cert, but your phone needs to trust it once).

**Install as PWA:**
- iOS: Safari → Share → Add to Home Screen
- Android: browser menu → Install App

Once installed, tap the mic button to begin. Clio listens for speech, detects a pause (~1 second of quiet), and sends the clip automatically — no button to hold.

---

## Live diff viewer *(prototype)*

While Clio is working, open `/diff` in a desktop browser (same base URL as the phone UI) to watch file changes as they happen. Each time Clio writes or edits a file, a GitHub-style diff card appears showing the old and new content side by side — red for removed lines, green for added lines. A status dot in the header shows whether the connection is live.

This is useful for keeping an eye on what Clio is doing without switching to a terminal or manually re-reading files. Opening VS Code on the same directory works equally well and gives you the full editor experience; the diff viewer is a lighter alternative if you just want to monitor changes.

> **Prototype:** the diff viewer is functional but rough — no persistence across page refreshes, no file tree, and no way to revert a change from the UI. It's a starting point, not a finished tool.

---

## Mic mute

Tap the mic button any time after setup to toggle mute. When muted:

- The mic tracks are fully stopped, so the OS mic indicator light goes off
- The session glow clears
- Clio won't listen or respond until you unmute

If you mute while Clio is speaking, mic teardown is deferred until playback finishes to avoid suspending the AudioContext on iOS.

Tap again to unmute and resume.

---

## Customization

### Personality (`soul.md`)
Edit `soul.md` to change how Clio speaks and behaves. It's loaded into the system prompt every turn. The example file is a good starting point.

### Memory (`memory.md`)
Clio reads `memory.md` at the start of every turn and updates it via the `update_memory` tool. Edit it directly to give Clio context about you and your projects.

After each conversation turn, Clio autonomously extracts any facts worth keeping and appends them to memory. At the start of each session, it consolidates logs from previous sessions into memory and marks them processed. If the memory file grows beyond a configurable size limit (`MEMORY_SIZE_LIMIT` in `agent.py`), it compresses the file by summarizing it — discarding redundancy while keeping what matters. This happens automatically, both on session start and after explicit `update_memory` calls.

### Your name (`USER_NAME`)
Set `USER_NAME` in `config.sh` to have Clio address you by name. The installer prompts for this. You can also set it later by editing `config.sh` directly.

### Tools
Tools are defined in `server/tools.py`. Add new tools by:
1. Adding a definition to `TOOL_DEFINITIONS`
2. Adding the tool name to `AUTO_APPROVE` or `REQUIRE_APPROVAL`
3. Adding a case to `execute_tool`
4. Adding cases to `describe_tool_call` and `summarize_tool_result`

---

## Tools

| Tool | Approved | Description |
|------|:---:|-------------|
| `read_file` | Auto | Read file contents, optionally by line range |
| `list_directory` | Auto | List directory contents |
| `search_code` | Auto | Grep across files with regex |
| `find_files` | Auto | Find files by glob pattern |
| `web_search` | Auto | DuckDuckGo search |
| `read_url` | Auto | Fetch and extract text from a URL |
| `get_current_time` | Auto | Current date and time |
| `update_memory` | Auto | Overwrite `memory.md` |
| `update_scratchpad` | Auto | Update session working notes |
| `restart_server` | Auto | Restart the Clio process |
| `close_connection` | Auto | Drop the WebSocket so the client reconnects |
| `check_job` | Auto | Read output from a background job |
| `stop_job` | Auto | Kill a background job |
| `list_jobs` | Auto | List all background jobs |
| `browser_open` | Auto | Open a browser session (headless or visible) |
| `browser_navigate` | Auto | Navigate to a URL |
| `browser_screenshot` | Auto | Take a screenshot and analyze it visually |
| `browser_get_content` | Auto | Get readable text content of the current page |
| `browser_get_elements` | Auto | List interactive elements on the current page |
| `write_file` | Phone approval | Create or overwrite a file |
| `edit_file` | Phone approval | Replace a string in a file |
| `bash_command` | Phone approval | Run a shell command |
| `run_background` | Phone approval | Start a long-running process |
| `delete_file` | Phone approval | Delete a file |
| `browser_click` | Phone approval | Click an element on the current page |
| `browser_type` | Phone approval | Type into an input field |
| `browser_close` | Phone approval | Close the browser session |

---

## Project structure

```
clio/
├── server/
│   ├── main.py         # FastAPI app, WebSocket endpoint, logging setup
│   ├── agent.py        # AgentSession: STT → Claude agent loop → TTS
│   ├── tools.py        # Tool definitions and execution
│   ├── browser.py      # Playwright browser automation wrapper
│   ├── jobs.py         # Background process manager
│   ├── stt.py          # faster-whisper wrapper (VAD, hallucination filtering)
│   ├── tts.py          # Kokoro/Piper TTS wrapper
│   ├── session.py      # Per-connection session log
│   ├── cost.py         # Token usage tracking and cost calculation
│   └── requirements.txt
├── phone/
│   ├── index.html      # Mobile PWA shell
│   ├── app.js          # WebSocket client, VAD, streaming audio playback, glow animation
│   ├── style.css
│   ├── manifest.json
│   ├── icon.svg        # PWA icon (cyan waveform on dark background)
│   ├── icon-192.png
│   ├── icon-512.png
│   └── sw.js           # Service worker
├── soul.example.md     # Clio's personality — copy to soul.md and customize
├── memory.example.md   # Clio's memory template — copy to memory.md
├── config.sh.example   # Config template — copy to config.sh and fill in
├── start.sh            # Start the server
├── restart.sh          # Restart a running instance
└── server.log          # Rotating log file (1MB cap) — timing and debug output
```

---

## How the agent loop works

1. Phone records audio until silence is detected (~1s of quiet)
2. Audio clip (WebM) is sent over WebSocket as base64
3. Backend transcribes with faster-whisper; hallucinated phrases are filtered out
4. Transcript is added to conversation history and sent to Claude API with streaming enabled
5. Text tokens are split into sentences as they stream
6. Each sentence is synthesized by piper-tts and sent to the phone immediately
7. Phone plays audio chunks back-to-back as they arrive, with an amplitude-driven glow
8. If Claude requests a tool, the stream pauses, the tool executes, and the loop continues
9. Destructive tools pause and send a `permission_request` to the phone before executing

See [docs/architecture.md](docs/architecture.md) for full technical detail.

---

## Logging

Clio logs timing and debug output to `server.log` (rotating, 1MB cap). Third-party library noise is suppressed — only Clio's own markers appear. You can read it directly to diagnose latency or errors.

Typical latency breakdown:
- **Whisper transcription:** 750ms–1.7s
- **Claude API (time to first sentence):** 1.3s–3.7s
- **TTS synthesis per sentence:** 40–270ms

Total perceived latency is roughly 2–5 seconds end-to-end.

---

## Limitations

- **ARM64 Linux only tested** — x86 Linux should work but is untested. macOS and Windows are not supported.
- **TLS cert setup is Tailscale-specific** — if using Cloudflare Tunnel, cert provisioning is handled differently. Contributions welcome for other networking setups.
- **No conversation branching** — conversation history grows linearly with no summarization strategy. Very long sessions will eventually approach context limits.
- **piper-tts voices** — the default voice (lessac-medium) is clear but robotic. Other voices available at [rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices).

---

## Roadmap

### Improvements
- Better visible browser navigation — when browser_open is called with headless=false, Clio should narrate what it sees on screen more fluidly and take natural multi-step browsing actions without needing explicit instructions for each step.
- Smarter sentence splitting — the current regex splits only on period/exclamation/question mark followed by whitespace, which means version numbers, file paths, URLs, and abbreviations like "e.g." can cause premature or missed splits during TTS streaming.
- Context management for long sessions — conversation history grows without bound; add summarization or sliding-window trimming before context limits are hit.
- Multi-voice TTS — allow the user to choose from multiple piper voices in config.sh without editing Python source.

### New features
- Local WiFi access via mDNS — serve Clio at a stable `clio.local` URL on the home network with zero configuration, no Tailscale required; remote access via Tailscale or Cloudflare Tunnel remains available as an upgrade path.
- Conversation export — let the user ask Clio to save the current session transcript to a file.
- Notification when long tasks finish — push an ntfy.sh notification to the phone when a multi-step task completes so the user does not have to watch the screen.
- Tool usage history panel — a collapsible log on the phone UI showing all tool calls made in the current session, beyond the single-line summaries currently shown.
- ARM and x86 Docker image — make setup truly one-command on any Linux host.
- macOS and Windows support — Clio is currently Linux-only.
- Multi-LLM support — allow users to configure alternative LLM backends (OpenAI, Gemini, local models via Ollama, etc.) instead of being locked to the Anthropic Claude API.
- Planning framework for multi-session projects — before writing any code, Clio proposes a structured plan and generates design artifacts (data flow diagram, architecture overview, written business rules) for the user to review and redirect verbally. Multiple review rounds before coding begins. Togglable per task: skip the ceremony for small requests ("add a button"), engage it for anything spanning multiple sessions. Artifacts serve as a shared reference between sessions so neither Clio nor the user needs to re-derive intent from the code.

---

## Contributing

Pull requests welcome. The most valuable contributions are:
- New tools
- Alternative networking setups (Cloudflare Tunnel, ngrok, local network without Tailscale)
- Better STT/TTS model support
- Context management for long sessions
