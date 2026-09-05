"""Ports that keep autonomous nodes separable from the in-process simulator."""

from __future__ import annotations

from typing import Protocol

from .models import GPSMeasurement, MotionIntent, NetworkMessage


class MessageTransport(Protocol):
    def send(self, message: NetworkMessage, now: float) -> bool: ...

    def receive(self, node_id: str) -> list[NetworkMessage]: ...


class SensorProvider(Protocol):
    def gps_measurement(self, node_id: str, now: float) -> GPSMeasurement | None: ...


class MotionController(Protocol):
    def set_motion_intent(self, node_id: str, intent: MotionIntent) -> None: ...


class StatusPublisher(Protocol):
    def publish(self, node_id: str, payload: dict[str, object]) -> None: ...

