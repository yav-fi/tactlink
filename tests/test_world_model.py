from __future__ import annotations

from simulation.models import (
    InterferenceConfig,
    MissionCommand,
    MissionTarget,
    Observation,
    ObservationType,
    TaskType,
    Vector3,
)
from simulation.world_model import CoverageGrid, WorldBelief

from .test_simulation import engine_for_test


def cell_observation(source: str, timestamp: float, confidence: float = 0.8) -> Observation:
    return Observation(
        observation_id=f"{source}-{timestamp}-{confidence}",
        source_node_id=source,
        timestamp=timestamp,
        observation_type=ObservationType.CELL_OBSERVED,
        domain_key="cell:R:R:+0:+0",
        geometry={"region_id": "R", "cell_id": "R:+0:+0"},
        confidence=confidence,
    )


def test_coverage_and_freshness_come_from_observations() -> None:
    belief = WorldBelief([CoverageGrid("R", Vector3(), 10, 10)])
    belief.incorporate(cell_observation("a", 0))
    initial = belief.coverage("R", 0, 10)
    stale = belief.coverage("R", 20, 10)
    assert initial and stale
    assert initial.coverage > 0
    assert initial.fresh_coverage == initial.coverage
    assert stale.coverage == initial.coverage
    assert stale.fresh_coverage == 0


def test_reconciliation_prefers_newer_then_confident_observation() -> None:
    belief = WorldBelief([CoverageGrid("R", Vector3(), 10, 10)])
    belief.incorporate(cell_observation("a", 2, 0.4))
    belief.incorporate(cell_observation("b", 1, 0.99))
    belief.incorporate(cell_observation("c", 2, 0.9))
    assert belief.domain_latest["cell:R:R:+0:+0"].source_node_id == "c"


def test_observation_waits_for_network_reconnection() -> None:
    engine = engine_for_test(drone_count=3)
    engine.set_interference(InterferenceConfig())
    engine.network.partition({"drone-1"}, {"drone-2", "drone-3"}, until=20, now=engine.time)
    first = engine.drones["drone-1"]
    observation = first.create_observation(
        engine.time,
        ObservationType.ENTITY_OBSERVED,
        "entity:test",
        {"position": Vector3(x=1).model_dump(mode="json")},
        0.9,
        1.0,
        {"entity_id": "test"},
    )
    engine.run_steps(15, 0.1)
    assert observation.observation_id not in engine.drones["drone-2"].world_belief.observations
    engine.network.reconnect_all(engine.time)
    engine.run_steps(15, 0.1)
    assert observation.observation_id in engine.drones["drone-2"].world_belief.observations


def test_search_effectiveness_tracks_actual_sensed_coverage() -> None:
    engine = engine_for_test(drone_count=4)
    task = engine.submit_mission(
        MissionCommand(
            type=TaskType.SEARCH,
            target=MissionTarget(region_id="ALPHA"),
            required_capabilities={"camera"},
            desired_units=2,
        )
    )
    before = task.effectiveness
    engine.run_steps(250, 0.1)
    coverage = next(region for region in engine.snapshot().world_knowledge.regions if region.region_id == "ALPHA")
    assert coverage.coverage > 0
    assert task.progress == coverage.coverage
    assert task.effectiveness > before


def test_watch_effectiveness_falls_when_information_ages() -> None:
    belief = WorldBelief([CoverageGrid("R", Vector3(), 10, 10)])
    belief.incorporate(cell_observation("a", 0, 1.0))
    assert belief.latest_region_observation("R") is not None
    assert CoverageGrid.freshness(30, 10) < CoverageGrid.freshness(1, 10)
