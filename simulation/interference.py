"""Configurable interference translated into measurement and network effects."""

from __future__ import annotations

from dataclasses import dataclass

from .models import InterferenceConfig, ScenarioEvent


@dataclass
class _Effect:
    kind: str
    severity: float
    nodes: set[str]
    expires_at: float


class InterferenceEngine:
    def __init__(self, config: InterferenceConfig | None = None) -> None:
        self.config = config or InterferenceConfig()
        self._effects: dict[str, _Effect] = {}

    def configure(self, config: InterferenceConfig) -> None:
        self.config = config

    def apply(self, event: ScenarioEvent) -> None:
        self._effects[event.id] = _Effect(
            kind=event.type.upper(),
            severity=event.severity,
            nodes=set(event.affected_nodes),
            expires_at=event.timestamp + event.duration,
        )

    def update(self, now: float) -> list[str]:
        expired = [effect_id for effect_id, effect in self._effects.items() if effect.expires_at <= now]
        for effect_id in expired:
            del self._effects[effect_id]
        return expired

    def gps_severity(self, node_id: str) -> float:
        return self._severity("GPS", node_id, self.config.gps_interference)

    def network_severity(self, node_id: str) -> float:
        return self._severity("NETWORK", node_id, self.config.network_interference)

    def sensor_severity(self, node_id: str) -> float:
        return self._severity("SENSOR", node_id, self.config.sensor_interference)

    def _severity(self, kind: str, node_id: str, base: float) -> float:
        values = [base]
        for effect in self._effects.values():
            if kind in effect.kind and (not effect.nodes or node_id in effect.nodes):
                values.append(effect.severity)
        return min(1.0, max(values))

