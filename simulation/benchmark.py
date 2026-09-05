"""Measured baseline-versus-resilient-fabric benchmark using one disturbance schedule."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from time import strftime

from .config import AllocatorWeights, SimulationConfig
from .models import MissionCommand, MissionTarget, ScenarioEvent, TaskType
from .replay import RunRecorder
from .scenarios import ScenarioPreset
from .simulation import SimulationEngine


def run_mode(mode: str, seed: int, duration: float, run_dir: Path) -> dict[str, object]:
    engine = SimulationEngine(
        SimulationConfig(seed=seed, tick_rate_hz=10, allocator=AllocatorWeights(workload=100)),
        scenario=ScenarioPreset.NORMAL,
        drone_count=4,
        mode=mode,
    )
    recorder_path = run_dir / f"{mode}.jsonl"
    recorder = RunRecorder(recorder_path)
    engine.attach_recorder(recorder)
    search = engine.submit_mission(
        MissionCommand(
            type=TaskType.SEARCH,
            target=MissionTarget(region_id="ALPHA"),
            priority=85,
            required_capabilities={"camera"},
            desired_units=2,
            minimum_units=1,
        )
    )
    engine.submit_mission(
        MissionCommand(
            type=TaskType.WATCH,
            target=MissionTarget(region_id="BRAVO"),
            priority=55,
            required_capabilities={"camera"},
            desired_units=1,
            minimum_units=1,
        )
    )
    recovery_after_loss: float | None = None
    failed_at = 24.0
    failed_node = "drone-3"
    network_recovery: float | None = None
    for step in range(int(duration * 10)):
        now = round(step / 10, 1)
        if now == 10.0:
            engine.set_interference(engine.interference.config.model_copy(update={"network_interference": 0.55}))
        elif now == 20.0:
            engine.fail_control()
        elif now == failed_at:
            engine.fail_drone(failed_node, source="benchmark")
        elif now == 32.0:
            engine.inject_event(ScenarioEvent(timestamp=now, type="NETWORK_PARTITION", affected_nodes=["drone-1", "drone-3"], severity=1, duration=6))
        elif now == 45.0:
            engine.inject_event(ScenarioEvent(timestamp=now, type="GPS_OUTAGE", affected_nodes=["drone-3"], severity=1, duration=10))
        snapshot = engine.tick(0.1)
        if recovery_after_loss is None and engine.time > failed_at:
            assigned = [node for node in search.assigned_nodes if engine.world.is_online(node)]
            if failed_node not in assigned and len(assigned) >= search.desired_units:
                recovery_after_loss = engine.time - failed_at
        if network_recovery is None and engine.time >= 10 and snapshot.network.network_health >= 0.7:
            network_recovery = engine.time - 10.0
    final = engine.snapshot(event_limit=500)
    recorder.record_snapshot(final)
    recorder.close()
    region = next((item for item in final.world_knowledge.regions if item.region_id == "ALPHA"), None)
    return {
        "mode": mode,
        "mission_effectiveness": final.mission_effectiveness,
        "mission_capability": final.mission_capability,
        "search_coverage": region.coverage if region else 0.0,
        "fresh_search_coverage": region.fresh_coverage if region else 0.0,
        "network_health": final.network.network_health,
        "largest_component_fraction": final.network.largest_component_fraction,
        "observation_count": final.world_knowledge.observation_count,
        "tasks_completed": sum(task.status == "COMPLETED" for task in final.missions),
        "recovery_after_node_loss_seconds": recovery_after_loss,
        "network_recovery_seconds": network_recovery,
        "control_result": "continued" if any(drone.truth.online and drone.current_task_id for drone in final.drones) else "halted",
        "replay": str(recorder_path),
    }


def benchmark(seed: int = 49281, duration: float = 55.0, output: str | Path | None = None) -> dict[str, object]:
    run_dir = Path("runs") / f"benchmark-{seed}-{strftime('%Y%m%d-%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    baseline = run_mode("baseline", seed, duration, run_dir)
    fabric = run_mode("fabric", seed, duration, run_dir)
    result: dict[str, object] = {"seed": seed, "duration_seconds": duration, "baseline": baseline, "fabric": fabric}
    output_path = Path(output) if output else run_dir / "comparison.json"
    output_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    result["artifact"] = str(output_path)
    return result


def _fmt(value: object, percent: bool = False) -> str:
    if value is None:
        return "not recovered"
    if percent:
        return f"{float(value):.0%}"
    if isinstance(value, float):
        return f"{value:.1f}s"
    return str(value)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=49281)
    parser.add_argument("--duration", type=float, default=55.0)
    parser.add_argument("--output")
    args = parser.parse_args()
    result = benchmark(args.seed, args.duration, args.output)
    baseline, fabric = result["baseline"], result["fabric"]
    print(f"seed={args.seed} duration={args.duration:.1f}s")
    print(f"{'METRIC':34} {'BASELINE':>14} {'FABRIC':>14}")
    for label, key, percent in [
        ("Mission effectiveness", "mission_effectiveness", True),
        ("Mission capability", "mission_capability", True),
        ("Search coverage", "search_coverage", True),
        ("Fresh coverage", "fresh_search_coverage", True),
        ("Network health", "network_health", True),
        ("Recovery after drone loss", "recovery_after_node_loss_seconds", False),
        ("Control loss", "control_result", False),
        ("Observations", "observation_count", False),
    ]:
        print(f"{label:34} {_fmt(baseline[key], percent):>14} {_fmt(fabric[key], percent):>14}")
    print(f"artifact={result['artifact']}")


if __name__ == "__main__":
    main()
