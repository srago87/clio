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
TOTAL_STEPS=10
CURRENT_STEP=0

step() {
  CURRENT_STEP=$((CURRENT_STEP + 1))
  percent=$((CURRENT_STEP * 100 / TOTAL_STEPS))
  echo -e "\n${BOLD}${CYAN}▸ Step $CURRENT_STEP/$TOTAL_STEPS ($percent%) - $1${NC}"
}
ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}!${NC} $1"; }
err()  { echo -e "  ${RED}✗${NC} $1"; exit 1; }
# Cross-platform sed -i (GNU requires no suffix, BSD requires empty string suffix)
sed_i() { sed -i.bak "$1" "$2" && rm -f "$2.bak"; }
OS_NAME="$(uname -s)"
TAILSCALE_MACOS_PKG_URL="https://pkgs.tailscale.com/stable/Tailscale-latest-macos.pkg"

pick_python() {
  for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" &>/dev/null && "$candidate" - <<'PYEOF'
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PYEOF
    then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

install_apt_packages() {
  if ! command -v sudo &>/dev/null; then
    err "sudo is required to install system packages with apt. Install Python 3.11+ manually and re-run."
  fi
  sudo apt-get update -q
  sudo apt-get install -y "$@"
}

apt_has_package() {
  apt-cache show "$1" >/dev/null 2>&1
}

install_python_debian() {
  warn "Python 3.11+ not found — checking apt for a supported Python..."

  for version in 3.12 3.11; do
    if apt_has_package "python$version" && apt_has_package "python$version-venv"; then
      echo "  Installing python$version and python$version-venv..."
      install_apt_packages "python$version" "python$version-venv"
      hash -r
      return 0
    fi
  done

  err "Python 3.11+ is required, but this distro's apt repositories do not provide python3.11/python3.12 with venv. Use Ubuntu 24.04+, install Python 3.11+ manually, or enable an appropriate distro-supported repository."
}

install_python_venv_debian() {
  python_bin="$1"
  package="${python_bin}-venv"

  if apt_has_package "$package"; then
    echo "  Installing $package..."
    install_apt_packages "$package"
  elif apt_has_package python3-venv; then
    echo "  Installing python3-venv..."
    install_apt_packages python3-venv
  else
    err "Could not find a venv package for $python_bin. Install ${package} manually and re-run."
  fi
}

download_file() {
  url="$1"
  dest_dir="$2"
  echo "  Downloading $(basename "$url")..."
  if command -v wget &>/dev/null; then
    wget -q --show-progress -P "$dest_dir" "$url"
  elif command -v curl &>/dev/null; then
    curl -L --fail --progress-bar -o "$dest_dir/$(basename "$url")" "$url"
  else
    err "Neither wget nor curl is installed. Install one and re-run."
  fi
}

find_tailscale() {
  if command -v tailscale &>/dev/null; then
    command -v tailscale
    return 0
  fi
  for candidate in \
    "/Applications/Tailscale.app/Contents/MacOS/Tailscale" \
    "/Applications/Tailscale.app/Contents/MacOS/tailscale"; do
    if [ -x "$candidate" ]; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

install_tailscale_macos() {
  local tmp_dir pkg
  tmp_dir="$(mktemp -d)"
  pkg="$tmp_dir/Tailscale-latest-macos.pkg"

  echo "  Downloading Tailscale for macOS..."
  curl -L --fail --progress-bar -o "$pkg" "$TAILSCALE_MACOS_PKG_URL"
  echo "  Installing Tailscale. macOS may ask for your password..."
  sudo installer -pkg "$pkg" -target /
  open -a Tailscale >/dev/null 2>&1 || true
  rm -rf "$tmp_dir"
  hash -r
}

echo -e "\n${BOLD}Clio — Install${NC}"
echo "────────────────────────────────────────"

# ── 1. Python virtual environment ─────────────────────────────────────────────

step "Python virtual environment"

if ! PYTHON_BIN="$(pick_python)"; then
  if [ "$OS_NAME" = "Darwin" ]; then
    err "Python 3.11+ is required. Install it with Homebrew: brew install python@3.12, or from https://www.python.org/downloads/macos/."
  elif command -v apt-get &>/dev/null; then
    install_python_debian
    PYTHON_BIN="$(pick_python)" || err "Python 3.11+ is still not available. Install Python 3.11+ and re-run."
  else
    err "Python 3.11+ is required. Install it and re-run."
  fi
fi
ok "Using $PYTHON_BIN ($($PYTHON_BIN --version 2>&1))"

create_venv=0
if [ ! -f ".venv/bin/activate" ]; then
  create_venv=1
elif ! .venv/bin/python - <<'PYEOF'
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PYEOF
then
  warn ".venv was created with Python < 3.11; recreating it with $PYTHON_BIN..."
  rm -rf .venv
  create_venv=1
else
  ok "Virtual environment already exists"
fi

if [ "$create_venv" = "1" ]; then
  if ! "$PYTHON_BIN" -c "import ensurepip" &>/dev/null; then
    warn "python3-venv not found — attempting to install..."
    if command -v apt-get &>/dev/null; then
      install_python_venv_debian "$PYTHON_BIN"
    else
      err "Could not install python3-venv automatically. Install it manually and re-run."
    fi
  fi
  "$PYTHON_BIN" -m venv .venv
  ok "Virtual environment created"
fi

source .venv/bin/activate
if ! python - <<'PYEOF'
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PYEOF
then
  err ".venv was created with Python < 3.11. Delete .venv and re-run ./install.sh with Python 3.11+ available."
fi
echo "  Installing Python dependencies (this can take a few minutes)..."
pip install --progress-bar on -r server/requirements.txt
ok "Dependencies installed"

# ── 2. TTS voice model ────────────────────────────────────────────────────────

step "TTS voice model"

MODEL_DIR="$SCRIPT_DIR/server/models"
mkdir -p "$MODEL_DIR"

# Read TTS_ENGINE from config.sh if it exists, otherwise default to kokoro
if [ -f "$SCRIPT_DIR/config.sh" ]; then
  source "$SCRIPT_DIR/config.sh"
fi
TTS_ENGINE="${TTS_ENGINE:-kokoro}"

if [ "$TTS_ENGINE" = "kokoro" ]; then
  KOKORO_ONNX="$MODEL_DIR/kokoro-v1.0.onnx"
  KOKORO_VOICES="$MODEL_DIR/voices-v1.0.bin"
  KOKORO_BASE_URL="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"

  if [ -f "$KOKORO_ONNX" ] && [ -f "$KOKORO_VOICES" ]; then
    ok "Kokoro model files already downloaded"
  else
    echo "  Downloading Kokoro model files (~338 MB total; this may take a few minutes)..."
    download_file "$KOKORO_BASE_URL/kokoro-v1.0.onnx" "$MODEL_DIR"
    download_file "$KOKORO_BASE_URL/voices-v1.0.bin" "$MODEL_DIR"
    ok "Kokoro model files downloaded"
  fi
else
  PIPER_ONNX="$MODEL_DIR/en_US-lessac-medium.onnx"
  PIPER_JSON="$MODEL_DIR/en_US-lessac-medium.onnx.json"
  PIPER_BASE_URL="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium"

  if [ -f "$PIPER_ONNX" ] && [ -f "$PIPER_JSON" ]; then
    ok "Piper model already downloaded"
  else
    echo "  Downloading en_US-lessac-medium (~63 MB)..."
    download_file "$PIPER_BASE_URL/en_US-lessac-medium.onnx" "$MODEL_DIR"
    download_file "$PIPER_BASE_URL/en_US-lessac-medium.onnx.json" "$MODEL_DIR"
    ok "Piper model downloaded"
  fi
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

# ── 4. User name ──────────────────────────────────────────────────────────────

step "Your name"

echo ""
echo "  What would you like Clio to call you?"
echo "  This can be your name, a nickname, or anything you prefer."
echo ""
read -rp "  Name: " user_name
if [ -n "$user_name" ]; then
  sed_i "s|^USER_NAME=.*|USER_NAME=\"$user_name\"|" config.sh
  ok "Clio will call you $user_name"
else
  ok "Skipped — you can set USER_NAME in config.sh later"
fi

# ── 5. Timezone ───────────────────────────────────────────────────────────────

step "Timezone"

echo ""
echo "  What timezone are you in? This lets Clio report the correct local time."
echo ""
echo "    1) America/New_York     (Eastern)"
echo "    2) America/Chicago      (Central)"
echo "    3) America/Denver       (Mountain)"
echo "    4) America/Los_Angeles  (Pacific)"
echo "    5) Europe/London        (GMT/BST)"
echo "    6) Other                (enter manually)"
echo ""

while true; do
  read -rp "  Choose [1-6]: " tz_choice
  case "$tz_choice" in
    1) TIMEZONE="America/New_York"; break ;;
    2) TIMEZONE="America/Chicago"; break ;;
    3) TIMEZONE="America/Denver"; break ;;
    4) TIMEZONE="America/Los_Angeles"; break ;;
    5) TIMEZONE="Europe/London"; break ;;
    6)
      read -rp "  Enter timezone (e.g. Europe/Paris, Asia/Tokyo): " TIMEZONE
      [ -n "$TIMEZONE" ] && break
      echo "  Timezone cannot be empty."
      ;;
    *) echo "  Please enter a number from 1 to 6." ;;
  esac
