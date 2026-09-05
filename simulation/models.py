"""Canonical, JSON-serializable integration contracts."""

from __future__ import annotations

from enum import StrEnum
from math import sqrt
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class Vector3(BaseModel):
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    def distance_to(self, other: "Vector3") -> float:
        return sqrt((self.x - other.x) ** 2 + (self.y - other.y) ** 2 + (self.z - other.z) ** 2)

    def moved(self, velocity: "Vector3", dt: float) -> "Vector3":
        return Vector3(x=self.x + velocity.x * dt, y=self.y + velocity.y * dt, z=self.z + velocity.z * dt)


class DroneState(StrEnum):
    IDLE = "IDLE"
    EXECUTING = "EXECUTING"
    DEGRADED = "DEGRADED"
    HOLDING = "HOLDING"
    RETURNING = "RETURNING"
    LOST = "LOST"
    OFFLINE = "OFFLINE"


class LocalizationMode(StrEnum):
    GPS = "GPS"
    DEGRADED_GPS = "DEGRADED_GPS"
    DEAD_RECKONING = "DEAD_RECKONING"
    UNKNOWN = "UNKNOWN"


class TaskType(StrEnum):
    GOTO = "GOTO"
    WATCH = "WATCH"
    SEARCH = "SEARCH"
    TRACE = "TRACE"
    FOLLOW = "FOLLOW"
    HOLD = "HOLD"
    RETURN = "RETURN"
    REGROUP = "REGROUP"
    RELAY = "RELAY"


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    ASSIGNED = "ASSIGNED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    DEGRADED = "DEGRADED"
    CANCELLED = "CANCELLED"


class MessageType(StrEnum):
    HEARTBEAT = "HEARTBEAT"
    STATUS = "STATUS"
    TASK_ASSIGNMENT = "TASK_ASSIGNMENT"
    TASK_ACK = "TASK_ACK"
    PEER_STATE = "PEER_STATE"
    MISSION_STATE = "MISSION_STATE"
    REPLAN_REQUEST = "REPLAN_REQUEST"
    POSITION_UPDATE = "POSITION_UPDATE"
    CAPABILITY_UPDATE = "CAPABILITY_UPDATE"
    TASK_COMPLETE = "TASK_COMPLETE"
    MISSION_ANNOUNCE = "MISSION_ANNOUNCE"
    TASK_BID = "TASK_BID"
    TASK_AWARD = "TASK_AWARD"
    TASK_RELEASE = "TASK_RELEASE"
    MISSION_SYNC = "MISSION_SYNC"
    WORLD_UPDATE = "WORLD_UPDATE"


class ObservationType(StrEnum):
    REGION_OBSERVED = "REGION_OBSERVED"
    CELL_OBSERVED = "CELL_OBSERVED"
    ENTITY_OBSERVED = "ENTITY_OBSERVED"
    OBSTACLE_OBSERVED = "OBSTACLE_OBSERVED"
    LINK_OBSERVED = "LINK_OBSERVED"
    ROUTE_OBSERVED = "ROUTE_OBSERVED"


class EventCategory(StrEnum):
    SIMULATION = "SIMULATION"
    MISSION = "MISSION"
    NETWORK = "NETWORK"
    LOCALIZATION = "LOCALIZATION"
    FAILURE = "FAILURE"
    AUTONOMY = "AUTONOMY"
    ALLOCATION = "ALLOCATION"
    KNOWLEDGE = "KNOWLEDGE"


