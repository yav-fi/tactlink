import * as Cesium from "cesium";

// Compact gameplay hitbox; release/replay prisms are enlarged visual markers.
export const DRONE_CLEARANCE = 0.5;
type RayScene = Cesium.Scene & {
  view?: unknown;
  pickFromRay(ray: Cesium.Ray, exclude: Cesium.Entity[], width: number): { position?: Cesium.Cartesian3 } | undefined;
};
const queryWarnings = new WeakMap<Cesium.Viewer, string>();
export function collisionWarning(viewer: Cesium.Viewer): string | undefined { return queryWarnings.get(viewer); }

export type AvoidanceMove = {
  position: Cesium.Cartesian3;
  detoured: boolean;
};

/**
 * Return a safe next position, preferring the requested segment and then
 * progressively wider lateral detours. A climb is the final option for a
 * horizontal/ascending request; a commanded descent never turns into a climb.
 */
export function avoidanceMove(viewer: Cesium.Viewer, from: Cesium.Cartesian3, to: Cesium.Cartesian3): AvoidanceMove | undefined {
  if (!movementBlocked(viewer, from, to)) return { position: Cesium.Cartesian3.clone(to), detoured: false };
  const detour = detourAround(viewer, from, to);
  return detour ? { position: detour.position, detoured: true } : undefined;
}

// Deflections are tried in pairs, nearest first, so the drone gives up as
// little of its requested heading as it can. The widest pair lets it turn back
// along a wall it has already reached instead of reporting no route at all.
const DETOUR_DEGREES = [35, 60, 90, 130];

type Detour = { position: Cesium.Cartesian3; sign: number };

/**
 * Widen the search around a segment already known to be blocked. `prefer` is
 * the side an earlier detour succeeded on; trying that side first keeps the
 * drone committed to one way around an obstacle instead of alternating between
 * two equally valid ones and stalling in front of it.
 */
function detourAround(viewer: Cesium.Viewer, from: Cesium.Cartesian3, to: Cesium.Cartesian3, prefer = 0): Detour | undefined {
  const frame = Cesium.Transforms.eastNorthUpToFixedFrame(from);
  const inverse = Cesium.Matrix4.inverseTransformation(frame, new Cesium.Matrix4());
  const worldDelta = Cesium.Cartesian3.subtract(to, from, new Cesium.Cartesian3());
  const local = Cesium.Matrix4.multiplyByPointAsVector(inverse, worldDelta, new Cesium.Cartesian3());
  const horizontal = Math.hypot(local.x, local.y);
  if (horizontal < 0.00001) return undefined;

  const candidates: Detour[] = [];
  const near = prefer < 0 ? -1 : 1;
  for (const degrees of DETOUR_DEGREES) {
    for (const side of [near, -near]) {
      const angle = Cesium.Math.toRadians(degrees * side);
      candidates.push({
        position: new Cesium.Cartesian3(
          local.x * Math.cos(angle) - local.y * Math.sin(angle),
          local.x * Math.sin(angle) + local.y * Math.cos(angle),
          local.z,
        ),
        sign: side,
      });
    }
  }
  if (local.z >= -0.00001) {
    candidates.push({
      position: new Cesium.Cartesian3(local.x, local.y, Math.max(local.z, horizontal * 0.75, DRONE_CLEARANCE * 4)),
      sign: 0,
    });
  }

  for (const candidate of candidates) {
    const position = Cesium.Matrix4.multiplyByPoint(frame, candidate.position, new Cesium.Cartesian3());
    if (!movementBlocked(viewer, from, position)) return { position, sign: candidate.sign };
  }
  return undefined;
}

