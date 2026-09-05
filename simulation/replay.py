"""JSONL experiment recording and deterministic timeline replay helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

from .models import MissionCommand, ScenarioEvent, SimulationSnapshot


class RunRecorder:
    def __init__(self, path: str | Path, sample_interval_seconds: float = 0.5) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.sample_interval_seconds = sample_interval_seconds
        self._last_sample = -sample_interval_seconds
        self._last_event_sequence = 0
        self._stream = self.path.open("w", encoding="utf-8")

    def write_header(self, *, seed: int, scenario: str, mode: str, config: dict[str, Any]) -> None:
        self._write({"record_type": "header", "seed": seed, "scenario": scenario, "mode": mode, "config": config})

    def record_mission(self, now: float, command: MissionCommand) -> None:
        self._write({"record_type": "mission_command", "time": now, "command": command.model_dump(mode="json")})

    def record_scenario_event(self, event: ScenarioEvent) -> None:
        self._write({"record_type": "scenario_event", "time": event.timestamp, "event": event.model_dump(mode="json")})

    def record_snapshot(self, snapshot: SimulationSnapshot) -> None:
        if snapshot.simulation_time - self._last_sample < self.sample_interval_seconds - 1e-9:
            return
        self._last_sample = snapshot.simulation_time
        self._write({"record_type": "snapshot", "time": snapshot.simulation_time, "state": snapshot.model_dump(mode="json")})

    def close(self) -> None:
        if not self._stream.closed:
            self._stream.flush()
            self._stream.close()

    def _write(self, record: dict[str, Any]) -> None:
        self._stream.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
        self._stream.flush()

    def __enter__(self) -> "RunRecorder":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class Replay:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def records(self) -> Iterator[dict[str, Any]]:
        with self.path.open(encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    yield json.loads(line)

    def snapshots(self) -> Iterator[SimulationSnapshot]:
        for record in self.records():
            if record.get("record_type") == "snapshot":
                yield SimulationSnapshot.model_validate(record["state"])

    def final_snapshot(self) -> SimulationSnapshot:
        final: SimulationSnapshot | None = None
        for final in self.snapshots():
            pass
        if final is None:
            raise ValueError(f"no snapshots in replay {self.path}")
        return final


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Inspect a recorded distributed-runtime timeline")
    parser.add_argument("path")
    args = parser.parse_args()
    replay = Replay(args.path)
    header = next(replay.records())
    final = replay.final_snapshot()
    print(f"seed={header.get('seed')} mode={header.get('mode')} scenario={header.get('scenario')}")
    print(
        f"t={final.simulation_time:.1f}s capability={final.mission_capability:.0%} "
        f"effectiveness={final.mission_effectiveness:.0%} network={final.network.network_health:.0%} "
        f"observations={final.world_knowledge.observation_count}"
    )


if __name__ == "__main__":
    main()
