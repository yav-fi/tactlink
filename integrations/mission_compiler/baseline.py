"""A keyword-grammar mission compiler used as an evaluation floor.

It shares the whole post-compilation pipeline with the model path: it emits
the same plan JSON and hands it to ``parse_plan_response``, so grounding,
semantic checks and policy are byte-identical.  Only the *understanding* step
differs, which is exactly what the benchmark is trying to measure.

It is also the offline fallback: the operator still gets a validated plan for
plain instructions when no model backend is reachable.
"""

from __future__ import annotations

import json
import re
import time

from simulation.mission_plan import (
    CompileStatus,
    MissionAmendResult,
    MissionCompileResult,
    MissionPlan,
)

from .amendments import apply_amendments, parse_amendment_response
from .context import OperatorContext
from .parsing import PlanParseError, describe_plan, parse_plan_response
from .policy import MissionPolicy

NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "another": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "twelve": 12, "twenty": 20, "fifty": 50, "hundred": 100,
}

VERB_TYPES: tuple[tuple[str, str], ...] = (
    (r"\b(search|sweep|scan|clear|explore|patrol)\b", "SEARCH"),
    (r"\b(watch|monitor|observe|overwatch|keep an eye|keep .* watching|guard)\b", "WATCH"),
    (r"\b(follow|track|tail|shadow)\b", "FOLLOW"),
    (r"\b(regroup|rally|form up)\b", "REGROUP"),
    (r"\b(return|come back|bring .* back|rtb|come home|go home)\b", "RETURN"),
    (r"\b(hold|land|loiter|stop|stand by|pause)\b", "HOLD"),
    (r"\b(relay|connectivity|network|connected|comms)\b", "MAINTAIN_NETWORK"),
    (r"\b(trace|route|path along)\b", "TRACE"),
    (r"\b(go to|move to|fly to|proceed to|head to)\b", "GOTO"),
    # Generic tasking verbs are tried last so a specific verb always wins.
    (r"\b(send|dispatch|move|position|put|take)\b", "GOTO"),
)

DEICTIC_TOKENS: tuple[tuple[str, str], ...] = (
    (r"\bthere\b|\bthat area\b|\bthis area\b|\bthat region\b|\bthat sector\b|\bthe selected (?:area|region)\b", "SELECTED_REGION"),
    (r"\bhere\b|\bthis point\b|\bthis spot\b|\bthat point\b", "MAP_CURSOR"),
    (r"\bto me\b|\bon me\b|\bmy position\b|\baround me\b", "OPERATOR_POSITION"),
    (r"\bthat drone\b|\bthis drone\b|\bthe selected drone\b", "SELECTED_DRONE"),
)

CAPABILITY_WORDS = {
    "camera": "camera", "video": "camera", "eo": "camera", "sensor": "camera",
    "thermal": "thermal", "ir": "thermal", "infrared": "thermal",
    "mapping": "mapping", "lidar": "mapping", "relay": "relay",
}

CLAUSE_SPLIT = re.compile(
    r",\s*(?:and\s+|then\s+|also\s+)?|\s+and\s+(?=\w)|;\s*|\s+then\s+|\s+while\s+|\s+plus\s+",
    flags=re.IGNORECASE,
)
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
CONDITIONAL = re.compile(
    r"\b(?:if|when|once|should|whenever)\b|\bpredicted to\b|\b(?:below|under)\s+(?:the\s+)?(?:reserve|threshold|minimum)\b",
    flags=re.IGNORECASE,
)
# In a conditional sentence the objective lives in the consequent, so the
# antecedent ("if vehicle 7 appears") does not steal the verb.
CONSEQUENT_SPLIT = re.compile(r",\s*|\bthen\b|\bshould\b|\bhave\b", flags=re.IGNORECASE)


def _segments(utterance: str) -> list[tuple[str, bool]]:
    """Split into (text, conditional) units: sentences first, then clauses."""
    segments: list[tuple[str, bool]] = []
    for sentence in SENTENCE_SPLIT.split(utterance):
        sentence = sentence.strip()
        if not sentence:
            continue
        if CONDITIONAL.search(sentence):
            segments.append((sentence, True))
            continue
        for clause in CLAUSE_SPLIT.split(sentence):
            clause = clause.strip()
            if clause:
                segments.append((clause, False))
    return segments


