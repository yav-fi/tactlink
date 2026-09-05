#!/usr/bin/env bash
# Builds the DFlash draft head (w4:gs64) used by scripts/start_chat_server.sh.
#
# Downloads the public z-lab draft checkpoint (bf16) matching --model and
# converts it to a quantized 4-bit, group-size-64 sidecar via chad's own
# conversion tool (chad.mlx_dflash). One-time, reproducible from a public
# repo — no access to any private research repo required.
#
# Usage: ./scripts/build_dflash_draft.sh [--model qwen3.5-9b|qwen3.8-27b]
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
OUT_DIR="$PROJECT_ROOT/$DRAFT_DIR"
SRC_DIR="$PROJECT_ROOT/models/.dflash-bf16-src-$MODEL"

if command -v hf >/dev/null 2>&1; then
  DL=(hf download)
elif command -v huggingface-cli >/dev/null 2>&1; then
  DL=(huggingface-cli download)
else
  echo "hf/huggingface-cli not found. Install it with: pip install -U huggingface_hub" >&2
  exit 1
fi
if ! python3 -c "import chad.mlx_dflash" >/dev/null 2>&1; then
  echo "chad package not importable. Install it with: pip install chad-code" >&2
  exit 1
fi

mkdir -p "$SRC_DIR"
"${DL[@]}" "$DRAFT_SRC_REPO" --local-dir "$SRC_DIR"

python3 -m chad.mlx_dflash "$SRC_DIR" --out "$OUT_DIR" --bits 4 --group-size 64

echo "DFlash ($DRAFT_GEN) w4:gs64 draft for $MODEL built at $OUT_DIR"
