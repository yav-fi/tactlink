"""Operator CLI for the mission compiler: compile, amend, explain, demo.

    python -m integrations.mission_compiler compile "search there with two" --region ALPHA
    python -m integrations.mission_compiler compile "..." --baseline      # no model needed
    python -m integrations.mission_compiler explain "why did drone 4 move?" --mission-url ...
    python -m integrations.mission_compiler demo                          # full scripted flow
"""

from __future__ import annotations

import argparse
import json
import sys

from simulation.mission_plan import CompileStatus, MissionPlan
from simulation.models import SimulationSnapshot, Vector3

from .amendments import MissionAmender, plan_digest
from .baseline import BaselineMissionCompiler
from .compiler import MissionPlanCompiler, render_plan
from .context import OperatorContext, UnitAvailability
from .mission_state import MissionStateClient, MissionStateError
from .query import MissionQueryEngine
from .runtime import plan_to_commands


def _context_from_args(args: argparse.Namespace, utterance: str) -> OperatorContext:
    cursor = None
    if args.cursor:
        parts = [float(value) for value in args.cursor.split(",")]
        if len(parts) != 3:
            raise SystemExit("--cursor takes x,y,z")
        cursor = Vector3(x=parts[0], y=parts[1], z=parts[2])
    return OperatorContext(
        utterance=utterance,
        selected_region=args.region,
        map_cursor=cursor,
        gesture=args.gesture,
        selected_drone_id=args.drone,
        known_regions=args.known_regions.split(",") if args.known_regions else [],
        known_entities=args.known_entities.split(",") if args.known_entities else [],
        known_capabilities=["camera", "thermal", "mapping", "relay", "navigation"],
        availability=UnitAvailability(
            total_units=args.available_units,
            available_units=args.available_units,
            capability_counts={"camera": args.available_units, "relay": max(0, args.available_units - 1)},
        ),
    )


def _compiler(args: argparse.Namespace):
    if args.baseline:
        return BaselineMissionCompiler()
    return MissionPlanCompiler(chat_base_url=args.chat_url)


def _print_result(result) -> int:
    print(f"status: {result.status.value}")
    if result.interpretation_summary:
        print(f"reading: {result.interpretation_summary}")
    if result.status == CompileStatus.NEEDS_CLARIFICATION:
        print(f"clarification needed: {result.clarification_question}")
    if result.status == CompileStatus.REJECTED:
        print(f"rejected: {result.rejection_reason}")
    for warning in result.warnings:
        print(f"warning: {warning}")
    plan = getattr(result, "plan", None)
    if plan is not None:
        print()
        print(render_plan(plan))
        print()
        print("compiles into:")
        for command in plan_to_commands(plan):
            print(f"  {command.type.value} {command.model_dump_json()}")
    if result.diagnostics:
        print(f"diagnostics: {json.dumps(result.diagnostics, sort_keys=True)}")
    return 0 if result.status == CompileStatus.READY else 1


def _snapshot(args: argparse.Namespace) -> tuple[SimulationSnapshot, list]:
    client = MissionStateClient(args.mission_url)
    snapshot = client.snapshot()
    return snapshot, snapshot.events


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mission_compiler", description=__doc__)
    parser.add_argument("--chat-url", default=None, help="override CHAT_SERVER_URL")
    parser.add_argument("--mission-url", default="http://127.0.0.1:8000")
    parser.add_argument("--baseline", action="store_true", help="use the deterministic grammar, no model")
    parser.add_argument("--region", default=None, help="region selected on the map")
    parser.add_argument("--cursor", default=None, help="map cursor as x,y,z")
    parser.add_argument("--gesture", default=None)
    parser.add_argument("--drone", default=None, help="selected drone id")
    parser.add_argument("--known-regions", default="ALPHA,BRAVO,ENTRANCE")
    parser.add_argument("--known-entities", default="vehicle-1")
    parser.add_argument("--available-units", type=int, default=3)
    subparsers = parser.add_subparsers(dest="command", required=True)

    compile_parser = subparsers.add_parser("compile", help="compile an instruction into a mission plan")
    compile_parser.add_argument("instruction")
    compile_parser.add_argument("--submit", action="store_true", help="submit the compiled commands to the runtime")

    amend_parser = subparsers.add_parser("amend", help="amend a plan stored as JSON")
    amend_parser.add_argument("instruction")
    amend_parser.add_argument("--plan-file", required=True)

    explain_parser = subparsers.add_parser("explain", help="ask a read-only question about the mission")
    explain_parser.add_argument("question")

    subparsers.add_parser("demo", help="scripted compile -> amend -> explain flow, no runtime needed")
    eval_parser = subparsers.add_parser("evaluate", help="run the scored mission-intelligence suite")
    eval_parser.add_argument("--output", default="runs/mission-intelligence-eval.json")

    args = parser.parse_args(argv)

    if args.command == "demo":
        from .demo import run_demo

        return run_demo(chat_base_url=args.chat_url, baseline=args.baseline)

    if args.command == "evaluate":
        from .evaluation import run_evaluation, write_evaluation

        backend = "baseline" if args.baseline else "local-llm"
        payload = run_evaluation(backend=backend, chat_url=args.chat_url)
        path = write_evaluation(payload, args.output)
        print(json.dumps(payload["metrics"], indent=2, sort_keys=True))
        print(f"artifact: {path}")
        return 0

    if args.command == "compile":
        context = _context_from_args(args, args.instruction)
        result = _compiler(args).compile(context)
        code = _print_result(result)
        if args.submit and result.plan is not None:
            client = MissionStateClient(args.mission_url)
            for command in plan_to_commands(result.plan):
                print(json.dumps(client.submit(command), sort_keys=True))
        return code

    if args.command == "amend":
        plan = MissionPlan.model_validate_json(open(args.plan_file, encoding="utf-8").read())
        context = _context_from_args(args, args.instruction)
        amender = MissionAmender(chat_base_url=args.chat_url)
        result = amender.amend(plan, context)
        print(f"status: {result.status.value}")
        for applied in result.applied:
            print(f"applied: {applied}")
        if result.clarification_question:
            print(f"clarification needed: {result.clarification_question}")
        if result.rejection_reason:
            print(f"rejected: {result.rejection_reason}")
        for warning in result.warnings:
            print(f"warning: {warning}")
        if result.plan is not None:
            print()
            print(render_plan(result.plan))
            with open(args.plan_file, "w", encoding="utf-8") as handle:
                handle.write(result.plan.model_dump_json(indent=2))
            print(f"\n(updated {args.plan_file})")
            print(f"digest:\n{plan_digest(result.plan)}")
        return 0 if result.status == CompileStatus.READY else 1

    if args.command == "explain":
        try:
            snapshot, events = _snapshot(args)
        except MissionStateError as exc:
            print(f"cannot read mission state: {exc}", file=sys.stderr)
            return 2
        engine = MissionQueryEngine(chat_base_url=args.chat_url) if not args.baseline else MissionQueryEngine(completion=None)
        result = engine.answer(args.question, snapshot, events)
        print(result.answer)
        for warning in result.warnings:
            print(f"warning: {warning}", file=sys.stderr)
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
