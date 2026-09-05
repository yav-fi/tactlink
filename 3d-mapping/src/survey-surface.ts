import * as Cesium from "cesium";

export const SURVEY_MAX_RANGE = 10_000;
export const SURVEY_RAYS = 12;

export function surfaceHit(viewer: Cesium.Viewer, ray: Cesium.Ray): Cesium.Cartesian3 | undefined {
  const scene = viewer.scene as (Cesium.Scene & { view?: unknown; pickFromRay?: (ray: Cesium.Ray, exclude: Cesium.Entity[], width: number) => { position?: Cesium.Cartesian3 } | undefined }) | undefined;
  if (!scene) return;
  const previousView = scene.view;
  const hits: Cesium.Cartesian3[] = [];
  try {
    const hit = scene.pickFromRay?.(ray, viewer.entities.values, 0.1)?.position;
    if (hit) hits.push(hit);
  } catch { /* Unknown geometry must not be recorded as covered. */ }
  finally { if (previousView !== undefined) scene.view = previousView; }
  if (scene.globe?.show !== false) {
    try { const hit = scene.globe?.pick(ray, scene); if (hit) hits.push(hit); } catch { /* No known surface. */ }
  }
  return hits.filter(hit => Cesium.Cartesian3.distance(ray.origin, hit) <= SURVEY_MAX_RANGE)
    .sort((a, b) => Cesium.Cartesian3.distance(ray.origin, a) - Cesium.Cartesian3.distance(ray.origin, b))[0];
}

export function sampleSurvey(origin: Cesium.Cartesian3, direction: Cesium.Cartesian3, cast: (ray: Cesium.Ray) => Cesium.Cartesian3 | undefined) {
  const right = Cesium.Cartesian3.normalize(Cesium.Cartesian3.cross(direction, Cesium.Cartesian3.mostOrthogonalAxis(direction, new Cesium.Cartesian3()), new Cesium.Cartesian3()), new Cesium.Cartesian3());
  const up = Cesium.Cartesian3.cross(direction, right, new Cesium.Cartesian3());
  const centerHit = cast(new Cesium.Ray(origin, direction));
  const hits = Array.from({ length: SURVEY_RAYS }, (_, i) => {
    const angle = i / SURVEY_RAYS * Math.PI * 2;
    const offset = Cesium.Cartesian3.add(Cesium.Cartesian3.multiplyByScalar(right, 0.375 * Math.cos(angle), new Cesium.Cartesian3()), Cesium.Cartesian3.multiplyByScalar(up, 0.375 * Math.sin(angle), new Cesium.Cartesian3()), new Cesium.Cartesian3());
    const ray = new Cesium.Ray(origin, Cesium.Cartesian3.normalize(Cesium.Cartesian3.add(direction, offset, new Cesium.Cartesian3()), new Cesium.Cartesian3()));
    const hit = cast(ray);
    return { hit, end: hit ?? Cesium.Ray.getPoint(ray, SURVEY_MAX_RANGE) };
  });
  return { centerHit, hits };
}
