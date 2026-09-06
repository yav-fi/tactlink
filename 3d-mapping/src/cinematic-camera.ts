import * as Cesium from "cesium";

export type FleetCameraFrame = { center: Cesium.Cartesian3; range: number };

/** Frames every supplied subject with enough margin for labels and movement. */
export function fleetCameraFrame(positions: Cesium.Cartesian3[]): FleetCameraFrame | undefined {
  if (!positions.length) return undefined;
  const sphere = Cesium.BoundingSphere.fromPoints(positions);
  return {
    center: sphere.center,
    range: Math.max(32, sphere.radius * 2.6 + 25),
  };
}

/** Converts WASD-style screen input into local east/north movement. */
export function screenRelativeMovement(
  position: Cesium.Cartesian3,
  cameraDirection: Cesium.Cartesian3,
  cameraRight: Cesium.Cartesian3,
  forward: number,
  right: number,
): { east: number; north: number } {
  const inverse = Cesium.Matrix4.inverseTransformation(Cesium.Transforms.eastNorthUpToFixedFrame(position), new Cesium.Matrix4());
  const view = Cesium.Matrix4.multiplyByPointAsVector(inverse, cameraDirection, new Cesium.Cartesian3());
  const side = Cesium.Matrix4.multiplyByPointAsVector(inverse, cameraRight, new Cesium.Cartesian3());
  const viewLength = Math.hypot(view.x, view.y) || 1;
  const sideLength = Math.hypot(side.x, side.y) || 1;
  return {
    east: forward * view.x / viewLength + right * side.x / sideLength,
    north: forward * view.y / viewLength + right * side.y / sideLength,
  };
}

export function blendHeading(current: number, target: number, amount: number): number {
  const delta = Math.atan2(Math.sin(target - current), Math.cos(target - current));
  return current + delta * Math.max(0, Math.min(1, amount));
}
