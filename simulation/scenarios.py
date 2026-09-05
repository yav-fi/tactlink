"""Seeded stochastic scenario presets that modify real simulation mechanisms."""

from __future__ import annotations

import random
from enum import StrEnum

from .models import InterferenceConfig, ScenarioEvent


class ScenarioPreset(StrEnum):
    NORMAL = "NORMAL"
    DEGRADED = "DEGRADED"
    CONTESTED = "CONTESTED"
    CHAOS = "CHAOS"


PRESET_INTERFERENCE: dict[ScenarioPreset, InterferenceConfig] = {
    ScenarioPreset.NORMAL: InterferenceConfig(
        gps_interference=0.02, network_interference=0.01, sensor_interference=0.01, node_failure_rate=0.0001
    ),
    ScenarioPreset.DEGRADED: InterferenceConfig(
        gps_interference=0.25, network_interference=0.18, sensor_interference=0.12, node_failure_rate=0.001
    ),
    ScenarioPreset.CONTESTED: InterferenceConfig(
        gps_interference=0.52, network_interference=0.42, sensor_interference=0.3, node_failure_rate=0.004
    ),
    ScenarioPreset.CHAOS: InterferenceConfig(
        gps_interference=0.78, network_interference=0.72, sensor_interference=0.65, node_failure_rate=0.015
    ),
}


class ScenarioEngine:
    def __init__(self, preset: ScenarioPreset, rng: random.Random) -> None:
        self.preset = preset
        self.rng = rng
        self._next_evaluation = 1.0
        self._counter = 0

    def step(self, now: float, node_ids: list[str]) -> list[ScenarioEvent]:
        generated: list[ScenarioEvent] = []
        while self._next_evaluation <= now + 1e-9:
            generated.extend(self._evaluate(self._next_evaluation, node_ids))
            self._next_evaluation += 1.0
        return generated

    def _evaluate(self, timestamp: float, node_ids: list[str]) -> list[ScenarioEvent]:
        if not node_ids:
            return []
        config = PRESET_INTERFERENCE[self.preset]
        events: list[ScenarioEvent] = []
        probabilities = [
            ("GPS_OUTAGE", config.gps_interference * 0.08, 0.75, (2.0, 8.0)),
            ("NETWORK_SPIKE", config.network_interference * 0.07, 0.75, (1.0, 5.0)),
            ("SENSOR_NOISE_SPIKE", config.sensor_interference * 0.04, 0.7, (2.0, 6.0)),
        ]
        for kind, probability, severity, duration_range in probabilities:
            if self.rng.random() < probability:
                self._counter += 1
                duration = self.rng.uniform(*duration_range)
                events.append(
                    ScenarioEvent(
                        id=f"seeded-{self._counter:05d}",
                        timestamp=timestamp,
                        type=kind,
                        affected_nodes=[self.rng.choice(node_ids)],
                        severity=min(1.0, severity + self.rng.uniform(-0.15, 0.2)),
                        duration=duration,
                        metadata={"preset": self.preset},
                    )
                )
        return events