class EventType(StrEnum):
    SIMULATION_STARTED = "SIMULATION_STARTED"
    SIMULATION_PAUSED = "SIMULATION_PAUSED"
    SIMULATION_RESUMED = "SIMULATION_RESUMED"
    SIMULATION_RESET = "SIMULATION_RESET"
    NODE_STARTED = "NODE_STARTED"
    NODE_FAILED = "NODE_FAILED"
    NODE_RECOVERED = "NODE_RECOVERED"
    HEARTBEAT_TIMEOUT = "HEARTBEAT_TIMEOUT"
    GPS_DEGRADED = "GPS_DEGRADED"
    GPS_LOST = "GPS_LOST"
    GPS_RECOVERED = "GPS_RECOVERED"
    LINK_DEGRADED = "LINK_DEGRADED"
    MESSAGE_DROPPED = "MESSAGE_DROPPED"
    NETWORK_PARTITION = "NETWORK_PARTITION"
    NETWORK_RECONNECTED = "NETWORK_RECONNECTED"
    TASK_CREATED = "TASK_CREATED"
    TASK_ASSIGNED = "TASK_ASSIGNED"
    TASK_REASSIGNED = "TASK_REASSIGNED"
    TASK_COMPLETED = "TASK_COMPLETED"
    MISSION_CAPABILITY_CHANGED = "MISSION_CAPABILITY_CHANGED"
    REPLAN_REQUESTED = "REPLAN_REQUESTED"
    INTERFERENCE_CHANGED = "INTERFERENCE_CHANGED"
    RANDOM_EVENT = "RANDOM_EVENT"
    CONTROL_LOST = "CONTROL_LOST"
    CONTROL_RECOVERED = "CONTROL_RECOVERED"
    MISSION_REPLICATED = "MISSION_REPLICATED"
    TASK_AUCTION_STARTED = "TASK_AUCTION_STARTED"
    TASK_BID = "TASK_BID"
    TASK_AUCTION_WON = "TASK_AUCTION_WON"
    TASK_RELEASED = "TASK_RELEASED"
    NETWORK_PARTITION_RISK = "NETWORK_PARTITION_RISK"
    NETWORK_HEALED = "NETWORK_HEALED"
    RELAY_TASK_CREATED = "RELAY_TASK_CREATED"
    RELAY_REPOSITIONING = "RELAY_REPOSITIONING"
    RELAY_ESTABLISHED = "RELAY_ESTABLISHED"
    LINK_QUALITY_CHANGED = "LINK_QUALITY_CHANGED"
    OBSERVATION_CREATED = "OBSERVATION_CREATED"
    OBSERVATION_SHARED = "OBSERVATION_SHARED"
    WORLD_MODEL_UPDATED = "WORLD_MODEL_UPDATED"
    WORLD_MODEL_RECONCILED = "WORLD_MODEL_RECONCILED"
    COVERAGE_CHANGED = "COVERAGE_CHANGED"
    INFORMATION_STALE = "INFORMATION_STALE"
    MISSION_EFFECTIVENESS_CHANGED = "MISSION_EFFECTIVENESS_CHANGED"
    OBJECTIVE_DEGRADED = "OBJECTIVE_DEGRADED"
    OBJECTIVE_RECOVERED = "OBJECTIVE_RECOVERED"
    RESOURCE_REPRIORITIZED = "RESOURCE_REPRIORITIZED"
    LEASE_GRANTED = "LEASE_GRANTED"
    LEASE_EXPIRED = "LEASE_EXPIRED"
    LEASE_CONFLICT_RESOLVED = "LEASE_CONFLICT_RESOLVED"
    WORKER_CONNECTED = "WORKER_CONNECTED"
    WORKER_DISCONNECTED = "WORKER_DISCONNECTED"
    SIGNATURE_REJECTED = "SIGNATURE_REJECTED"
    REPLAY_STARTED = "REPLAY_STARTED"
    REPLAY_FINISHED = "REPLAY_FINISHED"


class TrustLevel(StrEnum):
    UNVERIFIED = "UNVERIFIED"
    TRUSTED = "TRUSTED"


class NodeCapabilities(BaseModel):
    mobility: bool = True
    compute_score: float = Field(default=1.0, ge=0.0)
    sensors: set[str] = Field(default_factory=set)
    communication_roles: set[str] = Field(default_factory=lambda: {"peer"})
    battery_powered: bool = True
    relay: bool = False
    storage_score: float = Field(default=1.0, ge=0.0)


