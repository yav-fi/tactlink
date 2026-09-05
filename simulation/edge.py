"""Standalone edge compute node: advertise real resources, run real work units.

Run this on a spare laptop to contribute compute to a live runtime::

    python -m simulation.edge --node-id EDGE-1 --port 8770

It measures the host's actual CPU capacity rather than asserting a number, then
serves bounded ``WorkUnit`` jobs.  Nothing here is required for the mission: if
the process dies, the host reassigns the work and keeps flying.
"""

from __future__ import annotations

import argparse
import json
import os
import time

from websockets.exceptions import ConnectionClosed
from websockets.sync.server import ServerConnection, serve

from .workunits import EdgeResourceProfile
from .worker import EdgeRuntime


def measured_cpu_score(iterations: int = 200_000) -> float:
    """A tiny, real arithmetic benchmark, normalized so a typical core is ~1.0.

    Advertising a *measured* number keeps the placement decision honest: the
    broker prefers a machine that actually ran the loop faster.
    """

    started = time.perf_counter()
    total = 0.0
    for index in range(iterations):
        total += (index % 7) * 1.000_001
    elapsed = max(1e-6, time.perf_counter() - started)
    # 200k iterations in ~10ms on a modern core -> score ~1.0 per core.
    per_core = 0.010 / elapsed
    return round(per_core * max(1, os.cpu_count() or 1), 3)


def available_memory_mb() -> int:
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return int(pages * page_size / (1024 * 1024))
    except (ValueError, OSError, AttributeError):  # pragma: no cover - platform dependent
        return 2048


def detect_gpu() -> bool:
    """Best-effort, dependency-free GPU presence check."""

    if os.environ.get("DNHACKS_EDGE_GPU"):
        return os.environ["DNHACKS_EDGE_GPU"].lower() in {"1", "true", "yes"}
    for path in ("/dev/nvidia0", "/dev/dri/renderD128"):
        if os.path.exists(path):
            return True
    try:  # Apple silicon exposes Metal through mlx when it is installed.
        import mlx.core  # noqa: F401

        return True
    except Exception:
        return False


def build_profile(node_id: str, tags: list[str], concurrency: int) -> EdgeResourceProfile:
    return EdgeResourceProfile(
        node_id=node_id,
        cpu_score=measured_cpu_score(),
        gpu_available=detect_gpu(),
        memory_mb=available_memory_mb(),
        tags=set(tags) | {"edge"},
        concurrency=concurrency,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node-id", default="EDGE-1")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--tag", action="append", default=[], help="advertise a capability tag")
    parser.add_argument("--concurrency", type=int, default=1)
    args = parser.parse_args()

    profile = build_profile(args.node_id, args.tag, args.concurrency)
    runtime = EdgeRuntime(profile)

    def handler(connection: ServerConnection) -> None:
        try:
            for raw in connection:
                try:
                    connection.send(json.dumps(runtime.handle(json.loads(raw))))
                except Exception as exc:
                    connection.send(json.dumps({"kind": "error", "error": str(exc)}))
        except ConnectionClosed:
            return

    print(
        f"{profile.node_id} JOINED  cpu_score={profile.cpu_score} "
        f"gpu={'yes' if profile.gpu_available else 'no'} memory={profile.memory_mb}MB "
        f"tags={sorted(profile.tags)}"
    )
    print(f"listening on ws://{args.host}:{args.port}")
    with serve(handler, args.host, args.port) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
