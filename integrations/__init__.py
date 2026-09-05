"""Adapters that feed external inputs into the canonical mission runtime.

Modules are intentionally not imported eagerly so ``python -m
integrations.llm_mission`` starts without runpy warnings or optional model work.
"""

__all__ = [
    "gesture_mission",
    "llm_mission",
    "mission_client",
]
