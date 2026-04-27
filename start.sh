#!/usr/bin/env bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

TAILSCALE_IP=$(tailscale ip -4 2>/dev/null || echo "")
if [ -z "$TAILSCALE_IP" ]; then
  echo "Warning: Tailscale not connected."
  exit 1
fi

CONFIG="$SCRIPT_DIR/config.sh"
if [ ! -f "$CONFIG" ]; then
  echo "Error: config.sh not found. Copy config.sh.example to config.sh and fill it in."
  exit 1
fi
source "$CONFIG"

URL="https://$TAILSCALE_HOST:8765"

# Provision/renew Tailscale TLS cert (snap sandbox can only write to its own dirs)
SNAP_CERT_DIR="$HOME/snap/tailscale/common"
tailscale cert --cert-file "$SNAP_CERT_DIR/clio.crt" --key-file "$SNAP_CERT_DIR/clio.key" "$TAILSCALE_HOST"
cp "$SNAP_CERT_DIR/clio.crt" "$SCRIPT_DIR/clio.crt"
cp "$SNAP_CERT_DIR/clio.key" "$SCRIPT_DIR/clio.key"

source .venv/bin/activate

echo "Starting Clio..."
echo ""
echo "  Open on your phone: $URL"
echo ""

# Push URL to phone
curl -s -X POST "https://ntfy.sh/$NTFY_TOPIC" \
  -H "Title: Clio is ready" \
  -H "Priority: default" \
  -H "Actions: view, Open Clio, $URL" \
  -d "$URL" > /dev/null 2>&1 &

# Restart loop — exit code 0 means clean stop (Ctrl+C), anything else means restart
while true; do
  uvicorn laptop.main:app \
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
