/**
 * Wire types for the authoritative backend snapshot.
 *
 * These mirror `simulation/models.py`. The browser never invents any of these
 * values: every field here arrives over the WebSocket and is treated as truth.
 */

export type LocalVector = { x: number; y: number; z: number };

export type DroneStateName =
  | "IDLE" | "EXECUTING" | "DEGRADED" | "HOLDING" | "RETURNING" | "LOST" | "OFFLINE";

export type TaskTypeName =
  | "GOTO" | "WATCH" | "SEARCH" | "TRACE" | "FOLLOW" | "HOLD" | "RETURN" | "REGROUP" | "RELAY";

export type TaskStatusName =
  | "PENDING" | "ASSIGNED" | "IN_PROGRESS" | "COMPLETED" | "DEGRADED" | "CANCELLED";

export type LocalizationModeName = "GPS" | "DEGRADED_GPS" | "DEAD_RECKONING" | "UNKNOWN";

export type NodeResources = {
  mobility: boolean;
  compute_score: number;
  sensors: string[];
  communication_roles: string[];
  battery_powered: boolean;
  relay: boolean;
  storage_score: number;
};

export type NodeIdentity = {
  node_id: string;
  name: string;
  capabilities: string[];
  trust_level: string;
  authorization_level: string;
  metadata: Record<string, unknown>;
  resources: NodeResources;
};

export type LocalEstimatedState = {
  position: LocalVector;
  velocity: LocalVector;
  heading: number;
  position_uncertainty: number;
  localization_mode: LocalizationModeName;
  battery_estimate: number;
  sensor_confidence: number;
};

export type DroneTruthState = {
  node_id: string;
  position: LocalVector;
  velocity: LocalVector;
  /** Radians, counter-clockwise from local east: `atan2(vy, vx)`. */
  heading: number;
  actual_health: number;
  actual_battery: number;
  online: boolean;
};

export type PeerKnowledge = {
  node_id: string;
  last_seen: number;
  estimated_link_quality: number;
  last_position: LocalVector | null;
  state: DroneStateName;
  available: boolean;
};

export type PolicyResult = {
  allowed: boolean;
  reason_codes: string[];
  warnings: string[];
  recommended_action: string | null;
};

export type NodeAdaptiveState = {
  learned_cells: number;
  learned_mean_quality: number;
  observed_samples: number;
  merged_samples: number;
  peer_link_quality: Record<string, number>;
  predictions: Record<string, unknown>[];
  route_choice: Record<string, unknown> | null;
  handoffs: Record<string, unknown>[];
  explanations: Record<string, unknown>[];
};

export type RuntimeDrone = {
  identity: NodeIdentity;
  state: DroneStateName;
  estimated: LocalEstimatedState;
  truth: DroneTruthState;
  current_task_id: string | null;
  task_queue: string[];
  task_progress: number;
  peers: Record<string, PeerKnowledge>;
  current_plan: LocalVector[];
  /** Backend network role: "RELAY" while running a relay task, else "MISSION". */
  role: string;
  local_mission_revision: number;
  observation_count: number;
  known_cells: Record<string, number>;
  last_world_reconciliation: number | null;
  policy: PolicyResult;
  adaptive: NodeAdaptiveState;
};

export type RuntimeLink = {
  source_id: string;
  target_id: string;
  available: boolean;
  quality: number;
  latency_seconds: number;
  partitioned: boolean;
  distance_m: number;
  obstructed: boolean;
  packet_loss: number;
};

export type MissionTarget = {
  point: LocalVector | null;
  waypoints: LocalVector[];
  region_id: string | null;
  entity_id: string | null;
};

export type RuntimeTask = {
  id: string;
  type: TaskTypeName;
  target: MissionTarget;
  priority: number;
  required_capabilities: string[];
  desired_units: number;
  minimum_units: number;
  status: TaskStatusName;
  assigned_nodes: string[];
  progress: number;
  capability: number;
  effectiveness: number;
  effectiveness_components: Record<string, number>;
  created_at: number;
  metadata: Record<string, unknown>;
};

