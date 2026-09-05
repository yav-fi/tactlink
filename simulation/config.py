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
    bandwidth_messages_per_tick: int = 100
    duplication_probability: float = 0.0


class LocalizationConfig(BaseModel):
    nominal_uncertainty: float = 1.5
    gps_noise_meters: float = 1.0
    degraded_noise_meters: float = 8.0
    dead_reckoning_growth_mps: float = 1.2
    gps_outage_threshold: float = 0.72
    convergence_rate: float = 0.35


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
    allocator: AllocatorWeights = AllocatorWeights()
    network: NetworkConfig = NetworkConfig()
    localization: LocalizationConfig = LocalizationConfig()

    @property
    def tick_seconds(self) -> float:
        return 1.0 / self.tick_rate_hz

