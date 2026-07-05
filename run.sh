#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

# Activate venv if exists
if [ -f .venv/Scripts/activate ]; then
  source .venv/Scripts/activate
elif [ -f .venv/bin/activate ]; then
  source .venv/bin/activate
fi

echo "Starting Gumloop Bridge Proxy..."
echo "  HTTP API:  http://127.0.0.1:8082  (for Claude Code)"
echo "  WS Bridge: ws://127.0.0.1:8083    (for Chrome Extension)"
echo ""

python proxy/gumloop_bridge.py
