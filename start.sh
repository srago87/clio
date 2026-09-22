#!/usr/bin/env bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
OS_NAME="$(uname -s)"

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

notify_ntfy() {
  local url="$1"
  if [ -n "$NTFY_TOPIC" ]; then
    curl -s -X POST "https://ntfy.sh/$NTFY_TOPIC" \
      -H "Title: Clio is ready" \
      -H "Priority: default" \
      -H "Actions: view, Open Clio, $url" \
      -d "$url" >/dev/null 2>&1 &
  fi
}

# Runs Clio, restarting it on a non-zero exit (e.g. a crash) and re-sourcing
# config.sh each time so edits made via restart_server (e.g. TTS_ENGINE)
# take effect — this loop's env was otherwise only set once at launch.
# Returns normally, rather than exiting the script, once python -m
# server.serve exits 0 (a clean Ctrl+C stop), so callers can still run
# mode-specific cleanup (e.g. killing a tunnel process) afterward.
run_server_loop() {
  while true; do
    set -a
    source "$CONFIG"
    set +a
    python -m server.serve "$@"
    EXIT_CODE=$?
    if [ $EXIT_CODE -eq 0 ]; then
      echo "Clio stopped."
      break
    fi
    echo "Clio exited with code $EXIT_CODE, restarting..."
    sleep 1
  done
}

CONFIG="$SCRIPT_DIR/config.sh"
if [ ! -f "$CONFIG" ]; then
  echo "Error: config.sh not found. Copy config.sh.example to config.sh and fill it in."
  exit 1
fi
set -a
source "$CONFIG"
set +a

TUNNEL_MODE="${TUNNEL_MODE:-local}"

if [ ! -f ".venv/bin/activate" ]; then
  echo "Error: .venv not found. Run ./install.sh first."
  exit 1
fi
source .venv/bin/activate

# ── Display (dev convenience) ─────────────────────────────────────────────────
# Starts Clio's display GUI alongside the server so one command brings up both
# while testing. This is backwards from the settled design — the display
# is meant to launch this script, not the other way around (see the
# project-clio memory) — but that lifecycle piece isn't
# built yet, so this is the fast path until it is.

DISPLAY_LOG="$SCRIPT_DIR/display.log"

# electron-vite dev spawns several child processes (renderer dev server,
# main/preload watchers, the Electron binary itself) that don't all share
# one simple PID to kill — match by command line instead so nothing is
# left running after this script exits.
kill_display() {
  pkill -f "electron-vite" 2>/dev/null
  pkill -f "$SCRIPT_DIR/display" 2>/dev/null
}
trap kill_display EXIT

if [ -d "$SCRIPT_DIR/display/node_modules" ]; then
  echo "Starting Clio's display (log: $DISPLAY_LOG)..."
  (cd "$SCRIPT_DIR/display" && npm run dev) >"$DISPLAY_LOG" 2>&1 &
else
  echo "Note: display/node_modules not found — skipping the display (run 'npm install' in display/ first)."
fi

# ── Local network mode (default) ──────────────────────────────────────────────

if [ "$TUNNEL_MODE" = "local" ]; then
  if ! command -v openssl &>/dev/null; then
    echo "Error: openssl not found. Install it, or set TUNNEL_MODE=tailscale or TUNNEL_MODE=cloudflare in config.sh."
    exit 1
  fi

  LOCAL_IP="$(python -m server.local_network)"
  if [ -z "$LOCAL_IP" ]; then
    echo "Error: could not detect a local network IP address. Connect to Wi-Fi/Ethernet, or set TUNNEL_MODE=tailscale or TUNNEL_MODE=cloudflare in config.sh."
    exit 1
  fi

  LOCAL_CERT="$SCRIPT_DIR/clio-local.crt"
  LOCAL_KEY="$SCRIPT_DIR/clio-local.key"

  # Regenerate the cert if it's missing or was issued for a different IP
  # (e.g. this machine got a new DHCP lease, or moved to a new network).
  # Extract the exact SAN IP and compare it as a whole string — grepping
  # "IP Address:$LOCAL_IP" directly would match as a substring (e.g.
  # 192.168.1.1 matching inside an existing 192.168.1.10 SAN line) and
  # wrongly reuse a stale cert for the new IP.
  CERT_IP=""
  if [ -f "$LOCAL_CERT" ]; then
    CERT_IP="$(openssl x509 -in "$LOCAL_CERT" -noout -ext subjectAltName 2>/dev/null | grep -oE 'IP Address:[0-9.]+' | head -1 | cut -d: -f2)"
  fi

  NEED_CERT=true
  if [ "$CERT_IP" = "$LOCAL_IP" ]; then
    NEED_CERT=false
  fi

  if $NEED_CERT; then
    echo "Generating local TLS certificate for $LOCAL_IP..."
    # A config file (rather than -addext) generates the IP SAN in a way
    # both OpenSSL and macOS's stock LibreSSL understand.
    OPENSSL_CNF="$(mktemp)"
    cat >"$OPENSSL_CNF" <<EOF
[req]
distinguished_name = req_distinguished_name
x509_extensions = v3_req
prompt = no

[req_distinguished_name]
CN = $LOCAL_IP

[v3_req]
subjectAltName = @alt_names

