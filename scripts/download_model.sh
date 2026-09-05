#!/usr/bin/env bash
# Downloads the project's Qwen3.8-27B-4bit MLX weights into models/.
# Each team member runs this locally; the weights are gitignored and never committed.
set -euo pipefail

REPO_ID="mlx-community/Qwen3.8-27B-4bit"
TARGET_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/models/qwen3.8-27b-4bit"

if ! command -v huggingface-cli >/dev/null 2>&1; then
  echo "huggingface-cli not found. Install it with: pip install -U huggingface_hub" >&2
  exit 1
fi

mkdir -p "$TARGET_DIR"
huggingface-cli download "$REPO_ID" --local-dir "$TARGET_DIR"

echo "Model downloaded to $TARGET_DIR"
