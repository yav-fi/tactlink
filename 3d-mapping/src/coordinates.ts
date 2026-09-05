import * as Cesium from "cesium";

export type LocalVector = { x: number; y: number; z: number };

/** Convert backend x=east, y=north, z=up metres from one geodetic origin. */
export function localToFixed(origin: { latitude: number; longitude: number; altitude: number }, point: LocalVector): Cesium.Cartesian3 {
  const anchor = Cesium.Cartesian3.fromDegrees(origin.longitude, origin.latitude, origin.altitude);
  const frame = Cesium.Transforms.eastNorthUpToFixedFrame(anchor);
  return Cesium.Matrix4.multiplyByPoint(frame, new Cesium.Cartesian3(point.x, point.y, point.z), new Cesium.Cartesian3());
}