[alt_names]
IP.1 = $LOCAL_IP
EOF
    if ! openssl req -x509 -newkey rsa:2048 -sha256 -days 825 -nodes \
      -keyout "$LOCAL_KEY" -out "$LOCAL_CERT" -config "$OPENSSL_CNF" 2>/dev/null; then
      rm -f "$OPENSSL_CNF"
      echo "Error: failed to generate a local TLS certificate."
      exit 1
    fi
    rm -f "$OPENSSL_CNF"
  fi

  URL="https://$LOCAL_IP:8765"

  echo "Starting Clio..."
  echo ""
  echo "  Scan this on your phone (same Wi-Fi network):"
  echo ""
  python -m server.local_network qr "$URL"
  echo ""
  echo "  Or open directly: $URL"
  echo ""
  echo "  Your phone will warn that the connection isn't private the first time —"
  echo "  expected for a local, self-signed certificate. Tap through it (e.g."
  echo "  \"Visit site\" / \"Proceed\") to continue."
  echo ""

  notify_ntfy "$URL"
  run_server_loop --host 0.0.0.0 --port 8765 --ssl-certfile "$LOCAL_CERT" --ssl-keyfile "$LOCAL_KEY"
  exit 0
fi

# ── Cloudflare Tunnel mode ────────────────────────────────────────────────────

if [ "$TUNNEL_MODE" = "cloudflare" ]; then
  if ! command -v cloudflared &>/dev/null; then
    echo "Error: cloudflared not found. Install it from https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
    exit 1
  fi

  echo "Starting Clio with Cloudflare Tunnel..."

  CLOUDFLARE_LOG=$(mktemp)
  cloudflared tunnel --url http://localhost:8765 >"$CLOUDFLARE_LOG" 2>&1 &
  CLOUDFLARE_PID=$!

  # Wait up to 30s for the tunnel URL to appear
  URL=""
  for i in $(seq 1 30); do
    URL=$(grep -o 'https://[a-zA-Z0-9-]*\.trycloudflare\.com' "$CLOUDFLARE_LOG" 2>/dev/null | head -1)
    [ -n "$URL" ] && break
    sleep 1
  done

  if [ -z "$URL" ]; then
    echo "Error: cloudflared didn't produce a URL within 30s."
    kill "$CLOUDFLARE_PID" 2>/dev/null
    rm -f "$CLOUDFLARE_LOG"
    exit 1
  fi

  echo ""
  echo "  Open on your phone: $URL"
  echo ""

  notify_ntfy "$URL"
  run_server_loop --host 127.0.0.1 --port 8765
  kill "$CLOUDFLARE_PID" 2>/dev/null
  rm -f "$CLOUDFLARE_LOG"
  exit 0
fi

# ── Tailscale mode ─────────────────────────────────────────────────────────────

TAILSCALE_BIN="$(find_tailscale || true)"
if [ -z "$TAILSCALE_BIN" ]; then
  if [ "$OS_NAME" = "Darwin" ]; then
    echo "Error: Tailscale CLI not found."
    echo "  Install Tailscale for macOS from https://tailscale.com/download"
    echo "  Or with Homebrew: brew install --cask tailscale"
  else
    echo "Error: tailscale command not found. Install Tailscale, or set TUNNEL_MODE=cloudflare in config.sh."
  fi
  exit 1
fi

if ! "$TAILSCALE_BIN" ip -4 &>/dev/null; then
  if [ "$OS_NAME" = "Darwin" ]; then
    echo "Error: Tailscale is not connected. Open the Tailscale app, sign in, then retry."
  else
    echo "Error: Tailscale not connected. Run 'tailscale up', or set TUNNEL_MODE=cloudflare in config.sh."
  fi
  exit 1
fi

URL="https://$TAILSCALE_HOST:8765"

# Provision TLS cert.
CERT_OK=false
CERT_ERR=""
if "$TAILSCALE_BIN" cert --cert-file "$SCRIPT_DIR/clio.crt" --key-file "$SCRIPT_DIR/clio.key" "$TAILSCALE_HOST" 2>/dev/null; then
  CERT_OK=true
elif [ "$OS_NAME" != "Darwin" ] && sudo "$TAILSCALE_BIN" cert --cert-file "$SCRIPT_DIR/clio.crt" --key-file "$SCRIPT_DIR/clio.key" "$TAILSCALE_HOST" 2>/dev/null; then
  sudo chown "$(id -u):$(id -g)" "$SCRIPT_DIR/clio.crt" "$SCRIPT_DIR/clio.key" 2>/dev/null
  CERT_OK=true
elif [ "$OS_NAME" != "Darwin" ]; then
  SNAP_CERT_DIR="$HOME/snap/tailscale/common"
  CERT_ERR=$("$TAILSCALE_BIN" cert --cert-file "$SNAP_CERT_DIR/clio.crt" --key-file "$SNAP_CERT_DIR/clio.key" "$TAILSCALE_HOST" 2>&1)
  if [ $? -eq 0 ]; then
    cp "$SNAP_CERT_DIR/clio.crt" "$SCRIPT_DIR/clio.crt"
    cp "$SNAP_CERT_DIR/clio.key" "$SCRIPT_DIR/clio.key"
    CERT_OK=true
  fi
else
  CERT_ERR=$("$TAILSCALE_BIN" cert --cert-file "$SCRIPT_DIR/clio.crt" --key-file "$SCRIPT_DIR/clio.key" "$TAILSCALE_HOST" 2>&1 || true)
fi

if ! $CERT_OK; then
  echo "Error: could not provision TLS cert."
  echo "  Tailscale error: $CERT_ERR"
  echo "  Check that TAILSCALE_HOST in config.sh matches your machine's Tailscale hostname."
  exit 1
fi

echo "Starting Clio..."
echo ""
echo "  Open on your phone: $URL"
echo ""

notify_ntfy "$URL"
run_server_loop --host 0.0.0.0 --port 8765 --ssl-certfile clio.crt --ssl-keyfile clio.key
