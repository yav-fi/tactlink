"""End-to-end deterministic failure/reallocation demonstration."""

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
            priority=90,
            required_capabilities={"camera"},
            desired_units=2,
            minimum_units=1,
        )
    )
    engine.submit_mission(
        MissionCommand(
            type=TaskType.SEARCH,
            target=MissionTarget(region_id="BRAVO"),
            priority=95,
            required_capabilities={"mapping"},
            desired_units=2,
            minimum_units=1,
        )
    )
    last_event = 0
    print(f"seed={args.seed} scenario={engine.scenario} drones=4")
    for _ in range(260):
        if abs(engine.time - 10.0) < 0.05:
            engine.fail_drone("drone-2", source="demo")
        if abs(engine.time - 17.0) < 0.05:
            engine.inject_event(
                ScenarioEvent(timestamp=engine.time, type="GPS_OUTAGE", affected_nodes=["drone-3"], severity=1, duration=5)
            )
        if abs(engine.time - 22.0) < 0.05:
            config = engine.interference.config.model_copy(update={"network_interference": 0.55})
            engine.set_interference(config)
        snapshot = engine.tick(0.1)
        for event in engine.events.recent(100, after_sequence=last_event):
            print(f"t={event.timestamp:05.1f} {event.event_type:<28} {event.human_readable_summary}")
            last_event = event.sequence
        if args.realtime:
            time.sleep(0.1)
    print(f"final mission capability: {snapshot.mission_capability:.0%}")


if __name__ == "__main__":
    main()
