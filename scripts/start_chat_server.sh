#!/usr/bin/env bash
# Starts the DFlash2-accelerated chat backend (chad serve) as a local sidecar.
#
# Uses mlx-community/Qwen3.8-27B-4bit (this project's models/qwen3.8-27b-4bit,
# see scripts/download_model.sh) as the target, with the matching w4:gs64
# DFlash2 draft head (see scripts/build_dflash_draft.sh) — the benchmarked,
# corrected number for this exact pairing is ~65 tok/s decode (see
# dflash2-mlx/README.md's "Cross-runtime comparison" table), not the ~100 tok/s
# chad's own default bundled (different) model reports.
#
# If a local checkout of the LibraSpec-enabled chad fork is present
# (CHAD_REPO_DIR, default ~/dflash2-mlx/runtime/chad — a private repo), it is
# used instead of the public chad-code package: LibraSpec (CHAD_LIBRASPEC=1)
# pushes the same pairing to ~66 tok/s. Falls back to the public `chad-code`
# PyPI package (no LibraSpec) when that checkout isn't present.
set -euo pipefail

PORT="${CHAT_SERVER_PORT:-8081}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CHAD_REPO_DIR="${CHAD_REPO_DIR:-$HOME/dflash2-mlx/runtime/chad}"

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

if [ -d "$CHAD_REPO_DIR" ]; then
  echo "using LibraSpec-enabled chad at $CHAD_REPO_DIR" >&2
  exec uv run --project "$CHAD_REPO_DIR" chad-code serve --port "$PORT"
fi

if ! command -v uvx >/dev/null 2>&1; then
  echo "uvx not found. Install it with: pip install -U uv" >&2
  exit 1
fi
echo "LibraSpec fork not found at $CHAD_REPO_DIR — falling back to public chad-code (no LibraSpec)." >&2
unset CHAD_LIBRASPEC CHAD_LIBRASPEC_ALPHA
exec uvx chad-code serve --port "$PORT"
