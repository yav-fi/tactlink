import * as Cesium from "cesium";
import { DroneController } from "./drone-controller";
import type { Coordinates } from "./mission";

export const DRONE_COLORS = [
  { name: "Cyan", hex: "#35e8ff" },
  { name: "Orange", hex: "#ff9b42" },
  { name: "Violet", hex: "#b69cff" },
  { name: "Green", hex: "#69ed92" },
  { name: "Pink", hex: "#ff79bb" },
  { name: "Yellow", hex: "#ffe568" },
  { name: "Blue", hex: "#669dff" },
  { name: "Red", hex: "#ff6666" },
] as const;

export class Fleet {
  readonly drones = new Map<string, DroneController>();
  private nextNumber = 1;
  constructor(private readonly viewer: Cesium.Viewer) {}
  get nextColor() { return DRONE_COLORS[(this.nextNumber - 1) % DRONE_COLORS.length]; }
  deploy(position: Coordinates): DroneController {
    const drone = new DroneController(this.viewer, position, Cesium.Color.fromCssColorString(this.nextColor.hex), `drone_${this.nextNumber++}`);
    this.drones.set(drone.id, drone);
    return drone;
  }
  clear(): void {
    for (const drone of this.drones.values()) drone.destroy();
    this.drones.clear();
    this.nextNumber = 1;
  }
}
