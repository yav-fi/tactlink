import * as Cesium from "cesium";

export type FleetCameraFrame = { center: Cesium.Cartesian3; range: number };

/** Frames every supplied subject with enough margin for labels and movement. */
export function fleetCameraFrame(positions: Cesium.Cartesian3[]): FleetCameraFrame | undefined {
  if (!positions.length) return undefined;
  const sphere = Cesium.BoundingSphere.fromPoints(positions);
  return {
    center: sphere.center,
    range: Math.max(85, sphere.radius * 3.4 + 45),
  };
}
