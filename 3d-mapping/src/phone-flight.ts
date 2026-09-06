import type { DroneController } from "./drone-controller";
import type { Coordinates } from "./mission";
import type { RuntimeOperator, LocalVector } from "./runtime/types";

export type PhoneFlightBand = { floor: number; ceiling: number };
export function phoneFlightBand(people: Pick<RuntimeOperator, "position">[]): PhoneFlightBand {
  const highest = Math.max(0, ...people.map(p => p.position.z).filter(Number.isFinite));
  return { floor: highest + 2.5, ceiling: highest + 8 };
}
export function constrainPhoneHeight(target: DroneController, home: Coordinates, band: PhoneFlightBand): void {
  const current = target.snapshot();
  const altitude = Math.max(home.altitude + band.floor, Math.min(home.altitude + band.ceiling, current.altitude));
  if (altitude !== current.altitude) {
    target.stopCommand();
    target.applyManualMove({ ...current, altitude });
  }
}

export function applyPhoneAction(target: DroneController, action: string, owner: RuntimeOperator, home: Coordinates, actual: LocalVector, band: PhoneFlightBand = phoneFlightBand([])): void {
  if (action === "halt") target.stopCommand();
  else if (action === "follow" || action === "hover_overhead") {
    const height = Math.max(band.floor, Math.min(band.ceiling, owner.position.z + 3.5));
    const east = owner.position.x - actual.x, north = owner.position.y - actual.y, up = height - actual.z;
    const distance = Math.hypot(east, north, up);
    if (distance < 0.15) target.stopCommand();
    else target.startGestureMotion(east, north, up, Math.min(2, distance * 0.8));
  }
  else if (action === "takeoff") { if (actual.z < band.ceiling) target.startGestureMotion(0, 0, 1, 2); else target.stopCommand(); }
  else if (action === "land") { if (actual.z > band.floor) target.startGestureMotion(0, 0, -1, 1); else target.stopCommand(); }
  else if (["forward", "left", "right"].includes(action)) {
    const angle = owner.heading + (action === "left" ? Math.PI / 2 : action === "right" ? -Math.PI / 2 : 0);
    target.startGestureMotion(Math.cos(angle), Math.sin(angle), 0, 2);
  } else if (action === "orbit") {
    const dx = actual.x - owner.position.x, dy = actual.y - owner.position.y;
    const distance = Math.hypot(dx, dy), radial = (5 - distance) * 2;
    const angle = distance > 0.01 ? Math.atan2(dy, dx) : 0;
    const height = Math.max(band.floor, Math.min(band.ceiling, owner.position.z + 3.5));
    const east = radial * Math.cos(angle) - 3 * Math.sin(angle), north = radial * Math.sin(angle) + 3 * Math.cos(angle);
    const up = (height - actual.z) * 0.8;
    target.startGestureMotion(east, north, up, Math.min(3.5, Math.hypot(east, north, up)), true);
  } else if (action === "return_home") target.run({ drone_id: target.id, mission: [
    { action: "goto", ...home, altitude: home.altitude + Math.max(band.floor, Math.min(band.ceiling, actual.z)), speed_mps: 2 },
  ] });
}
