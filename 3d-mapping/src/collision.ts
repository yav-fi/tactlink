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

  const frame = Cesium.Transforms.eastNorthUpToFixedFrame(from);
  const inverse = Cesium.Matrix4.inverseTransformation(frame, new Cesium.Matrix4());
  const worldDelta = Cesium.Cartesian3.subtract(to, from, new Cesium.Cartesian3());
  const local = Cesium.Matrix4.multiplyByPointAsVector(inverse, worldDelta, new Cesium.Cartesian3());
  const horizontal = Math.hypot(local.x, local.y);
  if (horizontal < 0.00001) return undefined;

  const candidates: Cesium.Cartesian3[] = [];
  for (const degrees of [35, -35, 60, -60, 90, -90]) {
    const angle = Cesium.Math.toRadians(degrees);
    candidates.push(new Cesium.Cartesian3(
      local.x * Math.cos(angle) - local.y * Math.sin(angle),
      local.x * Math.sin(angle) + local.y * Math.cos(angle),
      local.z,
    ));
  }
  if (local.z >= -0.00001) {
    candidates.push(new Cesium.Cartesian3(local.x, local.y, Math.max(local.z, horizontal * 0.75, DRONE_CLEARANCE * 4)));
  }

  for (const candidate of candidates) {
    const position = Cesium.Matrix4.multiplyByPoint(frame, candidate, new Cesium.Cartesian3());
    if (!movementBlocked(viewer, from, position)) return { position, detoured: true };
  }
  return undefined;
}

export function movementBlocked(viewer: Cesium.Viewer, from: Cesium.Cartesian3, to: Cesium.Cartesian3): boolean {
  const scene = viewer.scene as RayScene | undefined;
  if (!scene) return false; // Controller-only simulations have no map geometry.
  const distance = Cesium.Cartesian3.distance(from, to);
  if (distance < 0.00001) return false;
  queryWarnings.delete(viewer);
  const ray = new Cesium.Ray(from, Cesium.Cartesian3.normalize(Cesium.Cartesian3.subtract(to, from, new Cesium.Cartesian3()), new Cesium.Cartesian3()));
  // Exclude every application entity: trails, labels and release prisms are not buildings.
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
