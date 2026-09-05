#!/usr/bin/env bash
# Starts the DFlash-accelerated chat backend (chad serve) as a local sidecar.
#
# Usage: ./scripts/start_chat_server.sh [--model qwen3.5-9b|qwen3.8-27b]
# Default: qwen3.5-9b. See scripts/model_registry.sh for the target/draft
# pairing — download/build the pair first with scripts/download_model.sh
# and scripts/build_dflash_draft.sh (same --model flag).
#
# LibraSpec (an extra decode-speed algorithm on top of DFlash2's candidate
# selector) is applied over the public `chad-code` package (see
# requirements.txt) via the small file overrides in patches/chad/ — no
# private repo or extra install needed, this works out of the box for
# anyone who installs requirements.txt. It's a documented no-op (and a pure
# overhead cost, no upside) on a plain DFlash v1 draft with no selector, so
# it's auto-disabled whenever the resolved model's draft isn't DFlash2 —
# override by setting CHAD_LIBRASPEC yourself before running this script.
set -euo pipefail

PORT="${CHAT_SERVER_PORT:-8081}"
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

export CHAD_MODEL="${CHAD_MODEL:-$PROJECT_ROOT/$TARGET_DIR}"
export CHAD_DFLASH_PATH="${CHAD_DFLASH_PATH:-$PROJECT_ROOT/$DRAFT_DIR}"

if [ -z "${CHAD_LIBRASPEC+x}" ]; then
  if [ "$DRAFT_GEN" = "dflash2" ]; then
    export CHAD_LIBRASPEC=1
    export CHAD_LIBRASPEC_ALPHA="${CHAD_LIBRASPEC_ALPHA:-8.0}"
  else
    echo "note: $MODEL's draft has no DFlash2 candidate selector, so LibraSpec" >&2
    echo "      has no confidence signal to trim on (mathematically a no-op that" >&2
    echo "      still pays its per-round sync cost) — leaving it disabled." >&2
  fi
fi

if [ ! -e "$CHAD_MODEL" ]; then
  echo "Target model not found at $CHAD_MODEL — run: scripts/download_model.sh --model $MODEL" >&2
  exit 1
fi
if [ ! -e "$CHAD_DFLASH_PATH" ]; then
  echo "DFlash draft not found at $CHAD_DFLASH_PATH — run: scripts/build_dflash_draft.sh --model $MODEL" >&2
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
