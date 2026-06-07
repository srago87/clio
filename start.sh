#!/usr/bin/env bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

CONFIG="$SCRIPT_DIR/config.sh"
if [ ! -f "$CONFIG" ]; then
  echo "Error: config.sh not found. Copy config.sh.example to config.sh and fill it in."
  exit 1
fi
source "$CONFIG"

TUNNEL_MODE="${TUNNEL_MODE:-tailscale}"

source .venv/bin/activate

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

  if [ -n "$NTFY_TOPIC" ]; then
    curl -s -X POST "https://ntfy.sh/$NTFY_TOPIC" \
      -H "Title: Clio is ready" \
      -H "Priority: default" \
      -H "Actions: view, Open Clio, $URL" \
      -d "$URL" >/dev/null 2>&1 &
  fi

  # Restart loop — cloudflared stays running across server restarts
  while true; do
    uvicorn server.main:app --host 127.0.0.1 --port 8765
    EXIT_CODE=$?
    if [ $EXIT_CODE -eq 0 ]; then
      echo "Clio stopped."
      kill "$CLOUDFLARE_PID" 2>/dev/null
      break
    fi
    echo "Clio exited with code $EXIT_CODE, restarting..."
    sleep 1
  done

  rm -f "$CLOUDFLARE_LOG"
  exit 0
fi

# ── Tailscale mode (default) ──────────────────────────────────────────────────

if ! tailscale ip -4 &>/dev/null; then
  echo "Error: Tailscale not connected. Run 'tailscale up', or set TUNNEL_MODE=cloudflare in config.sh."
  exit 1
fi

URL="https://$TAILSCALE_HOST:8765"

# Provision TLS cert — try writing directly to the project dir first.
# Falls back to /tmp/ for snap-installed Tailscale, which can't write to arbitrary paths.
CERT_OK=false
if tailscale cert --cert-file "$SCRIPT_DIR/clio.crt" --key-file "$SCRIPT_DIR/clio.key" "$TAILSCALE_HOST" 2>/dev/null; then
  CERT_OK=true
else
  TMP_CERT_DIR=$(mktemp -d)
  CERT_ERR=$(tailscale cert --cert-file "$TMP_CERT_DIR/clio.crt" --key-file "$TMP_CERT_DIR/clio.key" "$TAILSCALE_HOST" 2>&1)
  if [ $? -eq 0 ]; then
    cp "$TMP_CERT_DIR/clio.crt" "$SCRIPT_DIR/clio.crt"
    cp "$TMP_CERT_DIR/clio.key" "$SCRIPT_DIR/clio.key"
    CERT_OK=true
  fi
  rm -rf "$TMP_CERT_DIR"
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

if [ -n "$NTFY_TOPIC" ]; then
  curl -s -X POST "https://ntfy.sh/$NTFY_TOPIC" \
    -H "Title: Clio is ready" \
    -H "Priority: default" \
    -H "Actions: view, Open Clio, $URL" \
    -d "$URL" >/dev/null 2>&1 &
fi

# Restart loop — exit code 0 means clean stop (Ctrl+C), anything else means restart
while true; do
  uvicorn server.main:app \
    --host 0.0.0.0 \
    --port 8765 \
    --ssl-certfile clio.crt \
    --ssl-keyfile clio.key
  EXIT_CODE=$?
  if [ $EXIT_CODE -eq 0 ]; then
    echo "Clio stopped."
    break
  fi
  echo "Clio exited with code $EXIT_CODE, restarting..."
  sleep 1
done