done

sed_i "s|^TIMEZONE=.*|TIMEZONE=\"$TIMEZONE\"|" config.sh
ok "Timezone set to $TIMEZONE"

# ── 6. Networking ─────────────────────────────────────────────────────────────

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

sed_i "s|^TUNNEL_MODE=.*|TUNNEL_MODE=\"$TUNNEL_MODE\"|" config.sh

TAILSCALE_BIN="$(find_tailscale || true)"
if [ "$TUNNEL_MODE" = "tailscale" ] && [ "$OS_NAME" = "Darwin" ] && [ -z "$TAILSCALE_BIN" ]; then
  warn "Tailscale CLI not found."
  install_tailscale_macos
  TAILSCALE_BIN="$(find_tailscale || true)"
  if [ -z "$TAILSCALE_BIN" ]; then
    warn "Automatic Tailscale installation did not expose a CLI."
    echo "  Open Tailscale, finish setup if prompted, then re-run ./install.sh."
    echo ""
    read -rp "  Switch to Cloudflare for now instead? [Y/n]: " fallback_cloudflare
    if [[ "$fallback_cloudflare" =~ ^[Nn] ]]; then
      err "Tailscale is required for TUNNEL_MODE=tailscale."
    fi
    TUNNEL_MODE="cloudflare"
    sed_i "s|^TUNNEL_MODE=.*|TUNNEL_MODE=\"$TUNNEL_MODE\"|" config.sh
    ok "Switched networking mode to Cloudflare"
  else
    ok "Tailscale installed"
  fi
