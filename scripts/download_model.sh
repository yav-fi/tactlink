#!/usr/bin/env bash
# Downloads a target model's MLX weights into models/.
# Each team member runs this locally; the weights are gitignored and never committed.
#
# Usage: ./scripts/download_model.sh [--model qwen3.5-9b|qwen3.8-27b]
# Default: qwen3.5-9b
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/model_registry.sh"

MODEL="qwen3.5-9b"
while [ $# -gt 0 ]; do
  case "$1" in
    --model) MODEL="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 1 ;;
  esac
done
resolve_model "$MODEL"
TARGET_DIR="$PROJECT_ROOT/$TARGET_DIR"

if command -v hf >/dev/null 2>&1; then
  DL=(hf download)
elif command -v huggingface-cli >/dev/null 2>&1; then
  DL=(huggingface-cli download)
else
  echo "hf/huggingface-cli not found. Install it with: pip install -U huggingface_hub" >&2
  exit 1
fi

mkdir -p "$TARGET_DIR"
"${DL[@]}" "$TARGET_REPO" --local-dir "$TARGET_DIR"

echo "Model ($MODEL) downloaded to $TARGET_DIR"
