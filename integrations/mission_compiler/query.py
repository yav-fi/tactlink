"""Read-only mission explanation.

This path has no submitter, no plan runtime, and no writable state — the
inability to act is structural, not a prompt instruction.  The model receives
a bounded digest and returns prose; the prose is sanitized and, when the model
is unreachable, replaced by a deterministic answer computed from the digest.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Sequence
from typing import Any

from chat_client import chat_sync
from simulation.mission_plan import MissionPlan, MissionQueryResult
from simulation.models import SimulationEvent, SimulationSnapshot

from .digest import MissionContextDigest, build_digest
from .policy import detect_injection, sanitize_text
from .prompts import EXPLAIN_SYSTEM_PROMPT, explain_user_prompt

MAX_ANSWER_CHARACTERS = 700
CODE_FENCE = re.compile(r"```.*?```", flags=re.DOTALL)
ACTION_CLAIM = re.compile(
    r"\b(i (?:have |'ve )?(?:re)?(?:assigned|tasked|dispatched|launched|sent|cancelled|canceled)"
    r"|i will (?:send|task|dispatch|reassign|launch)|dispatching|tasking now)\b",
    flags=re.IGNORECASE,
)


class MissionQueryEngine:
    """Answers operator questions about a running mission. It cannot act."""

    def __init__(
        self,
        completion: Callable[..., Any] | None = chat_sync,
        chat_base_url: str | None = None,
        n_predict: int = 220,
    ) -> None:
        self._completion = completion
        self.chat_base_url = chat_base_url
        self.n_predict = n_predict

    def answer(
        self,
        question: str,
        snapshot: SimulationSnapshot,
        events: Sequence[SimulationEvent] = (),
        plans: Sequence[MissionPlan] = (),
    ) -> MissionQueryResult:
        digest = build_digest(snapshot, events, plans)
        return self.answer_from_digest(question, digest)

    def answer_from_digest(self, question: str, digest: MissionContextDigest) -> MissionQueryResult:
        cleaned_question = sanitize_text(question, 300)
        warnings: list[str] = []
        if not cleaned_question:
            return MissionQueryResult(
                question=question,
                answer="No question was provided.",
                warnings=["empty question"],
            )
        if detect_injection(cleaned_question):
            warnings.append(
                "question contained prompt-injection phrasing; this endpoint cannot act regardless"
            )
        citations = [event.event_type for event in digest.recent_events[-6:]]
        diagnostics: dict[str, Any] = {}

        if self._completion is None:
            return MissionQueryResult(
                question=cleaned_question,
                answer=deterministic_answer(cleaned_question, digest),
                citations=citations,
                context_digest=digest.model_dump(mode="json"),
                warnings=warnings + ["answered deterministically; no model backend configured"],
            )
        started = time.perf_counter()
        try:
            result = self._completion(
                explain_user_prompt(cleaned_question, digest.render()),
                system_message=EXPLAIN_SYSTEM_PROMPT,
                n_predict=self.n_predict,
                temperature=0.0,
                base_url=self.chat_base_url,
            )
        except OSError as exc:
            return MissionQueryResult(
                question=cleaned_question,
                answer=deterministic_answer(cleaned_question, digest),
                citations=citations,
                context_digest=digest.model_dump(mode="json"),
                warnings=warnings + [f"model backend unavailable ({exc}); answered deterministically"],
            )
        diagnostics["latency_ms"] = round((time.perf_counter() - started) * 1000.0, 2)
        text = result if isinstance(result, str) else getattr(result, "text", None)
        if not isinstance(text, str) or not text.strip():
            return MissionQueryResult(
                question=cleaned_question,
                answer=deterministic_answer(cleaned_question, digest),
                citations=citations,
                context_digest=digest.model_dump(mode="json"),
                warnings=warnings + ["model returned no text; answered deterministically"],
                diagnostics=diagnostics,
            )
        answer = _sanitize_answer(text)
        if ACTION_CLAIM.search(answer):
            warnings.append(
                "the explanation claimed an action; this endpoint is read-only and issued no commands"
            )
        return MissionQueryResult(
            question=cleaned_question,
            answer=answer,
            citations=citations,
            context_digest=digest.model_dump(mode="json"),
            warnings=warnings,
            diagnostics=diagnostics,
        )


def _sanitize_answer(text: str) -> str:
    stripped = CODE_FENCE.sub("", text).strip()
    # An explanation is prose; a JSON-looking answer means the model drifted.
    if stripped.startswith("{") or stripped.startswith("["):
        return "The model returned structured output instead of an explanation; no answer is available."
    return sanitize_text(stripped, MAX_ANSWER_CHARACTERS)


def deterministic_answer(question: str, digest: MissionContextDigest) -> str:
    """A grounded fallback answer computed straight from the digest.

    Also the reference the evaluation compares model answers against: every
    sentence here is traceable to one number in the snapshot.
    """
    lowered = question.lower()
    if "network" in lowered:
        return (
            f"Network health is {digest.network_health:.0%} with mean link quality "
            f"{digest.mean_link_quality:.0%}; the largest connected component holds "
            f"{digest.largest_component_fraction:.0%} of the fleet, {digest.degraded_nodes} node(s) are degraded "
            f"and relays are {digest.relay_nodes or 'not deployed'}."
        )
    if "least explored" in lowered or "coverage" in lowered or "region" in lowered:
        if not digest.regions:
            return "No region coverage has been reported yet."
        breakdown = ", ".join(f"{item.region_id} at {item.coverage:.0%}" for item in digest.regions)
        return (
            f"The least explored region is {digest.least_explored_region or 'unknown'}; "
            f"regions are {breakdown}."
        )
    if "weakest" in lowered or "effectiveness" in lowered or "going wrong" in lowered or "worst" in lowered:
        degraded = [item for item in digest.objectives if item.effectiveness < 0.45]
        detail = (
            "; ".join(
                f"{item.task_id} ({item.type}) at {item.effectiveness:.0%}" for item in degraded[:3]
            )
            or "no objective is below the degradation threshold"
        )
        return (
            f"Overall effectiveness is {digest.mission_effectiveness:.0%} and capability "
            f"{digest.mission_capability:.0%}. Weakest objective: {digest.weakest_objective or 'none'}. "
            f"Degraded: {detail}."
        )
    node_match = re.search(r"drone[\s-]?(\d+)", lowered)
    if node_match:
        node_id = f"drone-{node_match.group(1)}"
        node = next((item for item in digest.nodes if item.node_id == node_id), None)
        related = [event for event in digest.recent_events if node_id in event.entities]
        if node is None:
            return f"{node_id} is not present in the current snapshot."
        history = " ".join(f"{event.event_type}: {event.summary}." for event in related[-2:])
        return (
            f"{node_id} is {node.state} in role {node.role} on task {node.current_task_id or 'none'} "
            f"with {node.battery:.0%} battery and {node.localization} localization. {history}".strip()
        )
    return (
        f"Mission effectiveness is {digest.mission_effectiveness:.0%}, capability "
        f"{digest.mission_capability:.0%}, network health {digest.network_health:.0%}, "
        f"with {len(digest.objectives)} objective(s) and {digest.active_nodes} active node(s)."
    )
