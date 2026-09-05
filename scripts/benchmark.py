#!/usr/bin/env python3
"""Thermal-gated tok/s benchmark for this project's chat backend.

The shell wrapper resolves the requested target/draft pair and passes its
identity here so every result remains attributable to the model that produced
it. This is a single-configuration measurement, not a sweep.

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


def _stage(name: str):
    print(f"[stage] {name}...", file=sys.stderr, flush=True)


def _stage_ok(name: str, detail: str = "") -> None:
    suffix = f" ({detail})" if detail else ""
    print(f"[stage] {name}: OK{suffix}", file=sys.stderr, flush=True)


def _stage_failed(name: str, err: Exception) -> None:
    print(f"[stage] {name}: FAILED — {err}", file=sys.stderr, flush=True)


def _load_thermal():
    thermal_core = os.path.join(MLX_BENCH_DIR, "core")
    if not os.path.isdir(thermal_core):
        err = FileNotFoundError(
            f"mlx-bench not found at {MLX_BENCH_DIR}. Clone it once (public repo, no "
            f"access needed): git clone https://github.com/kaarelkaarelson/mlx-bench.git "
            f"{MLX_BENCH_DIR} — or set MLX_BENCH_DIR to point at an existing clone."
        )
        _stage_failed("load thermal gate module", err)
        raise err
    sys.path.insert(0, thermal_core)
    import thermal  # type: ignore

    return thermal


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--max-tokens", type=int, default=512)
    ap.add_argument("--base-url", default=os.environ.get("CHAT_SERVER_URL", "http://localhost:8081"))
    ap.add_argument("--out", default=str(PROJECT_ROOT / "benchmark_result.json"))
    ap.add_argument("--model", default=os.environ.get("BENCHMARK_MODEL", "unknown"))
    ap.add_argument("--target-repo", default=os.environ.get("BENCHMARK_TARGET_REPO", "unknown"))
    ap.add_argument("--draft-repo", default=os.environ.get("BENCHMARK_DRAFT_REPO", "unknown"))
    args = ap.parse_args()

    _stage("load thermal gate module")
    thermal = _load_thermal()
    _stage_ok("load thermal gate module", MLX_BENCH_DIR)

    _stage("read matched prompt")
    try:
        prompt_text = PROMPT_FILE.read_text().strip()
    except Exception as e:
        _stage_failed("read matched prompt", e)
        raise
    _stage_ok("read matched prompt", f"{len(prompt_text)} chars from {PROMPT_FILE.name}")

    # Untimed, discarded warmup: absorbs one-time costs (prefix cache setup,
    # lazy kernel compilation) that would otherwise land on rep 1 and skew it
    # — see dflash2-mlx's http_api_bench.py, which hit exactly this once.
    _stage("warmup (untimed, discarded)")
    try:
        chat_sync(prompt_text, n_predict=8, base_url=args.base_url)
    except Exception as e:
        _stage_failed("warmup", e)
        raise
    _stage_ok("warmup")

    reps = []
    for i in range(1, args.reps + 1):
        label = f"rep{i}"
        _stage(f"{label}: thermal-gated measurement")

        def _do():
            return chat_sync(prompt_text, n_predict=args.max_tokens, base_url=args.base_url)

        try:
            result, elapsed_s, receipt = thermal.gated_run(label, _do)
        except Exception as e:
            _stage_failed(f"{label}: thermal-gated measurement", e)
            raise
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
        if not decode_tps:
            err = RuntimeError(f"{label}: server returned no decode timing")
            _stage_failed(f"{label}: thermal-gated measurement", err)
            raise err
        _stage_ok(f"{label}: thermal-gated measurement",
                  f"{predicted_n} tok in {predicted_ms / 1000:.2f}s -> {decode_tps:.1f} tok/s decode")

    _stage("aggregate results")
    decode_values = [r["decode_tok_s"] for r in reps if r["decode_tok_s"] is not None]
    if not decode_values:
        err = RuntimeError("no valid decode measurements collected")
        _stage_failed("aggregate results", err)
        raise err
    median_tps = statistics.median(decode_values)
    _stage_ok("aggregate results", f"{len(decode_values)}/{args.reps} rep(s) valid")

    print(f"\ndecode tok/s per rep: {[round(v, 1) for v in decode_values]}")
    print(f"median decode tok/s: {median_tps:.1f}")

    _stage("write results file")
    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "model": args.model,
        "target_repo": args.target_repo,
        "draft_repo": args.draft_repo,
        "base_url": args.base_url,
        "max_tokens": args.max_tokens,
        "prompt_file": str(PROMPT_FILE),
        "reps": reps,
        "median_decode_tok_s": median_tps,
    }
    try:
        Path(args.out).write_text(json.dumps(summary, indent=2))
    except Exception as e:
        _stage_failed("write results file", e)
        raise
    _stage_ok("write results file", args.out)


if __name__ == "__main__":
    main()
