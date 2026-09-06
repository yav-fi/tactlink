import type { DroneController } from "./drone-controller";
import type { Coordinates } from "./mission";
import type { RuntimeOperator, LocalVector } from "./runtime/types";

export function applyPhoneAction(target: DroneController, action: string, owner: RuntimeOperator, home: Coordinates, actual: LocalVector): void {
  if (action === "halt") target.stopCommand();
  else if (action === "takeoff") { if (actual.z < 10) target.startGestureMotion(0, 0, 1, 2); else target.stopCommand(); }
  else if (action === "land") { if (actual.z > 0.25) target.startGestureMotion(0, 0, -1, 1); else target.stopCommand(); }
  else if (["forward", "left", "right"].includes(action)) {
    const angle = owner.heading + (action === "left" ? Math.PI / 2 : action === "right" ? -Math.PI / 2 : 0);
    target.startGestureMotion(Math.cos(angle), Math.sin(angle), 0, 2);
  } else if (action === "orbit") {
    const dx = actual.x - owner.position.x, dy = actual.y - owner.position.y;
    const distance = Math.hypot(dx, dy), radial = (5 - distance) * 0.5;
    const angle = distance > 0.01 ? Math.atan2(dy, dx) : 0;
    target.startGestureMotion(radial * Math.cos(angle) - Math.sin(angle), radial * Math.sin(angle) + Math.cos(angle), 0, 2);
  } else if (action === "rotate_heading") target.rotateHeading(90);
  else if (action === "return_home") target.run({ drone_id: target.id, mission: [
    { action: "goto", ...home, altitude: home.altitude + Math.max(1, actual.z), speed_mps: 2 },
  ] });
}