export type CoverageCell = {
  cell_id: string;
  region_id: string;
  center: LocalVector;
  size_m: number;
  last_observed: number | null;
  confidence: number;
  observed_by: string | null;
  observation_count: number;
  freshness: number;
};

export type RegionCoverage = {
  region_id: string;
  total_cells: number;
  observed_cells: number;
  fresh_cells: number;
  coverage: number;
  fresh_coverage: number;
  mean_confidence: number;
  cells: CoverageCell[];
};

export type WorldKnowledgeMetrics = {
  regions: RegionCoverage[];
  observation_count: number;
  mean_observation_confidence: number;
  world_model_sync_lag: number;
  duplicate_task_execution_count: number;
};

export type NetworkMetrics = {
  network_health: number;
  connected_components: string[][];
  largest_component_fraction: number;
  mean_link_quality: number;
  packet_loss_recent: number;
  active_nodes: number;
  degraded_nodes: number;
  relay_nodes: string[];
  gps_degraded_count: number;
  messaging: {
    adaptive: boolean;
    messages_attempted: number;
    messages_transmitted: number;
    messages_delivered: number;
    bytes_attempted: number;
    bytes_delivered: number;
    expired: number;
    coalesced: number;
    deduplicated: number;
    bandwidth_deferred: number;
    saturated_ticks: number;
    queue_depth: number;
    attempted_by_priority: Record<string, number>;
    delivered_by_priority: Record<string, number>;
    dropped_by_reason: Record<string, number>;
    critical_delivery_ratio: number;
    low_delivery_ratio: number;
  };
};

export type AdaptiveRuntimeState = {
  adaptive_messaging: boolean;
  communication_belief: boolean;
  communication_aware_routing: boolean;
  predictive_recovery: boolean;
  counterfactual_selection: boolean;
  multi_relay_coordination: boolean;
  communication_map: Array<{
    cell: string;
    quality: number;
    latency: number;
    success_rate: number;
    weight: number;
    samples: number;
    updated_at: number;
    sources: string[];
  }>;
  predictions: Record<string, unknown>[];
  decisions: Record<string, unknown>[];
  relay_stations: Record<string, unknown>[];
  counterfactual_runs: number;
  preemptive_relay_triggers: number;
  preemptive_handoffs: number;
  communication_route_changes: number;
  edge_compute: {
    enabled: boolean;
    edge_nodes: string[];
    profiles: Record<string, unknown>[];
    units_completed: number;
    executed_remotely: number;
    executed_locally: number;
    reassigned: number;
    orphaned: string[];
    failures: number;
    recent_units: Record<string, unknown>[];
  };
};

export type InterferenceKey =
  | "gps_interference" | "network_interference" | "sensor_interference" | "node_failure_rate";

export type InterferenceConfig = Record<InterferenceKey, number>;

export type RuntimeEvent = {
  sequence: number;
  timestamp: number;
  category: string;
  event_type: string;
  source: string;
  affected_entities: string[];
  human_readable_summary: string;
  payload: Record<string, unknown>;
};

export type RuntimeSnapshot = {
  type: string;
  simulation_time: number;
  running: boolean;
  scenario: string;
  drones: RuntimeDrone[];
  missions: RuntimeTask[];
  links: RuntimeLink[];
  interference: InterferenceConfig;
  mission_capability: number;
  mission_effectiveness: number;
  world_knowledge: WorldKnowledgeMetrics;
  network: NetworkMetrics;
  control_available: boolean;
  adaptive: AdaptiveRuntimeState;
  origin_lat: number;
  origin_lon: number;
  origin_alt: number;
  events: RuntimeEvent[];
};

/** Optional `GET /api/world` payload; runtime mode degrades cleanly without it. */
export type WorldRegion = { id: string; center: LocalVector; radius: number };
export type WorldBox = { id: string; minimum: LocalVector; maximum: LocalVector };
export type WorldCircle = { id: string; center: LocalVector; radius: number; height: number };
export type WorldEntity = { id: string; position: LocalVector; velocity: LocalVector };
export type WorldDefinition = {
  minimum: LocalVector;
  maximum: LocalVector;
  boxes: WorldBox[];
  circles: WorldCircle[];
  regions: WorldRegion[];
  entities: WorldEntity[];
};
