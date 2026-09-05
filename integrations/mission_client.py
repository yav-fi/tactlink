"""Tiny HTTP client shared by operator-input adapters."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from simulation.models import MissionCommand


class MissionClientError(RuntimeError):
    """The mission runtime rejected or could not receive a request."""


class MissionClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        timeout: float = 5.0,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._opener = opener

    def submit(self, command: MissionCommand) -> dict[str, Any]:
        """Submit only an already-validated canonical command."""
        if not isinstance(command, MissionCommand):
            raise TypeError("command must be simulation.models.MissionCommand")
        request = urllib.request.Request(
            f"{self.base_url}/api/missions",
            data=command.model_dump_json().encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return self._request_json(request)

    def state(self) -> dict[str, Any]:
        request = urllib.request.Request(f"{self.base_url}/api/state", method="GET")
        return self._request_json(request)

    def _request_json(self, request: urllib.request.Request) -> dict[str, Any]:
        try:
            with self._opener(request, timeout=self.timeout) as response:
                payload = response.read().decode("utf-8")
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
            raise MissionClientError(f"mission runtime request failed: {exc}") from exc
        try:
            result = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise MissionClientError("mission runtime returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise MissionClientError("mission runtime returned a non-object response")
        return result
