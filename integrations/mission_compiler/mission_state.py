"""Typed read/write client for the mission runtime HTTP API.

Wraps the existing ``MissionClient`` so the compiler works against parsed
canonical models instead of loose JSON, and so the read path used by the
explanation endpoint stays separate from the (single) write path.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from integrations.mission_client import MissionClient, MissionClientError
from simulation.models import MissionCommand, SimulationSnapshot


class MissionStateError(RuntimeError):
    """The runtime was unreachable or returned something unparseable."""


class MissionStateClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8000", timeout: float = 5.0) -> None:
        self._client = MissionClient(base_url, timeout)

    def snapshot(self) -> SimulationSnapshot:
        try:
            payload = self._client.state()
        except MissionClientError as exc:
            raise MissionStateError(str(exc)) from exc
        try:
            return SimulationSnapshot.model_validate(payload)
        except ValidationError as exc:
            raise MissionStateError(f"runtime snapshot did not match the canonical schema: {exc}") from exc

    def submit(self, command: MissionCommand) -> dict[str, Any]:
        try:
            return self._client.submit(command)
        except MissionClientError as exc:
            raise MissionStateError(str(exc)) from exc
