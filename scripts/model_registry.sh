# Source this from another script: resolve_model "$MODEL_KEY" sets
# TARGET_REPO/TARGET_DIR/DRAFT_SRC_REPO/DRAFT_DIR/DRAFT_GEN. Single source of
# truth for which draft head pairs with which target, so download/build/serve
# scripts can't drift out of sync with each other.
#
# DRAFT_GEN is "dflash2" (has the candidate-selector confidence signal
# LibraSpec needs) or "dflash" (plain v1, no selector — LibraSpec is a
# documented no-op on this and gets auto-disabled).
resolve_model() {
  case "$1" in
    qwen3.5-9b)
      TARGET_REPO="mlx-community/Qwen3.5-9B-4bit"
      TARGET_DIR="models/qwen3.5-9b-4bit"
      DRAFT_SRC_REPO="z-lab/Qwen3.5-9B-DFlash"
      DRAFT_DIR="models/dflash-qwen3.5-9b-w4gs64"
      DRAFT_GEN="dflash"
      ;;
    qwen3.8-27b)
      TARGET_REPO="mlx-community/Qwen3.8-27B-4bit"
      TARGET_DIR="models/qwen3.8-27b-4bit"
      DRAFT_SRC_REPO="z-lab/Qwen3.8-27B-DFlash2"
      DRAFT_DIR="models/dflash2-w4gs64"
      DRAFT_GEN="dflash2"
      ;;
    *)
      echo "unknown --model '$1' — supported: qwen3.5-9b, qwen3.8-27b" >&2
      exit 1
      ;;
  esac
}
