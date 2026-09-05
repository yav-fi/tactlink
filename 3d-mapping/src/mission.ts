export type Coordinates = {
  latitude: number;
  longitude: number;
  altitude: number;
};

export type MissionStep =
  | ({ action: "goto"; speed_mps?: number } & Coordinates)
  | { action: "hover"; duration_s: number }
  | { action: "orbit"; radius_m: number; duration_s: number; clockwise?: boolean }
  | { action: "return_home"; speed_mps?: number };

export type MissionCommand = {
  drone_id: string;
  mission: MissionStep[];
};

export const sampleMission: MissionCommand = {
  drone_id: "drone_1",
  mission: [
    { action: "goto", latitude: 38.8895, longitude: -77.0353, altitude: 120, speed_mps: 35 },
    { action: "orbit", radius_m: 70, duration_s: 18 },
    { action: "return_home", speed_mps: 35 },
  ],
};

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function assertCoordinates(value: Record<string, unknown>): void {
  if (!isFiniteNumber(value.latitude) || !isFiniteNumber(value.longitude) || !isFiniteNumber(value.altitude)) {
    throw new Error("goto requires numeric latitude, longitude, and altitude.");
  }
  if (value.latitude < -90 || value.latitude > 90 || value.longitude < -180 || value.longitude > 180 || value.altitude < 0) {
    throw new Error("Coordinates are outside supported latitude, longitude, or altitude ranges.");
  }
}

export function parseMission(value: string): MissionCommand {
  const parsed: unknown = JSON.parse(value);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("Mission must be a JSON object.");
  const mission = parsed as Record<string, unknown>;
  if (typeof mission.drone_id !== "string" || !mission.drone_id.trim()) throw new Error("drone_id is required.");
  if (!Array.isArray(mission.mission) || mission.mission.length === 0) throw new Error("mission must contain at least one step.");

  mission.mission.forEach((rawStep) => {
    if (!rawStep || typeof rawStep !== "object" || Array.isArray(rawStep)) throw new Error("Each mission step must be an object.");
    const step = rawStep as Record<string, unknown>;
    if (step.action === "goto") assertCoordinates(step);
    else if (step.action === "hover" && (!isFiniteNumber(step.duration_s) || step.duration_s <= 0)) throw new Error("hover requires a positive duration_s.");
    else if (step.action === "orbit" && (!isFiniteNumber(step.radius_m) || step.radius_m <= 0 || !isFiniteNumber(step.duration_s) || step.duration_s <= 0)) throw new Error("orbit requires positive radius_m and duration_s.");
    else if (step.action !== "return_home") throw new Error(`Unsupported action: ${String(step.action)}.`);
  });
  return mission as MissionCommand;
}
