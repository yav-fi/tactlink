import * as Cesium from "cesium";
import { DroneController } from "./drone-controller";
import type { Coordinates } from "./mission";
import { formationSlots } from "./formation";

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
  private groupFlight = false;
  readonly groups = new Map<string, { ids: string[]; color: string }>();
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
    this.groups.clear();
  }

  deployBulk(center: Coordinates, count: number, spacing: number): DroneController[] {
    return formationSlots(center, count, spacing).map(position => this.deploy(position));
  }

  saveGroup(name: string, ids: string[]): void {
    name = name.trim();
    const members = [...new Set(ids)].filter(id => this.drones.has(id));
    if (!name || !members.length) throw new Error("Select drones and enter a group name.");
    const color = this.groups.get(name)?.color ?? DRONE_COLORS[this.groups.size % DRONE_COLORS.length].hex;
    this.groups.set(name, { ids: members, color });
    for (const id of members) this.drones.get(id)!.setColor(color);
  }

  commandGroup(ids: string[], center: Coordinates, spacing: number, nowSeconds: number, groupName?: string): void {
    const members = [...new Set(ids)].map(id => {
      const item = this.drones.get(id);
      if (!item) throw new Error(`Unknown drone: ${id}`);
      return item;
    });
    const slots = formationSlots(center, members.length, spacing);
    const color = (groupName ? this.groups.get(groupName)?.color : undefined) ?? members[0].colorHex;
    this.stopReplay();
    this.groupFlight = true;
    this.replayFlights = members.map((item, index) => {
      item.setColor(color);
      item.commandDestination(slots[index]);
      const duration = item.beginReplay() ?? 0;
      if (duration === 0) item.stopCommand();
      return { drone: item, duration };
    });
    const duration = Math.max(...this.replayFlights.map(item => item.duration));
    this.replayStart = nowSeconds;
    this.replay = { running: duration > 0, elapsed: 0, duration, total: members.length, arrived: this.replayFlights.filter(item => item.duration === 0).length };
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
      flight.drone.replayAt(this.replay.elapsed, this.groupFlight);
      if (this.replay.elapsed >= flight.duration) this.replay.arrived++;
    }
    this.replay.running = this.replay.elapsed < this.replay.duration;
  }

  stopReplay(): void {
    if (this.groupFlight) for (const flight of this.replayFlights) flight.drone.stopCommand();
    this.groupFlight = false;
    for (const drone of this.drones.values()) drone.hideReplay();
    this.replayFlights = [];
    this.replay = { running: false, elapsed: 0, duration: 0, total: 0, arrived: 0 };
  }
}