// A building query renders an offscreen pick pass and reads pixels back from
// the GPU, which stalls the frame it runs on. Probing one per animation frame
// per drone is what makes flight look jagged while a parked drone looks smooth.
const MIN_LOOKAHEAD_METERS = 4;
const MAX_LOOKAHEAD_METERS = 45;
// One building query costs tens of milliseconds: it renders an offscreen pick
// pass over the whole photorealistic tileset and reads the result back from the
// GPU. That is several whole frames, so every probe is a visible jolt, and the
// old quarter-corridor re-probed about four times a second in a straight climb
// — four jolts a second, which is the stutter you feel.
//
// The ray already sweeps the entire lookahead distance, so clearing a longer
// corridor costs nothing in accuracy. It only commits the drone further ahead
// of its last look at the map, and the corridor is still abandoned the moment
// the drone turns out of it. This trades a bounded amount of staleness for
// roughly a sixth of the probes.
const LOOKAHEAD_SECONDS = 2.5;
const CORRIDOR_MAX_SECONDS = 3;
// Roughly 1.8 degrees, so drift across a full corridor stays inside the ray sweep.
const SAME_HEADING_DOT = 0.9995;
// A search that found no route is repeated only after the drone has actually
// moved, turned, or waited this long. Without it a drone pinned against a wall
// spends nine building queries every single frame and drags the whole scene
// down exactly when the view is already struggling.
const FAILED_ROUTE_SECONDS = 1;
const FAILED_ROUTE_METERS = 0.05;

/**
 * Clears a straight corridor ahead of a drone once, then flies inside it for
 * free. A corridor is re-probed only when it is used up, when it ages out, or
 * when the requested heading leaves it, which cuts building queries from one
 * per frame to a handful per second without shortening the safety margin.
 */
export class MotionGuard {
  private requested?: Cesium.Cartesian3;
  private travel?: Cesium.Cartesian3;
  private remaining = 0;
  private age = 0;
  private detoured = false;
  private turnSign = 0;
  private failedFrom?: Cesium.Cartesian3;
  private failedHeading?: Cesium.Cartesian3;
  private failedAge = 0;

  /** Drop the corridor whenever the flight mode, route, or position changes. */
  clear(): void {
    this.dropCorridor();
    this.turnSign = 0;
    this.failedFrom = undefined;
    this.failedHeading = undefined;
    this.failedAge = 0;
  }

  /** Retire the cleared corridor but keep what was learned about the obstacle. */
  private dropCorridor(): void {
    this.requested = undefined;
    this.travel = undefined;
    this.remaining = 0;
    this.age = 0;
    this.detoured = false;
  }

  move(viewer: Cesium.Viewer, from: Cesium.Cartesian3, to: Cesium.Cartesian3, seconds: number): AvoidanceMove | undefined {
    const delta = Cesium.Cartesian3.subtract(to, from, new Cesium.Cartesian3());
    const step = Cesium.Cartesian3.magnitude(delta);
    if (step < 0.00001) return { position: Cesium.Cartesian3.clone(to), detoured: false };
    const heading = Cesium.Cartesian3.divideByScalar(delta, step, new Cesium.Cartesian3());
    const elapsed = Math.max(0, seconds);
    this.age += elapsed;

    if (this.travel && this.requested && this.age <= CORRIDOR_MAX_SECONDS) {
      const alignment = Cesium.Cartesian3.dot(this.requested, heading);
      // Drift off the cleared axis is still covered while it stays inside the
      // swept corridor, so a gradual turn shortens the corridor rather than
      // spending another building query on every frame of the turn.
      if (alignment < SAME_HEADING_DOT && alignment > 0) {
        const drift = Math.sqrt(Math.max(0, 1 - alignment * alignment));
        this.remaining = Math.min(this.remaining, DRONE_CLEARANCE / Math.max(drift, 0.000001));
      }
      if (alignment > 0 && this.remaining >= step) {
        this.remaining -= step;
        return { position: this.detoured ? advance(from, this.travel, step) : Cesium.Cartesian3.clone(to), detoured: this.detoured };
      }
    }

    this.dropCorridor();
    if (this.failedFrom && this.failedHeading) {
      this.failedAge += elapsed;
      if (this.failedAge <= FAILED_ROUTE_SECONDS
        && Cesium.Cartesian3.distance(from, this.failedFrom) <= FAILED_ROUTE_METERS
        && Cesium.Cartesian3.dot(this.failedHeading, heading) >= SAME_HEADING_DOT) return undefined;
      this.failedFrom = this.failedHeading = undefined;
      this.failedAge = 0;
    }

    const speed = seconds > 0 ? step / seconds : 0;
    const lookahead = Math.min(MAX_LOOKAHEAD_METERS, Math.max(step, MIN_LOOKAHEAD_METERS, speed * LOOKAHEAD_SECONDS));
    const probe = advance(from, heading, lookahead);
    if (!movementBlocked(viewer, from, probe)) {
      this.requested = this.travel = heading;
      this.remaining = lookahead - step;
      return { position: Cesium.Cartesian3.clone(to), detoured: false };
    }

    // Steer around the obstacle at probe range and hold that heading, rather
    // than picking a fresh deflection angle on every single frame.
    const detour = detourAround(viewer, from, probe, this.turnSign);
    if (!detour) {
      this.failedFrom = Cesium.Cartesian3.clone(from);
      this.failedHeading = Cesium.Cartesian3.clone(heading);
      this.failedAge = 0;
      return undefined;
    }
    if (detour.sign !== 0) this.turnSign = detour.sign;
    const reach = Cesium.Cartesian3.distance(from, detour.position);
    this.requested = heading;
    this.travel = Cesium.Cartesian3.normalize(Cesium.Cartesian3.subtract(detour.position, from, new Cesium.Cartesian3()), new Cesium.Cartesian3());
    this.remaining = Math.max(0, reach - step);
    this.detoured = true;
    return { position: advance(from, this.travel, Math.min(step, reach)), detoured: true };
  }
}