def _consequent(sentence: str) -> str:
    parts = [part.strip() for part in CONSEQUENT_SPLIT.split(sentence) if part.strip()]
    return parts[-1] if len(parts) > 1 else sentence


def _units(clause: str) -> int:
    match = re.search(r"\b(\d{1,3})\s*(?:more\s+)?(?:drones?|units?|uavs?|aircraft)\b", clause, re.IGNORECASE)
    if match:
        return max(1, min(999, int(match.group(1))))
    words = "|".join(NUMBER_WORDS)
    match = re.search(rf"\b({words})\b\s*(?:more\s+)?(?:drones?|units?|uavs?|aircraft)?\b", clause, re.IGNORECASE)
    if match:
        return NUMBER_WORDS[match.group(1).lower()]
    return 1


def _task_type(clause: str) -> str | None:
    for pattern, task_type in VERB_TYPES:
        if re.search(pattern, clause, re.IGNORECASE):
            return task_type
    return None


def _target(clause: str, task_type: str, context: OperatorContext) -> dict:
    if task_type in {"HOLD", "RETURN", "MAINTAIN_NETWORK"}:
        return {}
    entity = re.search(r"\b(vehicle|target|contact|truck|car)[\s-]?(\d+)\b", clause, re.IGNORECASE)
    if entity and task_type == "FOLLOW":
        return {"entity_id": f"{entity.group(1).lower()}-{entity.group(2)}"}
    for region in context.known_regions:
        if re.search(rf"\b{re.escape(region.lower())}\b", clause.lower()):
            return {"region_id": region}
    coordinates = re.search(
        r"x\s*(-?\d+(?:\.\d+)?)\D+y\s*(-?\d+(?:\.\d+)?)(?:\D+z\s*(-?\d+(?:\.\d+)?))?",
        clause,
        re.IGNORECASE,
    )
    if coordinates:
        return {
            "point": {
                "x": float(coordinates.group(1)),
                "y": float(coordinates.group(2)),
                "z": float(coordinates.group(3) or 30.0),
            }
        }
    for pattern, token in DEICTIC_TOKENS:
        if re.search(pattern, clause, re.IGNORECASE):
            return {"reference": token}
    named = re.search(r"\b(?:the\s+)?(entrance|exit|gate|perimeter|bridge|road|junction)\b", clause, re.IGNORECASE)
    if named:
        return {"region_id": named.group(1).upper()}
    if entity:
        return {"entity_id": f"{entity.group(1).lower()}-{entity.group(2)}"}
    return {}


def _capabilities(clause: str) -> list[str]:
    found = {
        capability
        for word, capability in CAPABILITY_WORDS.items()
        if re.search(rf"\b{word}\b", clause, re.IGNORECASE)
    }
    return sorted(found)


def _priority(clause: str, task_type: str) -> int:
    if re.search(r"\b(top priority|most important|highest priority|critical|first)\b", clause, re.IGNORECASE):
        return 90
    return {"MAINTAIN_NETWORK": 90, "WATCH": 80, "RETURN": 85, "FOLLOW": 70, "SEARCH": 60}.get(task_type, 50)


def _trigger_for(clause: str, objective_id: str, index: int) -> dict | None:
    entity = re.search(r"\b(vehicle|target|contact|truck|car)[\s-]?(\d+)\b", clause, re.IGNORECASE)
    battery = re.search(r"\b(?:battery|charge|power|reserve)\b.{0,24}?(\d{1,3})\s*%", clause, re.IGNORECASE)
    network = re.search(r"\bnetwork\b.{0,24}?(\d{1,3})\s*%", clause, re.IGNORECASE)
    when: dict | None = None
    if network:
        when = {"event": "NETWORK_HEALTH_BELOW", "threshold": int(network.group(1)) / 100.0}
    elif battery:
        when = {"event": "BATTERY_BELOW", "threshold": int(battery.group(1)) / 100.0}
    elif re.search(r"\b(battery|reserve|fuel|charge)\b", clause, re.IGNORECASE):
        when = {"event": "BATTERY_BELOW", "threshold": 0.25}
    elif entity:
        when = {"event": "ENTITY_OBSERVED", "entity_id": f"{entity.group(1).lower()}-{entity.group(2)}"}
    if when is None:
        return None
    return {
        "id": f"t{index + 1}",
        "when": when,
        "action": "ACTIVATE_OBJECTIVE",
        "objective_id": objective_id,
    }


