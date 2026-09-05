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
  private replayStart = 0;
  private replayFlights: { drone: DroneController; duration: number }[] = [];
  replay = { running: false, elapsed: 0, duration: 0, total: 0, arrived: 0 };
  constructor(private readonly viewer: Cesium.Viewer) {}
  get nextColor() { return DRONE_COLORS[(this.nextNumber - 1) % DRONE_COLORS.length]; }
  deploy(position: Coordinates): DroneController {
    const drone = new DroneController(this.viewer, position, Cesium.Color.fromCssColorString(this.nextColor.hex), `drone_${this.nextNumber++}`);
    this.drones.set(drone.id, drone);
    return drone;
  }
  clear(): void {
    this.stopReplay();
    for (const drone of this.drones.values()) drone.destroy();
    this.drones.clear();
    this.nextNumber = 1;
  }

  startReplay(nowSeconds: number): void {
    this.stopReplay();
    this.replayFlights = [];
    for (const drone of this.drones.values()) {
      const duration = drone.beginReplay();
      if (duration !== null) this.replayFlights.push({ drone, duration });
    }
    const duration = Math.max(0, ...this.replayFlights.map(flight => flight.duration));
    this.replayStart = nowSeconds;
    this.replay = { running: duration > 0, elapsed: 0, duration, total: this.replayFlights.length, arrived: 0 };
  }

  updateReplay(nowSeconds: number): void {
    if (!this.replay.running) return;
    this.replay.elapsed = Math.min(this.replay.duration, Math.max(0, nowSeconds - this.replayStart));
    this.replay.arrived = 0;
    for (const flight of this.replayFlights) {
      flight.drone.replayAt(this.replay.elapsed);
      if (this.replay.elapsed >= flight.duration) this.replay.arrived++;
    }
    this.replay.running = this.replay.elapsed < this.replay.duration;
  }

  stopReplay(): void {
    for (const drone of this.drones.values()) drone.hideReplay();
    this.replayFlights = [];
    this.replay = { running: false, elapsed: 0, duration: 0, total: 0, arrived: 0 };
  }
}
