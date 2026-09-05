from __future__ import annotations

import json

import pytest

from integrations.gesture_mission import GestureMissionAdapter
from integrations.llm_mission import LocalLLMMissionAdapter, MissionTranslationError, parse_mission_response
from integrations.mission_client import MissionClient
from simulation.models import MissionCommand, MissionTarget, TaskType


class StubCompletion:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[dict[str, object]] = []

    def __call__(self, instruction: str, **kwargs: object) -> object:
        self.calls.append({"instruction": instruction, **kwargs})
        return type("Result", (), {"text": self.text})()


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_valid_llm_json_uses_canonical_schema_and_constrained_prompt() -> None:
    completion = StubCompletion(
        """```json
        {"type":"SEARCH","target":{"region_id":"alpha"},"desired_units":2,"minimum_units":1}
        ```"""
    )
    command = LocalLLMMissionAdapter(completion=completion).translate(
        "send two drones to search sector alpha"
    )
    assert isinstance(command, MissionCommand)
    assert command.type == TaskType.SEARCH
    assert command.target.region_id == "alpha"
    assert command.desired_units == 2
    assert "Return exactly one JSON object" in str(completion.calls[0]["system_message"])
    assert completion.calls[0]["temperature"] == 0.0


@pytest.mark.parametrize(
    "response",
    [
        "not json",
        '{"type":"FLY","target":{}}',
        '{"type":"FOLLOW","target":{}}',
        '[{"type":"HOLD","target":{}}]',
        '{"type":"HOLD","target":{},"shell_command":"rm something"}',
        '{"type":"SEARCH","target":{"region_id":"alpha"},"desired_units":1,"minimum_units":2}',
    ],
)
def test_invalid_llm_output_is_rejected(response: str) -> None:
    with pytest.raises(MissionTranslationError):
        parse_mission_response(response)


def test_gesture_return_and_hold_create_canonical_commands() -> None:
    adapter = GestureMissionAdapter()
    returned = adapter.event_to_command("return_home")
    held = adapter.event_to_command("Thumb_Down")
    assert isinstance(returned, MissionCommand)
    assert returned.type == TaskType.RETURN
    assert isinstance(held, MissionCommand)
    assert held.type == TaskType.HOLD
    assert held.metadata["input_source"] == "gesture"
    assert adapter.event_to_command("Closed_Fist") is None
    assert adapter.event_to_command("spin360") is None


def test_mission_client_serializes_canonical_command() -> None:
    captured: dict[str, object] = {}

    def opener(request: object, timeout: float) -> FakeResponse:
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse({"id": "task-1", "status": "PENDING"})

    command = MissionCommand(type=TaskType.HOLD, target=MissionTarget())
    response = MissionClient("http://runtime.test/", opener=opener).submit(command)
    request = captured["request"]
    body = json.loads(request.data.decode("utf-8"))
    assert request.full_url == "http://runtime.test/api/missions"
    assert request.method == "POST"
    assert body["type"] == "HOLD"
    assert body["target"] == {"point": None, "waypoints": [], "region_id": None, "entity_id": None}
    assert response["id"] == "task-1"