class PolicyResult(BaseModel):
    allowed: bool = True
    reason_codes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    recommended_action: str | None = None


class MissionTarget(BaseModel):
    point: Vector3 | None = None
    waypoints: list[Vector3] = Field(default_factory=list)
    region_id: str | None = None
    entity_id: str | None = None


class MissionCommand(BaseModel):
    """Canonical command accepted from LLM, UI, mobile, or gesture adapters."""

    type: TaskType
    target: MissionTarget
    priority: int = Field(default=50, ge=0, le=100)
    required_capabilities: set[str] = Field(default_factory=set)
    desired_units: int = Field(default=1, ge=1)
    minimum_units: int = Field(default=1, ge=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskLease(BaseModel):
    task_id: str
    owner: str
    lease_id: str
    lease_expires: float
    revision: int


class MissionTask(BaseModel):
    id: str = Field(default_factory=lambda: f"task-{uuid4().hex[:10]}")
    type: TaskType
    target: MissionTarget
    priority: int = 50
    required_capabilities: set[str] = Field(default_factory=set)
    desired_units: int = 1
    minimum_units: int = 1
    status: TaskStatus = TaskStatus.PENDING
    assigned_nodes: list[str] = Field(default_factory=list)
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    capability: float = Field(default=0.0, ge=0.0, le=1.0)
    effectiveness: float = Field(default=0.0, ge=0.0, le=1.0)
    effectiveness_components: dict[str, float] = Field(default_factory=dict)
    leases: list[TaskLease] = Field(default_factory=list)
    created_at: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class GPSMeasurement(BaseModel):
    timestamp: float
    position: Vector3
    accuracy_meters: float
    available: bool = True


class LocalEstimatedState(BaseModel):
    position: Vector3
    velocity: Vector3 = Vector3()
    heading: float = 0.0
    position_uncertainty: float = 1.5
    localization_mode: LocalizationMode = LocalizationMode.GPS
    battery_estimate: float = 1.0
    sensor_confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class PeerKnowledge(BaseModel):
    node_id: str
    last_seen: float
    estimated_link_quality: float = 1.0
    last_position: Vector3 | None = None
    state: DroneState = DroneState.IDLE
    available: bool = True


class NodeIdentity(BaseModel):
    node_id: str
    name: str
    capabilities: set[str] = Field(default_factory=set)
    public_key: str | None = None
    trust_level: TrustLevel = TrustLevel.UNVERIFIED
    authorization_level: str = "simulation"
    metadata: dict[str, Any] = Field(default_factory=dict)
    resources: NodeCapabilities = Field(default_factory=NodeCapabilities)


class MotionIntent(BaseModel):
    target: Vector3 | None = None
    maximum_speed: float = 0.0
    hold: bool = False


class NetworkMessage(BaseModel):
    message_id: str = Field(default_factory=lambda: f"msg-{uuid4().hex[:12]}")
    sender_id: str
    recipient_id: str | None = None
    timestamp_sent: float
    type: MessageType
    payload: dict[str, Any] = Field(default_factory=dict)
    sequence_number: int = 0
    signature: str | None = None


class Observation(BaseModel):
    observation_id: str
    source_node_id: str
    timestamp: float
    observation_type: ObservationType
    domain_key: str
    geometry: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    uncertainty: float = Field(default=0.0, ge=0.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CoverageCell(BaseModel):
    cell_id: str
    region_id: str
    center: Vector3
    size_m: float
    last_observed: float | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    observed_by: str | None = None
    observation_count: int = 0
    freshness: float = Field(default=0.0, ge=0.0, le=1.0)


class RegionCoverage(BaseModel):
    region_id: str
    total_cells: int = 0
    observed_cells: int = 0
    fresh_cells: int = 0
    coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    fresh_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    mean_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    cells: list[CoverageCell] = Field(default_factory=list)


class WorldKnowledgeMetrics(BaseModel):
    regions: list[RegionCoverage] = Field(default_factory=list)
    observation_count: int = 0
    mean_observation_confidence: float = 0.0
    world_model_sync_lag: float = 0.0
    duplicate_task_execution_count: int = 0


class DroneStatusReport(BaseModel):
    node_id: str
    timestamp: float
    state: DroneState
    estimated: LocalEstimatedState
    current_task_id: str | None
    workload: int
    capabilities: set[str]


class DroneTruthState(BaseModel):
    """Ground truth emitted by World for operator visualization, never node autonomy."""

    node_id: str
    position: Vector3
    velocity: Vector3
    heading: float
    actual_health: float
    actual_battery: float
    online: bool


class DronePublicState(BaseModel):
    identity: NodeIdentity
    state: DroneState
    estimated: LocalEstimatedState
    truth: DroneTruthState
    current_task_id: str | None
    task_queue: list[str]
    task_progress: float
    peers: dict[str, PeerKnowledge]
    current_plan: list[Vector3] = Field(default_factory=list)
    role: str = "MISSION"
    local_mission_revision: int = 0
    observation_count: int = 0
    known_cells: dict[str, int] = Field(default_factory=dict)
    last_world_reconciliation: float | None = None
    policy: PolicyResult = Field(default_factory=PolicyResult)


class LinkState(BaseModel):
    source_id: str
    target_id: str
    available: bool
    quality: float
    latency_seconds: float
    partitioned: bool = False
    distance_m: float = 0.0
    obstructed: bool = False
    packet_loss: float = 0.0


class NetworkMetrics(BaseModel):
    network_health: float = Field(default=1.0, ge=0.0, le=1.0)
    connected_components: list[list[str]] = Field(default_factory=list)
    largest_component_fraction: float = Field(default=1.0, ge=0.0, le=1.0)
    mean_link_quality: float = Field(default=1.0, ge=0.0, le=1.0)
    packet_loss_recent: float = Field(default=0.0, ge=0.0, le=1.0)
    active_nodes: int = 0
    degraded_nodes: int = 0
    relay_nodes: list[str] = Field(default_factory=list)
    gps_degraded_count: int = 0


class InterferenceConfig(BaseModel):
    gps_interference: float = Field(default=0.0, ge=0.0, le=1.0)
    network_interference: float = Field(default=0.0, ge=0.0, le=1.0)
    sensor_interference: float = Field(default=0.0, ge=0.0, le=1.0)
    node_failure_rate: float = Field(default=0.0, ge=0.0, le=1.0)


class ScenarioEvent(BaseModel):
    id: str = Field(default_factory=lambda: f"scenario-{uuid4().hex[:10]}")
    timestamp: float
    type: str
    affected_nodes: list[str] = Field(default_factory=list)
    severity: float = Field(default=0.0, ge=0.0, le=1.0)
    duration: float = Field(default=0.0, ge=0.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SimulationEvent(BaseModel):
    sequence: int = 0
    timestamp: float
    category: EventCategory
    event_type: EventType
    source: str
    affected_entities: list[str] = Field(default_factory=list)
    human_readable_summary: str
    payload: dict[str, Any] = Field(default_factory=dict)


class SimulationSnapshot(BaseModel):
    type: str = "state"
    simulation_time: float
    running: bool
    scenario: str
    drones: list[DronePublicState]
    missions: list[MissionTask]
    links: list[LinkState]
    interference: InterferenceConfig
    mission_capability: float
    mission_effectiveness: float = Field(default=0.0, ge=0.0, le=1.0)
    world_knowledge: WorldKnowledgeMetrics = Field(default_factory=WorldKnowledgeMetrics)
    network: NetworkMetrics = Field(default_factory=NetworkMetrics)
    control_available: bool = True
    origin_lat: float = 38.8895
    origin_lon: float = -77.0353
    origin_alt: float = 20.0
    events: list[SimulationEvent] = Field(default_factory=list)


class OperatorPositionUpdate(BaseModel):
    operator_id: str
    position: Vector3
    accuracy_meters: float
    timestamp: float