def build_plan_payload(context: OperatorContext) -> dict:
    """The same JSON the model is asked to produce, derived by grammar."""
    objectives: list[dict] = []
    triggers: list[dict] = []
    constraints: list[dict] = []
    for segment, conditional in _segments(context.utterance.strip()):
        # A conditional reads its verb from the consequent but its target from
        # the whole sentence, so "if vehicle 7 appears, follow it" still binds.
        clause = _consequent(segment) if conditional else segment
        task_type = _task_type(clause) or (_task_type(segment) if conditional else None)
        if task_type is None:
            continue
        objective_id = f"o{len(objectives) + 1}"
        objective = {
            "id": objective_id,
            "label": segment[:48],
            "type": task_type,
            "target": _target(segment if conditional else clause, task_type, context),
            "priority": _priority(segment, task_type),
            "desired_units": _units(clause),
            "minimum_units": 1,
            "required_capabilities": _capabilities(segment),
            "activation": "ON_TRIGGER" if conditional else "IMMEDIATE",
        }
        objectives.append(objective)
        if conditional:
            trigger = _trigger_for(segment, objective_id, len(triggers))
            if trigger is not None:
                triggers.append(trigger)
                if trigger["when"]["event"] == "BATTERY_BELOW":
                    constraints.append(
                        {
                            "type": "BATTERY_RESERVE",
                            "value": trigger["when"].get("threshold", 0.25),
                            "enforcement": "BLOCKING",
                        }
                    )
            else:
                objective["activation"] = "IMMEDIATE"
    return {
        "status": CompileStatus.READY.value if objectives else CompileStatus.REJECTED.value,
        "summary": (
            f"{len(objectives)} objective(s) parsed by the deterministic grammar"
            if objectives
            else "no executable objective was recognised in this instruction"
        ),
        "clarification_question": None,
        "objectives": objectives,
        "triggers": triggers,
        "constraints": constraints,
        "dependencies": [],
        "termination": [{"type": "ALL_OBJECTIVES_COMPLETE"}],
    }


class BaselineMissionCompiler:
    """Same interface as MissionPlanCompiler, no model involved."""

    name = "deterministic-grammar"

    def __init__(self, policy: MissionPolicy | None = None) -> None:
        self.policy = policy or MissionPolicy()

    def compile(self, context: OperatorContext) -> MissionCompileResult:
        if not context.utterance.strip():
            return MissionCompileResult(
                status=CompileStatus.REJECTED,
                rejection_reason="operator instruction cannot be empty",
            )
        started = time.perf_counter()
        payload = build_plan_payload(context)
        try:
            result = parse_plan_response(
                json.dumps(payload), context, self.policy, now=context.timestamp
            )
        except PlanParseError as exc:
            return MissionCompileResult(
                status=CompileStatus.REJECTED,
                rejection_reason=str(exc),
                diagnostics={"backend": self.name},
            )
        result.diagnostics = {
            **result.diagnostics,
            "backend": self.name,
            "latency_ms": round((time.perf_counter() - started) * 1000.0, 3),
        }
        if result.plan is not None:
            result.plan.metadata["compiler"] = self.name
            if not result.interpretation_summary:
                result.interpretation_summary = describe_plan(result.plan)
        return result


# --- amendments ---------------------------------------------------------

AMEND_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\b(bring|recall|call)\b.{0,20}\b(all|every|everyone|everybody)\b.{0,20}\b(back|home|return)\b", "RECALL_ALL"),
    (r"\b(all|every)\b.{0,20}\b(drones?|units?)\b.{0,20}\b(back|home|return)\b", "RECALL_ALL"),
    (r"\b(prioriti[sz]e|raise|elevate|bump)\b", "SET_PRIORITY"),
    (r"\b(deprioriti[sz]e|lower|drop the priority)\b", "SET_PRIORITY"),
    (r"\b(cancel|abort|drop|stop|end|call off)\b", "CANCEL_OBJECTIVE"),
    (r"\b(suspend|pause|hold off)\b", "SUSPEND_OBJECTIVE"),
    (r"\b(resume|restart|continue)\b", "RESUME_OBJECTIVE"),
    (r"\badd\b.{0,24}\b(drones?|units?|uavs?)\b", "ADD_UNITS"),
    (r"\b(use|assign|put)\b.{0,24}\b(drones?|units?|uavs?)\b", "SET_UNITS"),
)