fi

if [ "$TUNNEL_MODE" = "tailscale" ]; then
  if [ -z "$TAILSCALE_BIN" ]; then
    echo "  Installing Tailscale..."
    if command -v curl &>/dev/null; then
      curl -fsSL https://tailscale.com/install.sh | sh
    elif command -v wget &>/dev/null; then
      wget -qO- https://tailscale.com/install.sh | sh
    else
      err "Install curl or wget, then re-run."
    fi
    TAILSCALE_BIN="$(find_tailscale || true)"
    [ -n "$TAILSCALE_BIN" ] || err "Tailscale installation failed. Install it manually: https://tailscale.com/download"
    ok "Tailscale installed"
  else
    ok "Tailscale already installed"
  fi

  if ! "$TAILSCALE_BIN" ip &>/dev/null; then
    echo ""
    if [ "$OS_NAME" = "Darwin" ]; then
      echo "  Open the Tailscale app, sign in, and make sure this Mac is connected."
      echo "  Then re-run ./install.sh."
      err "Tailscale is installed but not connected."
    else
      echo "  Connect this machine to your Tailscale account."
      echo "  A browser link will appear below — open it to authenticate."
      echo ""
      sudo "$TAILSCALE_BIN" up
      ok "Tailscale connected"
    fi
  else
    ok "Tailscale already connected"
  fi

  ts_host=$("$TAILSCALE_BIN" status --json 2>/dev/null | python -c "import json,sys; print(json.load(sys.stdin)['Self']['DNSName'].rstrip('.'))" 2>/dev/null || true)
  if [ -z "$ts_host" ]; then
    echo ""
    echo "  Could not detect hostname automatically."
    echo "  Run 'tailscale status' to find it — looks like: your-machine.tail12345.ts.net"
    echo ""
    while true; do
      read -rp "  Tailscale hostname: " ts_host
      [ -n "$ts_host" ] && break
      echo "  Hostname cannot be empty."
    done
  fi
  sed_i "s|^TAILSCALE_HOST=.*|TAILSCALE_HOST=\"$ts_host\"|" config.sh
  ok "Tailscale configured: $ts_host"

  if [ "$OS_NAME" = "Darwin" ]; then
    ok "Tailscale configured"
  else
    sudo "$TAILSCALE_BIN" set --operator="$USER"
    ok "Tailscale operator set ($USER can provision TLS certs)"
  fi
else
  if ! command -v cloudflared &>/dev/null; then
    warn "cloudflared not found. Install it before running ./start.sh:"
    echo "  https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
  else
    ok "cloudflared found"
  fi
