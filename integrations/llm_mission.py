"""Constrained local-LLM translation into the canonical MissionCommand."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from chat_client import chat_sync
from simulation.models import MissionCommand, TaskType

from .mission_client import MissionClient, MissionClientError

MISSION_SYSTEM_PROMPT = """You translate one operator instruction into one mission command.
Return exactly one JSON object and no markdown, commentary, or extra keys.
Allowed type values: GOTO, WATCH, SEARCH, TRACE, FOLLOW, HOLD, RETURN, REGROUP.
The exact shape is:
{"type":"TYPE","target":{"point":{"x":0,"y":0,"z":0},"waypoints":[],"region_id":null,"entity_id":null},"priority":50,"required_capabilities":[],"desired_units":1,"minimum_units":1,"metadata":{}}
Use x/y/z metres only. Include only the applicable target selector. HOLD and RETURN use an empty target object.
Examples:
Operator: go to x 12 y -4 z 20
JSON: {"type":"GOTO","target":{"point":{"x":12,"y":-4,"z":20}},"priority":50,"required_capabilities":[],"desired_units":1,"minimum_units":1,"metadata":{}}
Operator: send two drones to search sector alpha
JSON: {"type":"SEARCH","target":{"region_id":"alpha"},"priority":50,"required_capabilities":[],"desired_units":2,"minimum_units":1,"metadata":{}}
Operator: watch point x 5 y 8 z 30
JSON: {"type":"WATCH","target":{"point":{"x":5,"y":8,"z":30}},"priority":50,"required_capabilities":[],"desired_units":1,"minimum_units":1,"metadata":{}}
Operator: return
JSON: {"type":"RETURN","target":{},"priority":50,"required_capabilities":[],"desired_units":1,"minimum_units":1,"metadata":{}}
Operator: regroup at x 0 y 0 z 25
JSON: {"type":"REGROUP","target":{"point":{"x":0,"y":0,"z":25}},"priority":50,"required_capabilities":[],"desired_units":1,"minimum_units":1,"metadata":{}}
Operator: follow vehicle 7
JSON: {"type":"FOLLOW","target":{"entity_id":"vehicle-7"},"priority":50,"required_capabilities":[],"desired_units":1,"minimum_units":1,"metadata":{}}
Operator: hold
JSON: {"type":"HOLD","target":{},"priority":50,"required_capabilities":[],"desired_units":1,"minimum_units":1,"metadata":{}}"""


class MissionTranslationError(ValueError):
    """The model response was not safe to execute as a mission command."""


def strip_markdown_fence(text: str) -> str:
    stripped = text.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else stripped


def parse_mission_response(text: str) -> MissionCommand:
    raw = strip_markdown_fence(text)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MissionTranslationError(f"local model returned invalid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise MissionTranslationError("local model response must be one JSON object")
    allowed = {
        "type",
        "target",
        "priority",
        "required_capabilities",
        "desired_units",
        "minimum_units",
        "metadata",
    }
    unexpected = sorted(set(payload) - allowed)
    if unexpected:
        raise MissionTranslationError(f"local model returned unexpected fields: {', '.join(unexpected)}")
    target_payload = payload.get("target")
    if isinstance(target_payload, dict):
        unexpected_target = sorted(
            set(target_payload) - {"point", "waypoints", "region_id", "entity_id"}
        )
        if unexpected_target:
            raise MissionTranslationError(
                f"local model returned unexpected target fields: {', '.join(unexpected_target)}"
            )
    try:
        command = MissionCommand.model_validate(payload)
    except ValidationError as exc:
        raise MissionTranslationError(f"local model returned an invalid mission schema: {exc}") from exc
    _validate_semantics(command)
    return command


def _validate_semantics(command: MissionCommand) -> None:
    target = command.target
    selectors = [target.point is not None, bool(target.waypoints), target.region_id is not None, target.entity_id is not None]
    if sum(selectors) > 1:
        raise MissionTranslationError("mission target must use exactly one selector")
    if command.minimum_units > command.desired_units:
        raise MissionTranslationError("minimum_units cannot exceed desired_units")
    if command.type in {TaskType.GOTO, TaskType.WATCH, TaskType.REGROUP} and not (
        target.point or target.region_id
    ):
        raise MissionTranslationError(f"{command.type} requires a point or region target")
    if command.type == TaskType.SEARCH and not (target.region_id or target.waypoints):
        raise MissionTranslationError("SEARCH requires a region or waypoints")
    if command.type == TaskType.TRACE and not target.waypoints:
        raise MissionTranslationError("TRACE requires waypoints")
    if command.type == TaskType.FOLLOW and not target.entity_id:
        raise MissionTranslationError("FOLLOW requires an entity_id")


class LocalLLMMissionAdapter:
    def __init__(
        self,
        completion: Callable[..., Any] = chat_sync,
        chat_base_url: str | None = None,
    ) -> None:
        self._completion = completion
        self.chat_base_url = chat_base_url

    def translate(self, instruction: str) -> MissionCommand:
        if not instruction.strip():
            raise MissionTranslationError("operator instruction cannot be empty")
        result = self._completion(
            instruction,
            system_message=MISSION_SYSTEM_PROMPT,
            n_predict=320,
            temperature=0.0,
            base_url=self.chat_base_url,
        )
        text = result if isinstance(result, str) else getattr(result, "text", None)
        if not isinstance(text, str):
            raise MissionTranslationError("local model client returned no text")
        return parse_mission_response(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("instruction", help="natural-language operator instruction")
    parser.add_argument("--submit", action="store_true", help="submit the validated command to the runtime")
    parser.add_argument("--mission-url", default="http://127.0.0.1:8000")
    parser.add_argument("--chat-url", default=None, help="override CHAT_SERVER_URL")
    args = parser.parse_args(argv)
    try:
        command = LocalLLMMissionAdapter(chat_base_url=args.chat_url).translate(args.instruction)
        print(command.model_dump_json(indent=2))
        if args.submit:
            response = MissionClient(args.mission_url).submit(command)
            print(json.dumps(response, indent=2, sort_keys=True))
    except (MissionTranslationError, MissionClientError, OSError) as exc:
        print(f"mission rejected: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
