import * as Cesium from "cesium";
import type { Coordinates, MissionCommand, MissionStep } from "./mission";

export type ENUPoint = { x: number; y: number; z: number };
export type FlightPreview = {
  schema_version: "1.0";
  type: "flight_preview";
  frame: "ENU";
  mission_id: string;
  segments: { type: "line" | "vertical" | "arc" | "hold"; points: ENUPoint[]; duration_s?: number }[];
};

// The simulator's deployment altitude is an ellipsoid height. It is the local
// preview zero; do not substitute ArduPilot MSL home altitude here.
export function previewCoordinate(point: ENUPoint, home: Coordinates): Coordinates {
  const center = Cesium.Cartesian3.fromDegrees(home.longitude, home.latitude, home.altitude);
  const cartesian = Cesium.Matrix4.multiplyByPoint(Cesium.Transforms.eastNorthUpToFixedFrame(center),
    new Cesium.Cartesian3(point.x, point.y, 0), new Cesium.Cartesian3());
  const geo = Cesium.Cartographic.fromCartesian(cartesian);
  return { latitude: Cesium.Math.toDegrees(geo.latitude), longitude: Cesium.Math.toDegrees(geo.longitude),
    altitude: home.altitude + point.z };
}

export function previewMission(preview: FlightPreview, droneId: string, home: Coordinates, speed: number): MissionCommand {
  if (preview.schema_version !== "1.0" || preview.frame !== "ENU" || !preview.segments?.length) {
    throw new Error("Unsupported flight preview format.");
  }
  if (!Number.isFinite(speed) || speed <= 0) throw new Error("Preview speed must be positive.");
  const mission: MissionStep[] = [];
  for (const segment of preview.segments) {
    for (const point of segment.points) {
      if (![point.x, point.y, point.z].every(Number.isFinite)) throw new Error("Invalid preview point.");
    }
    if (!segment.points.length) throw new Error("Empty preview segment.");
    if (segment.type === "hold") {
      if (!Number.isFinite(segment.duration_s) || segment.duration_s! <= 0) throw new Error("Invalid hold duration.");
      mission.push({ action: "hover", duration_s: segment.duration_s! });
      continue;
    }
    // Include the first start point so a previously moved simulated drone first
    // returns to the preview origin. All subsequent segments share endpoints.
    const points = mission.length ? segment.points.slice(1) : segment.points;
    for (const point of points) mission.push({ action: "goto", ...previewCoordinate(point, home), speed_mps: speed });
  }
  return { drone_id: droneId, mission };
}
