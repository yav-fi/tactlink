#!/usr/bin/env bash
# Builds the DFlash2 draft head (w4:gs64) used by scripts/start_chat_server.sh.
#
# Downloads the public z-lab/Qwen3.8-27B-DFlash2 (bf16) checkpoint and converts
# it to a quantized 4-bit, group-size-64 sidecar via chad's own conversion tool
# (chad.mlx_dflash). One-time, reproducible from a public repo — no access to
# any private research repo required.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUT_DIR="$PROJECT_ROOT/models/dflash2-w4gs64"
SRC_REPO="z-lab/Qwen3.8-27B-DFlash2"
SRC_DIR="$PROJECT_ROOT/models/.dflash2-bf16-src"

if ! command -v huggingface-cli >/dev/null 2>&1; then
  echo "huggingface-cli not found. Install it with: pip install -U huggingface_hub" >&2
  exit 1
fi
if ! python3 -c "import chad.mlx_dflash" >/dev/null 2>&1; then
  echo "chad package not importable. Install it with: pip install chad-code" >&2
  exit 1
fi

mkdir -p "$SRC_DIR"
huggingface-cli download "$SRC_REPO" --local-dir "$SRC_DIR"

python3 -m chad.mlx_dflash "$SRC_DIR" --out "$OUT_DIR" --bits 4 --group-size 64

echo "DFlash2 w4:gs64 draft built at $OUT_DIR"
