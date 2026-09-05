#!/usr/bin/env bash
# One-shot, ollama-`run`-style entry point: ensures the chat server is up
# (starting it in the background if needed), then asks it a question.
#
# Usage: ./scripts/ask.sh "what's 17 * 23?"
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${CHAT_SERVER_PORT:-8081}"
URL="http://localhost:$PORT"

if [ $# -eq 0 ]; then
  echo "usage: $0 \"your question\"" >&2
  exit 1
fi

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
CHAT_SERVER_URL="$URL" PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 -c "
import sys
from chat_client import chat_sync
print(chat_sync(sys.argv[1]).text)
" "$*"
