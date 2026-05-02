#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

BOLD='\033[1m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m'

step() { echo -e "\n${BOLD}${CYAN}▸ $1${NC}"; }
ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}!${NC} $1"; }
err()  { echo -e "  ${RED}✗${NC} $1"; exit 1; }

echo -e "\n${BOLD}Clio — Install${NC}"
echo "────────────────────────────────────────"

# ── 1. Python virtual environment ─────────────────────────────────────────────

step "Python virtual environment"

if [ ! -d ".venv" ]; then
  if ! python3 -m venv --help &>/dev/null; then
    warn "python3-venv not found — attempting to install..."
    if command -v apt-get &>/dev/null; then
      sudo add-apt-repository -y universe
      sudo apt-get update -q && sudo apt-get install -y python3-venv
    else
      err "Could not install python3-venv automatically. Install it manually and re-run."
    fi
  fi
  python3 -m venv .venv
  ok "Virtual environment created"
else
  ok "Virtual environment already exists"
fi

source .venv/bin/activate
pip install -q -r laptop/requirements.txt
ok "Dependencies installed"

# ── 2. TTS voice model ────────────────────────────────────────────────────────

step "TTS voice model"

MODEL_DIR="$SCRIPT_DIR/laptop/models"
ONNX="$MODEL_DIR/en_US-lessac-medium.onnx"
JSON_FILE="$MODEL_DIR/en_US-lessac-medium.onnx.json"
BASE_URL="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium"

if [ -f "$ONNX" ] && [ -f "$JSON_FILE" ]; then
  ok "Model already downloaded"
else
  echo "  Downloading en_US-lessac-medium (~63 MB)..."
  wget -q --show-progress -P "$MODEL_DIR" "$BASE_URL/en_US-lessac-medium.onnx"
  wget -q --show-progress -P "$MODEL_DIR" "$BASE_URL/en_US-lessac-medium.onnx.json"
  ok "Model downloaded"
fi

# ── 3. Config files ───────────────────────────────────────────────────────────

step "Config files"

if [ ! -f config.sh ]; then
  cp config.sh.example config.sh
  ok "config.sh created"
else
  ok "config.sh already exists"
fi

if [ ! -f memory.md ]; then
  cp memory.example.md memory.md
  ok "memory.md created"
else
  ok "memory.md already exists"
fi

if [ ! -f soul.md ]; then
  cp soul.example.md soul.md
  ok "soul.md created"
else
  ok "soul.md already exists"
fi

# ── 4. Networking ─────────────────────────────────────────────────────────────

step "Networking"

echo ""
echo "  How do you want to connect your phone to Clio?"
echo ""
echo "    1) Tailscale  — recommended for daily use"
echo "       Stable URL, PWA installable, audio stays on your local network."
echo ""
echo "    2) Cloudflare — good for trying it out"
echo "       No Tailscale needed, but URL changes on each restart."
echo "       PWA installation won't persist."
echo ""

while true; do
  read -rp "  Choose [1/2]: " net_choice
  case "$net_choice" in
    1) TUNNEL_MODE="tailscale"; break ;;
    2) TUNNEL_MODE="cloudflare"; break ;;
    *) echo "  Please enter 1 or 2." ;;
  esac
done

sed -i "s|^TUNNEL_MODE=.*|TUNNEL_MODE=\"$TUNNEL_MODE\"|" config.sh

if [ "$TUNNEL_MODE" = "tailscale" ]; then
  echo ""
  echo "  Run 'tailscale status' to find your Tailscale hostname."
  echo "  It looks like: your-machine.tail12345.ts.net"
  echo ""
  while true; do
    read -rp "  Tailscale hostname: " ts_host
    [ -n "$ts_host" ] && break
    echo "  Hostname cannot be empty."
  done
  sed -i "s|^TAILSCALE_HOST=.*|TAILSCALE_HOST=\"$ts_host\"|" config.sh
  ok "Tailscale configured"
else
  if ! command -v cloudflared &>/dev/null; then
    warn "cloudflared not found. Install it before running ./start.sh:"
    echo "  https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
  else
    ok "cloudflared found"
  fi
fi

# ── 5. Anthropic API key ──────────────────────────────────────────────────────

step "Anthropic API key"

echo ""
echo "  Get your key from: https://console.anthropic.com → API Keys → Create Key"
echo ""

while true; do
  read -rp "  Paste your API key (or press Enter to skip): " api_key
  if [ -z "$api_key" ]; then
    warn "Skipped — set ANTHROPIC_API_KEY before running ./start.sh"
    break
  fi
  [[ "$api_key" == sk-ant-* ]] && break
  warn "Key should start with sk-ant-. Try again."
done

if [ -n "$api_key" ]; then
  export ANTHROPIC_API_KEY="$api_key"

  if [ -f "$HOME/.zshrc" ]; then
    PROFILE="$HOME/.zshrc"
  else
    PROFILE="$HOME/.bashrc"
  fi

  echo ""
  read -rp "  Add to $PROFILE to persist across sessions? [Y/n]: " persist
  if [[ ! "$persist" =~ ^[Nn] ]]; then
    grep -v "^export ANTHROPIC_API_KEY=" "$PROFILE" > "$PROFILE.tmp" 2>/dev/null && mv "$PROFILE.tmp" "$PROFILE" || true
    echo "export ANTHROPIC_API_KEY=\"$api_key\"" >> "$PROFILE"
    ok "API key saved to $PROFILE"
  else
    warn "API key set for this session only"
  fi
fi

# ── 6. Push notifications (optional) ──────────────────────────────────────────

step "Push notifications (optional)"

echo ""
echo "  Clio can push the URL to your phone via ntfy.sh when it starts."
echo "  Create a free topic at ntfy.sh and enter it below, or press Enter to skip."
echo ""
read -rp "  ntfy.sh topic: " ntfy_topic

if [ -n "$ntfy_topic" ]; then
  sed -i "s|^NTFY_TOPIC=.*|NTFY_TOPIC=\"$ntfy_topic\"|" config.sh
  ok "ntfy.sh configured"
else
  ok "Skipped"
fi

# ── Done ──────────────────────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}${GREEN}✓ Clio is ready.${NC}"
echo ""
echo "  Start:   ./start.sh"
echo "  Restart: ./restart.sh"
echo ""
