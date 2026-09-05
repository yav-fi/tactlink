"""Seeded end-to-end distributed mission survival demonstration."""

from __future__ import annotations

import argparse
import time

from .config import AllocatorWeights, SimulationConfig
from .models import MissionCommand, MissionTarget, ScenarioEvent, TaskType
from .scenarios import ScenarioPreset
from .simulation import SimulationEngine


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--realtime", action="store_true", help="sleep between accelerated demo ticks")
    parser.add_argument("--seed", type=int, default=49281)
    parser.add_argument("--duration", type=float, default=36.0)
    args = parser.parse_args()
    engine = SimulationEngine(
        SimulationConfig(seed=args.seed, tick_rate_hz=10, allocator=AllocatorWeights(workload=100)),
        scenario=ScenarioPreset.NORMAL,
        drone_count=4,
    )
    engine.submit_mission(
        MissionCommand(
            type=TaskType.WATCH,
            target=MissionTarget(region_id="ALPHA"),
            priority=30,
            required_capabilities={"camera"},
            desired_units=2,
            minimum_units=1,
        )
    )
    engine.submit_mission(
        MissionCommand(
            type=TaskType.SEARCH,
            target=MissionTarget(region_id="BRAVO"),
            priority=90,
            required_capabilities={"mapping"},
            desired_units=2,
            minimum_units=1,
        )
    )
    last_event = 0
    print(f"seed={args.seed} scenario={engine.scenario} drones=4 cloud=not-required")
    steps = int(args.duration * 10)
    failed_after_control: str | None = None
    for _ in range(steps):
        if abs(engine.time - 4.0) < 0.05:
            config = engine.interference.config.model_copy(update={"network_interference": 0.55})
            engine.set_interference(config)
        if abs(engine.time - 14.0) < 0.05:
            engine.fail_control()
        if abs(engine.time - 18.0) < 0.05:
            assigned = [
                node_id
                for task in engine.missions.tasks.values()
                if task.type != TaskType.RELAY
                for node_id in task.assigned_nodes
                if engine.world.is_online(node_id)
            ]
            failed_after_control = sorted(set(assigned))[0] if assigned else "drone-2"
            engine.fail_drone(failed_after_control, source="killer-demo")
        if abs(engine.time - 24.0) < 0.05:
            gps_node = "drone-3" if engine.world.is_online("drone-3") else min(engine._online_node_ids())
            engine.inject_event(
                ScenarioEvent(timestamp=engine.time, type="GPS_OUTAGE", affected_nodes=[gps_node], severity=1, duration=20)
            )
        snapshot = engine.tick(0.1)
        for event in engine.events.recent(100, after_sequence=last_event):
            print(f"t={event.timestamp:05.1f} {event.event_type:<28} {event.human_readable_summary}")
            last_event = event.sequence
        if round(engine.time * 10) % 10 == 0:
            metrics = snapshot.network
            print(
                f"t={engine.time:05.1f} capability={snapshot.mission_capability:4.0%} "
                f"network={metrics.network_health:4.0%} components={len(metrics.connected_components)} "
                f"control={'online' if snapshot.control_available else 'offline'} "
                f"active={metrics.active_nodes}/4 relay={','.join(metrics.relay_nodes) or '-'}"
            )
        if args.realtime:
            time.sleep(0.1)
    print(
        f"final mission capability={snapshot.mission_capability:.0%} "
        f"network={snapshot.network.network_health:.0%} control={'online' if snapshot.control_available else 'offline'} "
        f"failed_after_control={failed_after_control or '-'} gps_degraded={snapshot.network.gps_degraded_count}"
    )


if __name__ == "__main__":
    main()
