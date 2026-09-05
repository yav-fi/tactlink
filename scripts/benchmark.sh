#!/usr/bin/env bash
# Ensures the chat server is up (starting it if needed), then runs the
# thermal-gated tok/s benchmark. See scripts/benchmark.py for what it
# measures and README.md for the one-time mlx-bench clone this needs.
#
# Usage: ./scripts/benchmark.sh [--model qwen3.5-9b|qwen3.8-27b] [--reps N ...]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/model_registry.sh"

MODEL="qwen3.5-9b"
if [ "${1:-}" = "--model" ]; then
  MODEL="$2"
  shift 2
fi
resolve_model "$MODEL"

PORT="${CHAT_SERVER_PORT:-8081}"
URL="http://localhost:$PORT"

if ! curl -sf "$URL/health" >/dev/null 2>&1; then
  echo "starting chat server..." >&2
  nohup env PYTHONUNBUFFERED=1 "$SCRIPT_DIR/start_chat_server.sh" --model "$MODEL" \
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

exec env CHAT_SERVER_URL="$URL" CHAT_MODEL_DIR="${CHAT_MODEL_DIR:-$PROJECT_ROOT/$TARGET_DIR}" \
  python3 "$SCRIPT_DIR/benchmark.py" "$@"
