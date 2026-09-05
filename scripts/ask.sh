#!/usr/bin/env bash
# ollama-`run`-style entry point: ensures the chat server is up (starting it in
# the background if needed), then either asks it one question and streams the
# reply with a tok/s readout (args given), or drops into an interactive
# multi-turn chat REPL (no args) — mirrors `ollama run <model> ["prompt"]`.
#
# Usage: ./scripts/ask.sh "what's 17 * 23?"   # one-shot
#        ./scripts/ask.sh                     # interactive REPL
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${CHAT_SERVER_PORT:-8081}"
URL="http://localhost:$PORT"

if ! curl -sf "$URL/health" >/dev/null 2>&1; then
  echo "starting chat server..." >&2
  nohup env PYTHONUNBUFFERED=1 "$SCRIPT_DIR/start_chat_server.sh" \
    > /tmp/chad-serve.log 2>&1 &
  for _ in $(seq 1 120); do
    curl -sf "$URL/health" >/dev/null 2>&1 && break
    sleep 2
  done
  if ! curl -sf "$URL/health" >/dev/null 2>&1; then
    echo "server failed to come up — see /tmp/chad-serve.log" >&2
    exit 1
  fi
  echo "ready." >&2
fi

PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
exec env CHAT_SERVER_URL="$URL" python3 "$PROJECT_ROOT/chat_client.py" "$@"
