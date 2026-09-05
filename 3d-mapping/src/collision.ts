import * as Cesium from "cesium";

// Enclose the 16 x 10 x 4 m display prism, independent of heading.
export const DRONE_CLEARANCE = 10;
type RayScene = Cesium.Scene & {
  pickFromRay(ray: Cesium.Ray, exclude: Cesium.Entity[], width: number): { position?: Cesium.Cartesian3 } | undefined;
};

export function movementBlocked(viewer: Cesium.Viewer, from: Cesium.Cartesian3, to: Cesium.Cartesian3): boolean {
  const scene = viewer.scene as RayScene | undefined;
  if (!scene) return false; // Controller-only simulations have no map geometry.
  const distance = Cesium.Cartesian3.distance(from, to);
  if (distance < 0.00001) return false;
  const ray = new Cesium.Ray(from, Cesium.Cartesian3.normalize(Cesium.Cartesian3.subtract(to, from, new Cesium.Cartesian3()), new Cesium.Cartesian3()));
  // Exclude every application entity: trails, labels and release prisms are not buildings.
  // Cesium's ray width sweeps a square footprint around the center ray.
  try {
    if (scene.pickFromRay) {
      const hit = scene.pickFromRay(ray, viewer.entities.values, DRONE_CLEARANCE * 2)?.position;
      if (hit && Cesium.Cartesian3.distance(from, hit) <= distance + DRONE_CLEARANCE) return true;
    }
    const terrainHit = scene.globe?.pick(ray, scene);
    if (terrainHit && Cesium.Cartesian3.distance(from, terrainHit) <= distance + DRONE_CLEARANCE) return true;
    // Sample the full segment as well: endpoint-only checks can tunnel through slopes.
    const count = Math.ceil(distance / 5);
    if (count > 2000) return true; // Reject extreme typed speeds without unbounded frame work.
    for (let i = 1; i <= count; i++) {
      const point = Cesium.Cartographic.fromCartesian(Cesium.Cartesian3.lerp(from, to, i / count, new Cesium.Cartesian3()));
      const ground = scene.globe?.getHeight(point) ?? 0;
      if (point.height < ground + DRONE_CLEARANCE) return true;
    }
    return false;
  } catch {
    // If a geometry query fails, keep the last safe position rather than fly unchecked.
    return true;
  }
}
