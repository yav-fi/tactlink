#!/usr/bin/env bash
# Ensures the chat server is up (starting it if needed), then runs the
# thermal-gated tok/s benchmark. See scripts/benchmark.py for what it
# measures and README.md for the one-time mlx-bench clone this needs.
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

exec env CHAT_SERVER_URL="$URL" python3 "$SCRIPT_DIR/benchmark.py" "$@"
