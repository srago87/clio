#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Stopping Clio..."
pkill -f "uvicorn server.main:app" || echo "No running instance found."

sleep 1

exec "$SCRIPT_DIR/start.sh"
