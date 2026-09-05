import type { LocalVector, RuntimeDrone, RuntimeSnapshot } from "./types";

/**
 * Presentation-only smoothing between authoritative backend snapshots.
 *
 * The backend owns physics. This buffer never advances a drone past the newest
 * snapshot it has received: it renders one snapshot interval in the past and
 * interpolates between two states the runtime actually published, so the worst
 * case is a frozen aircraft rather than an invented one. Cubic Hermite uses the
 * backend's own velocity vectors as tangents, so the curve passes exactly
 * through both backend positions with the backend's own direction of travel.
 */

export type PresentedDrone = {
  id: string;
  position: LocalVector;
  velocity: LocalVector;
  estimatedPosition: LocalVector;
  groundSpeed: number;
  verticalSpeed: number;
  /** Radians, counter-clockwise from east, matching the backend convention. */
  heading: number;
  /** Positive is a right bank, derived from the rate of turn. */
  bank: number;
  /** Positive is nose up, derived from climb rate. */
  pitch: number;
  /** True while this drone's newest snapshot is older than the stale window. */
  stale: boolean;
};

export type BufferOptions = {
  /** Ceiling on the render delay, in ms. */
  maximumBufferMs?: number;
  /** Interpolation is abandoned when a step exceeds this much unexplained motion. */
  teleportMarginM?: number;
  maximumBankRad?: number;
  maximumPitchRad?: number;
  /** Seconds for the heading/attitude low-pass to cover ~63% of a step. */
  attitudeTimeConstant?: number;
};

type Frame = { drones: Map<string, RuntimeDrone>; receivedAt: number; simulationTime: number };

type Smoothed = { heading: number; bank: number; pitch: number };

const zero = (): LocalVector => ({ x: 0, y: 0, z: 0 });

export const wrapAngle = (angle: number): number => {
  const wrapped = (angle + Math.PI) % (Math.PI * 2);
  return (wrapped < 0 ? wrapped + Math.PI * 2 : wrapped) - Math.PI;
};

/** Signed shortest rotation from `from` to `to`, in (-pi, pi]. */
export const angleDelta = (from: number, to: number): number => wrapAngle(to - from);

export const lerpAngle = (from: number, to: number, t: number): number =>
  wrapAngle(from + angleDelta(from, to) * t);

const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value));

/** Cubic Hermite through p0 and p1 with backend velocities as tangents. */
export function hermite(
  p0: LocalVector, v0: LocalVector, p1: LocalVector, v1: LocalVector, t: number, dt: number,
): LocalVector {
  const t2 = t * t;
  const t3 = t2 * t;
  const h00 = 2 * t3 - 3 * t2 + 1;
  const h10 = t3 - 2 * t2 + t;
  const h01 = -2 * t3 + 3 * t2;
  const h11 = t3 - t2;
  return {
    x: h00 * p0.x + h10 * dt * v0.x + h01 * p1.x + h11 * dt * v1.x,
    y: h00 * p0.y + h10 * dt * v0.y + h01 * p1.y + h11 * dt * v1.y,
    z: h00 * p0.z + h10 * dt * v0.z + h01 * p1.z + h11 * dt * v1.z,
  };
}

const lerpVector = (a: LocalVector, b: LocalVector, t: number): LocalVector => ({
  x: a.x + (b.x - a.x) * t,
  y: a.y + (b.y - a.y) * t,
  z: a.z + (b.z - a.z) * t,
});

const distance = (a: LocalVector, b: LocalVector) => Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z);

export class PresentationBuffer {
  private previous?: Frame;
  private current?: Frame;
  private intervalMs = 200;
  private lastSampleAt = 0;
  private readonly smoothed = new Map<string, Smoothed>();
  private readonly options: Required<BufferOptions>;

  constructor(options: BufferOptions = {}) {
    this.options = {
      maximumBufferMs: options.maximumBufferMs ?? 450,
      teleportMarginM: options.teleportMarginM ?? 25,
      maximumBankRad: options.maximumBankRad ?? 0.42,
      maximumPitchRad: options.maximumPitchRad ?? 0.26,
      attitudeTimeConstant: options.attitudeTimeConstant ?? 0.28,
    };
  }

