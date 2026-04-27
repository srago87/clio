# Clio

A voice-controlled coding assistant you talk to from your phone. Speak a request, Clio transcribes it, runs a Claude API agent loop with real tool use, and reads the response back sentence by sentence as it arrives.

> **Demo:** "Add a retry loop to the fetch function in api.js" → Clio finds the file, reads the relevant section, edits it, and confirms what it did — all by voice.

---

## What it does

- **Voice in, voice out** — speak from your phone, hear responses in real time via streaming TTS
- **Real tool use** — reads, writes, and edits files; runs shell commands; searches the web; manages background processes
- **Phone-side approval** — destructive actions (writing files, running commands) require a tap to approve before executing
- **Persistent memory** — Clio maintains a memory file across sessions and a per-session scratchpad
- **Customizable personality** — edit `soul.md` to change how Clio thinks and speaks
- **PWA** — installable on your phone's home screen, works over your local network via Tailscale

---

## Architecture

```
Phone (PWA)
    ↕ WebSocket (wss://)
FastAPI Backend (laptop)
    ├── faster-whisper  (speech-to-text)
    ├── Anthropic API   (Claude Sonnet agent loop)
    ├── piper-tts       (text-to-speech, streamed sentence by sentence)
    └── VoiceSession    (markdown log per connection)

Tailscale → laptop:8765 (HTTPS/WSS via Tailscale cert)
```

The agent loop streams Claude's response token by token, splits it into sentences, and synthesizes + sends each sentence to the phone as it completes — so audio starts playing within a second or two of Claude beginning to respond.

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

### 1. Clone and create a virtual environment

```bash
git clone https://github.com/srago87/clio
cd clio
python -m venv .venv && source .venv/bin/activate
pip install -r laptop/requirements.txt
```

### 2. Download models

**Whisper (speech-to-text):**
```bash
# The base model downloads automatically on first run.
# For better accuracy at the cost of speed, edit WHISPER_MODEL in laptop/stt.py
```

**Piper (text-to-speech):**
```bash
mkdir -p laptop/models
cd laptop/models
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json
```

### 3. Configure

```bash
cp config.sh.example config.sh
```

Edit `config.sh` and fill in your Tailscale hostname and (optionally) your ntfy.sh topic.

```bash
cp memory.example.md memory.md
cp soul.example.md soul.md
```

### 4. Set your API key

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

Add this to your shell profile (`~/.bashrc` or `~/.zshrc`) to persist it.

### 5. Configure networking

**Option A — Tailscale (recommended):**
Set `TUNNEL_MODE=tailscale` in `config.sh` and fill in your `TAILSCALE_HOST`. Tailscale must be installed and connected on both your laptop and phone. `start.sh` provisions a TLS cert automatically via `tailscale cert`.

**Option B — Cloudflare Tunnel (easier setup):**
Install [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/), then set `TUNNEL_MODE=cloudflare` in `config.sh`. No Tailscale or cert setup needed — `start.sh` handles everything. The URL changes on each restart.

### 6. Start

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

---

## Customization

### Personality (`soul.md`)
Edit `soul.md` to change how Clio speaks and behaves. It's loaded into the system prompt every turn. The example file is a good starting point.

### Memory (`memory.md`)
Clio reads `memory.md` at the start of every turn and updates it herself via the `update_memory` tool. Edit it directly to give Clio context about you and your projects.

### Tools
Tools are defined in `laptop/tools.py`. Add new tools by:
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
| `check_job` | Auto | Read output from a background job |
| `stop_job` | Auto | Kill a background job |
| `list_jobs` | Auto | List all background jobs |
| `write_file` | Phone approval | Create or overwrite a file |
| `edit_file` | Phone approval | Replace a string in a file |
| `bash_command` | Phone approval | Run a shell command |
| `run_background` | Phone approval | Start a long-running process |
| `delete_file` | Phone approval | Delete a file |

---

## Project structure

```
clio/
├── laptop/
│   ├── main.py         # FastAPI app, WebSocket endpoint
│   ├── agent.py        # AgentSession: STT → Claude agent loop → TTS
│   ├── tools.py        # Tool definitions and execution
│   ├── jobs.py         # Background process manager
│   ├── stt.py          # faster-whisper wrapper
│   ├── tts.py          # piper-tts wrapper
│   ├── session.py      # Per-connection session log
│   └── requirements.txt
├── phone/
│   ├── index.html      # Mobile PWA shell
│   ├── app.js          # WebSocket client, silence detection, audio playback
│   ├── style.css
│   ├── manifest.json
│   └── sw.js           # Service worker
├── soul.example.md     # Clio's personality — copy to soul.md and customize
├── memory.example.md   # Clio's memory template — copy to memory.md
├── config.sh.example   # Config template — copy to config.sh and fill in
├── start.sh            # Start the server
└── restart.sh          # Restart a running instance
```

---

## How the agent loop works

1. Phone records audio until silence is detected (~1s of quiet)
2. Audio clip (WebM) is sent over WebSocket as base64
3. Backend transcribes with faster-whisper
4. Transcript is added to conversation history and sent to Claude API with streaming enabled
5. Text tokens are split into sentences as they stream
6. Each sentence is synthesized by piper-tts and sent to the phone immediately
7. Phone plays audio chunks back-to-back as they arrive
8. If Claude requests a tool, the stream pauses, the tool executes, and the loop continues
9. Destructive tools pause and send a `permission_request` to the phone before executing

See [docs/architecture.md](docs/architecture.md) for full technical detail.

---

## Limitations

- **ARM64 Linux only tested** — runs on Apple Silicon Mac and ARM64 Linux. x86 should work but is untested.
- **TLS cert setup is Tailscale-specific** — if using Cloudflare Tunnel, cert provisioning is handled differently. Contributions welcome for other networking setups.
- **No conversation branching** — conversation history grows linearly with no summarization strategy. Very long sessions will eventually approach context limits.
- **piper-tts voices** — the default voice (lessac-medium) is clear but robotic. Other voices available at [rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices).

---

## Contributing

Pull requests welcome. The most valuable contributions are:
- New tools
- Alternative networking setups (Cloudflare Tunnel, ngrok, local network without Tailscale)
- Better STT/TTS model support
- Context management for long sessions
