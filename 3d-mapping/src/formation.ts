import * as Cesium from "cesium";
import type { Coordinates } from "./mission";

export function formationSlots(center: Coordinates, count: number, spacing: number): Coordinates[] {
  if (!Number.isInteger(count) || count < 1 || count > 100) throw new Error("Choose between 1 and 100 drones.");
  if (!Number.isFinite(spacing) || spacing < 20) throw new Error("Use at least 20 meters of spacing.");
  const columns = Math.ceil(Math.sqrt(count));
  const rows = Math.ceil(count / columns);
  const frame = Cesium.Transforms.eastNorthUpToFixedFrame(Cesium.Cartesian3.fromDegrees(center.longitude, center.latitude, center.altitude));
  return Array.from({ length: count }, (_, i) => {
    const offset = new Cesium.Cartesian3((i % columns - (columns - 1) / 2) * spacing, (Math.floor(i / columns) - (rows - 1) / 2) * spacing, 0);
    const geographic = Cesium.Cartographic.fromCartesian(Cesium.Matrix4.multiplyByPoint(frame, offset, new Cesium.Cartesian3()));
    return { latitude: Cesium.Math.toDegrees(geographic.latitude), longitude: Cesium.Math.toDegrees(geographic.longitude), altitude: center.altitude };
  });
}