def _selector_for(clause: str, context: OperatorContext) -> dict:
    selector: dict = {}
    for region in context.known_regions:
        if re.search(rf"\b{re.escape(region.lower())}\b", clause.lower()):
            selector["region_id"] = region
            break
    task_type = _task_type(clause)
    if task_type and task_type not in {"RETURN", "HOLD"}:
        selector["objective_type"] = task_type
    entity = re.search(r"\b(vehicle|target|contact)[\s-]?(\d+)\b", clause, re.IGNORECASE)
    if entity:
        selector["entity_id"] = f"{entity.group(1).lower()}-{entity.group(2)}"
    return selector


def build_amendment_payload(plan: MissionPlan, context: OperatorContext) -> dict:
    """Grammar-derived amendments in the same JSON shape the model emits."""
    utterance = context.utterance.strip()
    amendment_type = next(
        (name for pattern, name in AMEND_PATTERNS if re.search(pattern, utterance, re.IGNORECASE)),
        None,
    )
    if amendment_type is None:
        return {
            "status": CompileStatus.NEEDS_CLARIFICATION.value,
            "summary": "no supported amendment was recognised",
            "clarification_question": "Which objective should change, and how?",
            "amendments": [],
        }
    if amendment_type == "RECALL_ALL":
        return {
            "status": CompileStatus.READY.value,
            "summary": "recall every active objective",
            "clarification_question": None,
            "amendments": [{"type": "RECALL_ALL", "selector": {}, "value": None, "rationale": "operator recall"}],
        }

    over = re.split(r"\bover\b|\bahead of\b|\bbefore\b", utterance, maxsplit=1, flags=re.IGNORECASE)
    primary = _selector_for(over[0], context)
    value: int | None = None
    if amendment_type == "SET_PRIORITY":
        others = [item.priority for item in plan.objectives] or [50]
        raised = min(100, max(others) + 10)
        if re.search(r"\b(deprioriti[sz]e|lower|drop the priority)\b", utterance, re.IGNORECASE):
            raised = max(0, min(others) - 10)
        value = raised
    elif amendment_type in {"ADD_UNITS", "SET_UNITS"}:
        value = _units(utterance)
    if not primary:
        return {
            "status": CompileStatus.NEEDS_CLARIFICATION.value,
            "summary": "the amendment did not name an objective",
            "clarification_question": "Which objective should change?",
            "amendments": [],
        }
    return {
        "status": CompileStatus.READY.value,
        "summary": f"{amendment_type} applied by the deterministic grammar",
        "clarification_question": None,
        "amendments": [
            {
                "type": amendment_type,
                "selector": primary,
                "value": value,
                "rationale": utterance[:80],
            }
        ],
    }


class BaselineMissionAmender:
    """Same interface as MissionAmender, no model involved."""

    name = "deterministic-grammar"

    def __init__(self, policy: MissionPolicy | None = None) -> None:
        self.policy = policy or MissionPolicy()

    def amend(self, plan: MissionPlan, context: OperatorContext) -> MissionAmendResult:
        payload = build_amendment_payload(plan, context)
        try:
            result = parse_amendment_response(json.dumps(payload), plan, self.policy)
        except PlanParseError as exc:
            return MissionAmendResult(
                status=CompileStatus.REJECTED,
                rejection_reason=str(exc),
                diagnostics={"backend": self.name},
            )
        result.diagnostics = {**result.diagnostics, "backend": self.name}
        if result.status == CompileStatus.READY:
            applied, warnings = apply_amendments(plan, result.amendments, self.policy)
            result.applied = applied
            result.warnings.extend(warnings)
            result.plan = plan
            if not result.interpretation_summary:
                result.interpretation_summary = "; ".join(applied)
        return result
