import * as Cesium from "cesium";
import type { CommandIntent } from "./command-console";
import { previewCoordinate } from "./flight-preview";
import type { Coordinates, MissionStep } from "./mission";

export const isFlightSequenceIntent = (intent: CommandIntent): boolean =>
  ["goto", "landmark", "move", "turn", "hover", "orbit", "return"].includes(intent.type);

function headingBetween(from: Coordinates, to: Coordinates, fallback: number): number {
  const start = Cesium.Cartesian3.fromDegrees(from.longitude, from.latitude, from.altitude);
  const end = Cesium.Cartesian3.fromDegrees(to.longitude, to.latitude, to.altitude);
  const local = Cesium.Matrix4.multiplyByPointAsVector(
    Cesium.Matrix4.inverseTransformation(Cesium.Transforms.eastNorthUpToFixedFrame(start), new Cesium.Matrix4()),
    Cesium.Cartesian3.subtract(end, start, new Cesium.Cartesian3()),
    new Cesium.Cartesian3(),
  );
  return Math.hypot(local.x, local.y) > 0.01 ? Math.atan2(local.x, local.y) : fallback;
}

export function compileMissionSequence(
  intents: CommandIntent[],
  start: Coordinates,
  home: Coordinates,
  initialHeading: number,
  speedMps: number,
): MissionStep[] {
  if (!intents.length || !intents.every(isFlightSequenceIntent)) throw new Error("Only flight actions can be chained into one mission.");
  let cursor = { ...start };
  let heading = initialHeading;
  const mission: MissionStep[] = [];

  for (const intent of intents) {
    if (intent.type === "turn") {
      heading += Cesium.Math.toRadians(intent.degrees) * (intent.direction === "right" ? 1 : -1);
      continue;
    }
    if (intent.type === "move") {
      const forward = intent.direction === "forward" ? intent.meters : intent.direction === "back" ? -intent.meters : 0;
      const right = intent.direction === "right" ? intent.meters : intent.direction === "left" ? -intent.meters : 0;
      cursor = previewCoordinate({
        x: Math.sin(heading) * forward + Math.cos(heading) * right,
        y: Math.cos(heading) * forward - Math.sin(heading) * right,
        z: 0,
      }, cursor);
      mission.push({ action: "goto", ...cursor, speed_mps: speedMps });
      continue;
    }
    if (intent.type === "goto") {
      if (intent.all) throw new Error("A chained mission controls the selected drone; omit “all”.");
      const destination = { latitude: intent.latitude, longitude: intent.longitude, altitude: intent.altitude ?? cursor.altitude };
      heading = headingBetween(cursor, destination, heading); cursor = destination;
      mission.push({ action: "goto", ...cursor, speed_mps: speedMps });
      continue;
    }
    if (intent.type === "landmark") {
      const destination = { latitude: intent.latitude, longitude: intent.longitude, altitude: cursor.altitude };
      heading = headingBetween(cursor, destination, heading); cursor = destination;
      mission.push({ action: "goto", ...cursor, speed_mps: speedMps });
      continue;
    }
    if (intent.type === "hover") {
      mission.push({ action: "hover", duration_s: intent.seconds });
      continue;
    }
    if (intent.type === "orbit") {
      mission.push({ action: "orbit", radius_m: intent.radius, duration_s: intent.seconds });
      continue;
    }
    if (intent.type === "return") {
      if (intent.all) throw new Error("A chained mission controls the selected drone; omit “all”.");
      heading = headingBetween(cursor, home, heading); cursor = { ...home };
      mission.push({ action: "return_home", speed_mps: speedMps });
    }
  }
  if (!mission.length) throw new Error("Add movement or a wait after the turn command.");
  return mission;
}