function advance(from: Cesium.Cartesian3, direction: Cesium.Cartesian3, distance: number): Cesium.Cartesian3 {
  return Cesium.Cartesian3.add(from, Cesium.Cartesian3.multiplyByScalar(direction, distance, new Cesium.Cartesian3()), new Cesium.Cartesian3());
}

export function movementBlocked(viewer: Cesium.Viewer, from: Cesium.Cartesian3, to: Cesium.Cartesian3): boolean {
  const scene = viewer.scene as RayScene | undefined;
  if (!scene) return false; // Controller-only simulations have no map geometry.
  const distance = Cesium.Cartesian3.distance(from, to);
  if (distance < 0.00001) return false;
  queryWarnings.delete(viewer);
  const ray = new Cesium.Ray(from, Cesium.Cartesian3.normalize(Cesium.Cartesian3.subtract(to, from, new Cesium.Cartesian3()), new Cesium.Cartesian3()));
  // Exclude every application entity: trails and map labels are not buildings.
  // Cesium's ray width sweeps a square footprint around the center ray.
  const originalView = scene.view;
  if (scene.pickFromRay) {
    try {
      const hit = scene.pickFromRay(ray, viewer.entities.values, DRONE_CLEARANCE * 2)?.position;
      if (hit && Cesium.Cartesian3.distance(from, hit) <= distance + DRONE_CLEARANCE) return true;
    } catch (error) {
      queryWarnings.set(viewer, `Building collision query unavailable: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      // Cesium switches to an offscreen view internally, but does not restore it if it throws.
      if (originalView !== undefined) scene.view = originalView;
    }
  } else {
    queryWarnings.set(viewer, "Building collision queries are unavailable in this browser.");
  }
  // Google tiles can have valid negative ellipsoid heights. The hidden fallback
  // globe is not the ground in that mode; its sea-level surface would trap drones.
  if (scene.globe?.show === false) return false;
  try {
    const terrainHit = scene.globe?.pick(ray, scene);
    if (terrainHit && Cesium.Cartesian3.distance(from, terrainHit) <= distance + DRONE_CLEARANCE) return true;
  } catch {
    queryWarnings.set(viewer, "Terrain ray query unavailable; using ground height checks.");
  }
    // Sample the full segment as well: endpoint-only checks can tunnel through slopes.
    const count = Math.ceil(distance / 5);
    if (count > 2000) return true; // Reject extreme typed speeds without unbounded frame work.
    const origin = Cesium.Cartographic.fromCartesian(from);
    let previousClearance: number | undefined;
    try { previousClearance = origin.height - (scene.globe?.getHeight(origin) ?? 0); } catch { /* Use endpoint checks below. */ }
    for (let i = 1; i <= count; i++) {
      const point = Cesium.Cartographic.fromCartesian(Cesium.Cartesian3.lerp(from, to, i / count, new Cesium.Cartesian3()));
      let ground = 0;
      try { ground = scene.globe?.getHeight(point) ?? 0; } catch {
        queryWarnings.set(viewer, "Terrain heights unavailable; using the fallback ellipsoid ground.");
      }
      const clearance = point.height - ground;
      // A drone placed below the margin must be able to climb back out.
      if (clearance < DRONE_CLEARANCE && !(previousClearance !== undefined && clearance > previousClearance + 0.00001)) return true;
      previousClearance = clearance;
    }
    return false;
}
