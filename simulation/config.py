"""Runtime configuration with explicit, reproducible defaults."""

from pydantic import BaseModel, Field


class AllocatorWeights(BaseModel):
    distance: float = 1.0
    battery: float = 35.0
    workload: float = 50.0
    communication: float = 25.0
    uncertainty: float = 2.0
    capability_mismatch: float = 1_000_000.0


class NetworkConfig(BaseModel):
    base_latency: float = 0.06
    jitter: float = 0.03
    base_packet_loss: float = 0.0
    bandwidth_messages_per_tick: int = Field(default=100, ge=0)
    duplication_probability: float = 0.0
    reference_range_m: float = Field(default=220.0, gt=0)
    hard_range_m: float = Field(default=650.0, gt=0)
    distance_falloff_power: float = Field(default=2.0, gt=0)
    obstruction_penalty: float = Field(default=0.45, ge=0.0, le=1.0)
    minimum_usable_quality: float = Field(default=0.08, ge=0.0, le=1.0)
    healthy_link_quality: float = Field(default=0.35, ge=0.0, le=1.0)
    poor_link_latency_seconds: float = Field(default=0.8, ge=0.0)
    adaptive_messaging: bool = True
    bandwidth_bytes_per_tick: int = Field(default=0, ge=0)
    maximum_queue_messages: int = Field(default=2048, ge=1)
    deduplication_window_seconds: float = Field(default=0.75, ge=0.0)


class LocalizationConfig(BaseModel):
    nominal_uncertainty: float = 1.5
    gps_noise_meters: float = 1.0
    degraded_noise_meters: float = 8.0
    dead_reckoning_growth_mps: float = 1.2
    gps_outage_threshold: float = 0.72
    convergence_rate: float = 0.35


class SensingConfig(BaseModel):
    observation_radius_m: float = Field(default=32.0, gt=0)
    cell_size_m: float = Field(default=12.0, gt=0)
    update_interval_seconds: float = Field(default=0.8, gt=0)
    confidence_falloff: float = Field(default=0.55, ge=0.0, le=1.0)
    freshness_half_life_seconds: float = Field(default=18.0, gt=0)


class SecurityConfig(BaseModel):
    enabled: bool = False


class CommunicationBeliefConfig(BaseModel):
    """Node-local learned connectivity map (never simulator RF truth)."""

    enabled: bool = True
    cell_size_m: float = Field(default=40.0, gt=0)
    sample_interval_seconds: float = Field(default=1.0, gt=0)
    half_life_seconds: float = Field(default=45.0, gt=0)
    minimum_samples: int = Field(default=2, ge=1)
    share_interval_seconds: float = Field(default=3.0, gt=0)
    maximum_shared_cells: int = Field(default=24, ge=1)


class PredictionConfig(BaseModel):
    """Deterministic trend extrapolation thresholds."""

    enabled: bool = True
    window_seconds: float = Field(default=8.0, gt=0)
    minimum_samples: int = Field(default=4, ge=2)
    minimum_confidence: float = Field(default=0.55, ge=0.0, le=1.0)
    battery_reserve: float = Field(default=0.18, ge=0.0, le=1.0)
    battery_horizon_seconds: float = Field(default=45.0, gt=0)
    link_failure_threshold: float = Field(default=0.30, ge=0.0, le=1.0)
    link_horizon_seconds: float = Field(default=12.0, gt=0)
    partition_horizon_seconds: float = Field(default=12.0, gt=0)
    alert_cooldown_seconds: float = Field(default=5.0, gt=0)
    preemptive_handoff: bool = True


class CounterfactualConfig(BaseModel):
    """Bounded shadow forecasting on meaningful decision events only."""

    enabled: bool = True
    horizon_seconds: float = Field(default=12.0, gt=0)
    step_seconds: float = Field(default=2.0, gt=0)
    maximum_candidates: int = Field(default=6, ge=1)
    minimum_margin: float = Field(default=0.02, ge=0.0)
    cache_seconds: float = Field(default=2.0, ge=0.0)


class EdgeComputeConfig(BaseModel):
    """Optional external compute contribution; the core mission never needs it."""

    enabled: bool = True
    assignment_timeout_seconds: float = Field(default=4.0, gt=0)
    maximum_attempts: int = Field(default=3, ge=1)


class AdaptiveConfig(BaseModel):
    """Feature switches used by the ablation benchmark."""

    communication_aware_routing: bool = True
    multi_relay_coordination: bool = True
    communication: CommunicationBeliefConfig = CommunicationBeliefConfig()
    prediction: PredictionConfig = PredictionConfig()
    counterfactual: CounterfactualConfig = CounterfactualConfig()
    edge_compute: EdgeComputeConfig = EdgeComputeConfig()


class SimulationConfig(BaseModel):
    seed: int = 49281
    tick_rate_hz: float = Field(default=20.0, gt=0)
    snapshot_rate_hz: float = Field(default=5.0, gt=0)
    heartbeat_interval: float = 0.5
    status_interval: float = 1.0
    peer_timeout: float = 3.0
    recent_event_limit: int = 500
    default_speed_mps: float = 12.0
    battery_drain_per_meter: float = 0.00008
    auction_window_seconds: float = Field(default=0.8, gt=0)
    auction_rebroadcast_seconds: float = Field(default=0.25, gt=0)
    task_lease_seconds: float = Field(default=4.0, gt=0)
    relay_evaluation_seconds: float = Field(default=1.0, gt=0)
    relay_health_threshold: float = Field(default=0.58, ge=0.0, le=1.0)
    allocator: AllocatorWeights = AllocatorWeights()
    network: NetworkConfig = NetworkConfig()
    localization: LocalizationConfig = LocalizationConfig()
    sensing: SensingConfig = SensingConfig()
    security: SecurityConfig = SecurityConfig()
    adaptive: AdaptiveConfig = AdaptiveConfig()

    @property
    def tick_seconds(self) -> float:
        return 1.0 / self.tick_rate_hz
