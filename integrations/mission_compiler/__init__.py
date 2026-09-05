"""Local mission compiler: multimodal operator turns into typed mission plans.

Layering, outermost first:

    OperatorContext  ->  MissionPlanCompiler  ->  MissionCompileResult
                                                     |
                                       MissionPlan (typed, validated)
                                                     |
                                  MissionPlanRuntime -> MissionCommand ...

``MissionCommand`` remains the runtime's execution primitive; nothing here
replaces it.  The model only ever produces text that this package parses,
grounds, validates and gates before any command exists.
"""

from .amendments import MissionAmender, apply_amendments, parse_amendment_response, plan_digest
from .baseline import BaselineMissionCompiler
from .compiler import MissionCompilerError, MissionPlanCompiler, render_plan
from .context import OperatorContext, UnitAvailability
from .digest import MissionContextDigest, build_digest
from .evaluation import run_evaluation, write_evaluation
from .parsing import PlanParseError, parse_plan_response
from .policy import MissionPolicy, evaluate_plan
from .query import MissionQueryEngine, deterministic_answer
from .runtime import MissionPlanRuntime, PlanAction, objective_to_command, plan_to_commands

__all__ = [
    "BaselineMissionCompiler",
    "MissionAmender",
    "MissionCompilerError",
    "MissionContextDigest",
    "MissionPlanCompiler",
    "MissionPlanRuntime",
    "MissionPolicy",
    "MissionQueryEngine",
    "OperatorContext",
    "PlanAction",
    "PlanParseError",
    "UnitAvailability",
    "apply_amendments",
    "build_digest",
    "deterministic_answer",
    "evaluate_plan",
    "objective_to_command",
    "parse_amendment_response",
    "parse_plan_response",
    "plan_digest",
    "plan_to_commands",
    "render_plan",
    "run_evaluation",
    "write_evaluation",
]
