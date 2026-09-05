#!/usr/bin/env python3
"""Thermal-gated tok/s benchmark for this project's chat backend.

This project only ships one configuration — mlx-community/Qwen3.8-27B-4bit
target + w4:gs64 DFlash2 draft + LibraSpec, the best-performing setup found
in dflash2-mlx's own matched comparisons — so this benchmarks whatever's
currently running, not a sweep across configs.

Reuses this machine family's one canonical thermal-gate implementation from
https://github.com/kaarelkaarelson/mlx-bench (public, MIT) instead of
reimplementing gating a third time (dartree-mlx does the same import). See
README.md for the one-time `git clone` step.

Usage:
    ./scripts/benchmark.py [--reps 1] [--max-tokens 512] [--base-url http://localhost:8081]
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
from chat_client import chat_sync  # noqa: E402

PROMPT_FILE = Path(__file__).parent / "matched_prompt.txt"
MLX_BENCH_DIR = os.environ.get("MLX_BENCH_DIR", os.path.expanduser("~/mlx-bench"))


def _load_thermal():
    thermal_core = os.path.join(MLX_BENCH_DIR, "core")
    if not os.path.isdir(thermal_core):
        print(
            f"error: mlx-bench not found at {MLX_BENCH_DIR}\n"
            f"Clone it once (public repo, no access needed):\n"
            f"  git clone https://github.com/kaarelkaarelson/mlx-bench.git {MLX_BENCH_DIR}\n"
            f"Or set MLX_BENCH_DIR to point at an existing clone.",
            file=sys.stderr,
        )
        sys.exit(1)
    sys.path.insert(0, thermal_core)
    import thermal  # type: ignore

    return thermal


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--max-tokens", type=int, default=512)
    ap.add_argument("--base-url", default=os.environ.get("CHAT_SERVER_URL", "http://localhost:8081"))
    ap.add_argument("--out", default=str(PROJECT_ROOT / "benchmark_result.json"))
    args = ap.parse_args()

    thermal = _load_thermal()
    prompt_text = PROMPT_FILE.read_text().strip()

    # Untimed, discarded warmup: absorbs one-time costs (prefix cache setup,
    # lazy kernel compilation) that would otherwise land on rep 1 and skew it
    # — see dflash2-mlx's http_api_bench.py, which hit exactly this once.
    print("warmup (untimed, discarded)...", file=sys.stderr)
    chat_sync(prompt_text, n_predict=8, base_url=args.base_url)

    reps = []
    for i in range(1, args.reps + 1):
        label = f"rep{i}"

        def _do():
            return chat_sync(prompt_text, n_predict=args.max_tokens, base_url=args.base_url)

        result, elapsed_s, receipt = thermal.gated_run(label, _do)
        t = result.timings
        prompt_n, prompt_ms = t.get("prompt_n", 0), t.get("prompt_ms", 0)
        predicted_n, predicted_ms = t.get("predicted_n", 0), t.get("predicted_ms", 0)
        prefill_tps = (prompt_n / (prompt_ms / 1000)) if prompt_ms else None
        decode_tps = (predicted_n / (predicted_ms / 1000)) if predicted_ms else None

        reps.append({
            "rep": i,
            "prompt_tokens": prompt_n,
            "generated_tokens": predicted_n,
            "prefill_tok_s": prefill_tps,
            "decode_tok_s": decode_tps,
            "wall_s": elapsed_s,
            "thermal_receipt": receipt.to_dict(),
        })
        if decode_tps:
            print(f"[{label}] {predicted_n} tok in {predicted_ms / 1000:.2f}s -> {decode_tps:.1f} tok/s decode")
        else:
            print(f"[{label}] no decode timing")

    decode_values = [r["decode_tok_s"] for r in reps if r["decode_tok_s"] is not None]
    if not decode_values:
        print("no valid decode measurements collected", file=sys.stderr)
        sys.exit(1)
    median_tps = statistics.median(decode_values)

    print(f"\ndecode tok/s per rep: {[round(v, 1) for v in decode_values]}")
    print(f"median decode tok/s: {median_tps:.1f}")

    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "base_url": args.base_url,
        "max_tokens": args.max_tokens,
        "prompt_file": str(PROMPT_FILE),
        "reps": reps,
        "median_decode_tok_s": median_tps,
    }
    Path(args.out).write_text(json.dumps(summary, indent=2))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