  /** Records a snapshot. `receivedAt` is a client clock reading in ms. */
  ingest(snapshot: RuntimeSnapshot, receivedAt: number): void {
    const frame: Frame = {
      drones: new Map(snapshot.drones.map((drone) => [drone.identity.node_id, drone])),
      receivedAt,
      simulationTime: snapshot.simulation_time,
    };
    if (this.current) {
      const gap = receivedAt - this.current.receivedAt;
      // A backend reset rewinds simulation time; drop history rather than
      // interpolate across the discontinuity.
      if (snapshot.simulation_time < this.current.simulationTime) {
        this.previous = undefined;
        this.smoothed.clear();
      } else {
        this.previous = this.current;
        if (gap > 20 && gap < 2000) this.intervalMs = this.intervalMs * 0.7 + gap * 0.3;
      }
    }
    this.current = frame;
  }

  get renderDelayMs(): number {
    return Math.min(this.intervalMs, this.options.maximumBufferMs);
  }

  get connected(): boolean {
    return this.current !== undefined;
  }

  /** Interpolated presentation state for every drone in the newest snapshot. */
  sample(now: number): Map<string, PresentedDrone> {
    const result = new Map<string, PresentedDrone>();
    const current = this.current;
    if (!current) return result;

    const stepSeconds = this.lastSampleAt ? clamp((now - this.lastSampleAt) / 1000, 1 / 240, 0.5) : 1 / 60;
    this.lastSampleAt = now;

    const previous = this.previous;
    const renderAt = now - this.renderDelayMs;
    const span = previous ? current.receivedAt - previous.receivedAt : 0;
    const alpha = previous && span > 0 ? clamp((renderAt - previous.receivedAt) / span, 0, 1) : 1;
    const dt = previous ? Math.max(1e-3, current.simulationTime - previous.simulationTime) : 0;
    const stale = now - current.receivedAt > Math.max(1500, this.intervalMs * 6);

    for (const [id, drone] of current.drones) {
      const before = previous?.drones.get(id);
      let position = drone.truth.position;
      let velocity = drone.truth.velocity;
      let estimated = drone.estimated.position;

      if (before && alpha < 1) {
        const travelled = Math.max(
          Math.hypot(before.truth.velocity.x, before.truth.velocity.y, before.truth.velocity.z),
          Math.hypot(drone.truth.velocity.x, drone.truth.velocity.y, drone.truth.velocity.z),
        ) * dt;
        const jump = distance(before.truth.position, drone.truth.position);
        if (jump <= travelled + this.options.teleportMarginM) {
          position = hermite(before.truth.position, before.truth.velocity, drone.truth.position, drone.truth.velocity, alpha, dt);
          velocity = lerpVector(before.truth.velocity, drone.truth.velocity, alpha);
          estimated = lerpVector(before.estimated.position, drone.estimated.position, alpha);
        }
      }

      const groundSpeed = Math.hypot(velocity.x, velocity.y);
      const target = groundSpeed > 0.35 ? Math.atan2(velocity.y, velocity.x) : drone.truth.heading;
      const state = this.smoothed.get(id) ?? { heading: target, bank: 0, pitch: 0 };
      const blend = 1 - Math.exp(-stepSeconds / this.options.attitudeTimeConstant);
      const turned = angleDelta(state.heading, target) * blend;
      state.heading = wrapAngle(state.heading + turned);

      // Bank opposes the turn direction: heading here grows counter-clockwise,
      // so a right turn is negative and must produce a positive (right) bank.
      const turnRate = turned / stepSeconds;
      const bankTarget = clamp(
        -turnRate * clamp(groundSpeed / 12, 0.25, 1.6) * 1.35,
        -this.options.maximumBankRad, this.options.maximumBankRad,
      );
      const pitchTarget = clamp(
        Math.atan2(velocity.z, Math.max(groundSpeed, 0.5)) * 0.6,
        -this.options.maximumPitchRad, this.options.maximumPitchRad,
      );
      state.bank += (bankTarget - state.bank) * blend;
      state.pitch += (pitchTarget - state.pitch) * blend;
      this.smoothed.set(id, state);

      result.set(id, {
        id,
        position,
        velocity,
        estimatedPosition: estimated,
        groundSpeed,
        verticalSpeed: velocity.z,
        heading: state.heading,
        bank: state.bank,
        pitch: state.pitch,
        stale,
      });
    }

    for (const id of [...this.smoothed.keys()]) {
      if (!current.drones.has(id)) this.smoothed.delete(id);
    }
    return result;
  }

  reset(): void {
    this.previous = undefined;
    this.current = undefined;
    this.smoothed.clear();
    this.lastSampleAt = 0;
  }
}

export const zeroVector = zero;
