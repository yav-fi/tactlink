#!/usr/bin/env bash
# Starts the DFlash2-accelerated chat backend (chad serve) as a local sidecar.
#
# Uses mlx-community/Qwen3.8-27B-4bit (this project's models/qwen3.8-27b-4bit,
# see scripts/download_model.sh) as the target, with the matching w4:gs64
# DFlash2 draft head (see scripts/build_dflash_draft.sh) — the benchmarked,
# corrected number for this exact pairing is ~65 tok/s decode, not the ~100
# tok/s chad's own default bundled (different) model reports.
#
# LibraSpec (an extra decode-speed algorithm on top of DFlash2) is applied
# over the public `chad-code` package (see requirements.txt) via the small
# file overrides in patches/chad/ — no private repo or extra install needed,
# this works out of the box for anyone who installs requirements.txt.
set -euo pipefail

PORT="${CHAT_SERVER_PORT:-8081}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

export CHAD_MODEL="${CHAD_MODEL:-$PROJECT_ROOT/models/qwen3.8-27b-4bit}"
export CHAD_DFLASH_PATH="${CHAD_DFLASH_PATH:-$PROJECT_ROOT/models/dflash2-w4gs64}"
export CHAD_LIBRASPEC="${CHAD_LIBRASPEC:-1}"
export CHAD_LIBRASPEC_ALPHA="${CHAD_LIBRASPEC_ALPHA:-8.0}"

if [ ! -e "$CHAD_MODEL" ]; then
  echo "Target model not found at $CHAD_MODEL — run scripts/download_model.sh first." >&2
  exit 1
fi
if [ ! -e "$CHAD_DFLASH_PATH" ]; then
  echo "DFlash2 draft not found at $CHAD_DFLASH_PATH — run scripts/build_dflash_draft.sh first." >&2
  exit 1
fi

CHAD_PKG_DIR="$(python3 -c "import chad, os; print(os.path.dirname(chad.__file__))" 2>/dev/null || true)"
if [ -z "$CHAD_PKG_DIR" ]; then
  echo "chad-code not installed. Install it with: pip install -r requirements.txt" >&2
  exit 1
fi

cp "$PROJECT_ROOT/patches/chad/engine.py" "$CHAD_PKG_DIR/engine.py"
cp "$PROJECT_ROOT/patches/chad/mlx_dflash.py" "$CHAD_PKG_DIR/mlx_dflash.py"
cp "$PROJECT_ROOT/patches/chad/wide_block.py" "$CHAD_PKG_DIR/wide_block.py"

exec chad-code serve --port "$PORT"
