"""Multimodal operator context and the deterministic grounding of deixis.

The model never invents a location.  It may only emit one of the reference
tokens in ``ContextReference``; this module substitutes the real value from
whatever the operator interface actually supplied.  A reference with no
backing context is a clarification, never a guess.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from simulation.mission_plan import ContextReference
from simulation.models import Vector3


class UnitAvailability(BaseModel):
    """What the operator interface believes is currently assignable."""

    total_units: int = 0
    available_units: int = 0
    capability_counts: dict[str, int] = Field(default_factory=dict)

    def units_with(self, capabilities: set[str]) -> int:
        if not capabilities:
            return self.available_units
        return min(self.capability_counts.get(name, 0) for name in sorted(capabilities))


class OperatorContext(BaseModel):
    """One fused operator turn: speech plus whatever else was on screen."""

    utterance: str = ""
    selected_region: str | None = None
    map_cursor: Vector3 | None = None
    gesture: str | None = None
    operator_position: Vector3 | None = None
    selected_drone_id: str | None = None
    known_regions: list[str] = Field(default_factory=list)
    known_entities: list[str] = Field(default_factory=list)
    known_capabilities: list[str] = Field(default_factory=list)
    availability: UnitAvailability = Field(default_factory=UnitAvailability)
    timestamp: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)

    def resolve(self, reference: ContextReference) -> Any | None:
        if reference == ContextReference.SELECTED_REGION:
            return self.canonical_region(self.selected_region)
        if reference == ContextReference.MAP_CURSOR:
            return self.map_cursor
        if reference == ContextReference.OPERATOR_POSITION:
            return self.operator_position
        if reference == ContextReference.SELECTED_DRONE:
            return self.selected_drone_id
        return None

    def canonical_region(self, region_id: str | None) -> str | None:
        """Match a spoken region name to a known region id, case-insensitively."""
        if not region_id:
            return None
        wanted = region_id.strip().lower()
        if not self.known_regions:
            return region_id.strip()
        for known in self.known_regions:
            if known.lower() == wanted:
                return known
        # "sector alpha" / "the alpha region" still names ALPHA.
        for known in self.known_regions:
            if known.lower() in wanted.split() or known.lower() in wanted:
                return known
        return None

    def canonical_entity(self, entity_id: str | None) -> str | None:
        if not entity_id:
            return None
        wanted = entity_id.strip().lower().replace(" ", "-").replace("_", "-")
        for known in self.known_entities:
            if known.lower() == wanted:
                return known
        return wanted

    def available_reference_tokens(self) -> list[str]:
        tokens: list[str] = []
        if self.selected_region:
            tokens.append(ContextReference.SELECTED_REGION.value)
        if self.map_cursor is not None:
            tokens.append(ContextReference.MAP_CURSOR.value)
        if self.operator_position is not None:
            tokens.append(ContextReference.OPERATOR_POSITION.value)
        if self.selected_drone_id:
            tokens.append(ContextReference.SELECTED_DRONE.value)
        return tokens

    def render_for_model(self) -> str:
        """A compact, explicitly delimited context block for the prompt."""
        lines = [
            f"selected_region: {self.selected_region or 'NONE'}",
            f"map_cursor: {self.map_cursor.model_dump() if self.map_cursor else 'NONE'}",
            f"operator_position: {self.operator_position.model_dump() if self.operator_position else 'NONE'}",
            f"selected_drone_id: {self.selected_drone_id or 'NONE'}",
            f"gesture: {self.gesture or 'NONE'}",
            f"known_regions: {self.known_regions or 'NONE'}",
            f"known_entities: {self.known_entities or 'NONE'}",
            f"known_capabilities: {self.known_capabilities or 'NONE'}",
            f"available_units: {self.availability.available_units}",
            f"usable_reference_tokens: {self.available_reference_tokens() or 'NONE'}",
        ]
        return "\n".join(lines)