fi

# ── 5. Speech recognition language ───────────────────────────────────────────

step "Speech recognition language"

echo ""
echo "  Choose your spoken language so Whisper recognizes your speech correctly."
echo "  (Skipping this can cause Whisper to misidentify your language.)"
echo ""
echo "    1) English  (en)"
echo "    2) Spanish  (es)"
echo "    3) French   (fr)"
echo "    4) German   (de)"
echo "    5) Japanese (ja)"
echo "    6) Other    (enter a Whisper language code manually)"
echo ""

while true; do
  read -rp "  Choose [1-6]: " lang_choice
  case "$lang_choice" in
    1) STT_LANGUAGE="en"; break ;;
    2) STT_LANGUAGE="es"; break ;;
    3) STT_LANGUAGE="fr"; break ;;
    4) STT_LANGUAGE="de"; break ;;
    5) STT_LANGUAGE="ja"; break ;;
    6)
      read -rp "  Enter language code (e.g. zh, pt, ko): " STT_LANGUAGE
      [ -n "$STT_LANGUAGE" ] && break
      echo "  Language code cannot be empty."
      ;;
    *) echo "  Please enter a number from 1 to 6." ;;
  esac
done

sed_i "s|^STT_LANGUAGE=.*|STT_LANGUAGE=\"$STT_LANGUAGE\"|" config.sh
ok "Language set to $STT_LANGUAGE"

# ── 6. Anthropic API key ──────────────────────────────────────────────────────

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

  # Always write to config.sh so start.sh picks it up regardless of shell state
  CONFIG_FILE="$SCRIPT_DIR/config.sh"
  grep -v "^export ANTHROPIC_API_KEY=" "$CONFIG_FILE" > "$CONFIG_FILE.tmp" && mv "$CONFIG_FILE.tmp" "$CONFIG_FILE"
  echo "export ANTHROPIC_API_KEY=\"$api_key\"" >> "$CONFIG_FILE"
  ok "API key saved to config.sh"

  if [ -f "$HOME/.zshrc" ]; then
    PROFILE="$HOME/.zshrc"
  else
    PROFILE="$HOME/.bashrc"
  fi

  echo ""
  read -rp "  Also add to $PROFILE for use in other terminals? [Y/n]: " persist
  if [[ ! "$persist" =~ ^[Nn] ]]; then
    grep -v "^export ANTHROPIC_API_KEY=" "$PROFILE" > "$PROFILE.tmp" 2>/dev/null && mv "$PROFILE.tmp" "$PROFILE" || true
    echo "export ANTHROPIC_API_KEY=\"$api_key\"" >> "$PROFILE"
    ok "API key also saved to $PROFILE"
  fi
fi

# ── 7. Push notifications (optional) ──────────────────────────────────────────

step "Push notifications (optional)"

echo ""
echo "  Clio can push the URL to your phone via ntfy.sh when it starts."
echo "  Create a free topic at ntfy.sh and enter it below, or press Enter to skip."
echo ""
read -rp "  ntfy.sh topic: " ntfy_topic

if [ -n "$ntfy_topic" ]; then
  sed_i "s|^NTFY_TOPIC=.*|NTFY_TOPIC=\"$ntfy_topic\"|" config.sh
  ok "ntfy.sh configured"
else
  ok "Skipped"
fi

# ── 8. Whisper model download ─────────────────────────────────────────────────

step "Whisper speech recognition model"

if [ -f "$SCRIPT_DIR/config.sh" ]; then
  source "$SCRIPT_DIR/config.sh"
fi
WHISPER_MODEL="${WHISPER_MODEL:-small}"
MODELS_DIR="$SCRIPT_DIR/server/models"

echo "  Downloading Whisper model: $WHISPER_MODEL (this may take a moment)..."
WHISPER_MODEL="$WHISPER_MODEL" MODELS_DIR="$MODELS_DIR" .venv/bin/python3 - <<'PYEOF'
import os
from pathlib import Path
from faster_whisper import WhisperModel
model_name = os.environ.get("WHISPER_MODEL", "small")
models_dir = Path(os.environ.get("MODELS_DIR", "server/models"))
WhisperModel(model_name, compute_type="int8", download_root=str(models_dir))
PYEOF
ok "Whisper model ready"

# ── Done ──────────────────────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}${GREEN}✓ Clio is ready.${NC}"
echo "  Install progress: 100%"
echo ""
echo "  Start:   ./start.sh"
echo "  Restart: ./restart.sh"
echo ""
