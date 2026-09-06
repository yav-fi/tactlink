import * as Cesium from "cesium";
import { DroneController } from "./drone-controller";
import type { DroneType } from "./drone-controller";
import type { Coordinates } from "./mission";
import { formationSlots } from "./formation";

export const DRONE_COLORS = [
  { name: "Red", hex: "#ff263f" },
  { name: "Cyan", hex: "#35e8ff" },
  { name: "Orange", hex: "#ff9b42" },
  { name: "Violet", hex: "#b69cff" },
  { name: "Green", hex: "#69ed92" },
  { name: "Pink", hex: "#ff79bb" },
  { name: "Yellow", hex: "#ffe568" },
  { name: "Blue", hex: "#669dff" },
] as const;

export class Fleet {
  readonly drones = new Map<string, DroneController>();
  private nextNumber = 1;
  private replayStart = 0;
  paused = false;
  private history: { label: string; drones: ReturnType<DroneController["capture"]>[]; groups: [string, { ids: string[]; color: string }][]; markers: { entity: Cesium.Entity; ids: string[] }[]; nextNumber: number; markerNumber: number }[] = [];
  get undoLabel(): string | undefined { return this.history.at(-1)?.label; }
  checkpoint(label: string): void {
    this.history.push({ label, drones: [...this.drones.values()].map(d => d.capture()), groups: [...this.groups].map(([name, g]) => [name, { ...g, ids: [...g.ids] }]), markers: [...this.batchMarkers].map(([id, ids]) => ({ entity: this.viewer.entities.getById(id)!, ids: [...ids] })), nextNumber: this.nextNumber, markerNumber: this.batchMarkerNumber });
    if (this.history.length > 20) this.history.shift();
  }
  undo(): string | undefined {
    const entry = this.history.pop(); if (!entry) return;
    this.clear();
    for (const data of entry.drones) {
      const drone = new DroneController(this.viewer, data.home, Cesium.Color.fromCssColorString(data.color), data.id, data.type);
      drone.restore(data); this.drones.set(data.id, drone);
    }
    for (const [name, group] of entry.groups) this.groups.set(name, group);
    for (const marker of entry.markers) { this.viewer.entities.add(marker.entity); this.batchMarkers.set(marker.entity.id, marker.ids); }
    this.nextNumber = entry.nextNumber; this.batchMarkerNumber = entry.markerNumber;
    return entry.label;
  }
  togglePause(now: number): void {
    if (!this.replay.running) return;
    if (this.paused) { this.replayStart = now - this.replay.elapsed; this.paused = false; }
    else { this.updateReplay(now); this.paused = this.replay.running; }
  }
  private groupFlight = false;
  readonly groups = new Map<string, { ids: string[]; color: string }>();
  manualBatch: DroneController[] = [];
  readonly batchMarkers = new Map<string, string[]>();
  private batchMarkerNumber = 0;
  private replayFlights: { drone: DroneController; duration: number }[] = [];
  replay = { running: false, elapsed: 0, duration: 0, total: 0, arrived: 0 };
  constructor(private readonly viewer: Cesium.Viewer) {}
  get nextColor() { return DRONE_COLORS[(this.nextNumber - 1) % DRONE_COLORS.length]; }
  deploy(position: Coordinates, type: DroneType = "normal"): DroneController {
    const drone = new DroneController(this.viewer, position, Cesium.Color.fromCssColorString(this.nextColor.hex), `drone_${this.nextNumber++}`, type);
    this.drones.set(drone.id, drone);
    return drone;
  }
  clear(): void {
    this.releaseBatch();
    for (const id of this.batchMarkers.keys()) this.viewer.entities.removeById(id);
    this.batchMarkers.clear();
    this.batchMarkerNumber = 0;
    this.stopReplay();
    for (const drone of this.drones.values()) drone.destroy();
    this.drones.clear();
    this.nextNumber = 1;
    this.groups.clear();
  }

  deployBulk(center: Coordinates, count: number, spacing: number, type: DroneType = "normal"): DroneController[] {
    return formationSlots(center, count, spacing).map(position => this.deploy(position, type));
  }

  setBatchSpeed(ids: string[], speed: number): void {
    if (!Number.isFinite(speed) || speed <= 0) throw new Error("Enter a positive batch speed in mph.");
    const members = ids.map(id => {
      const member = this.drones.get(id);
      if (!member) throw new Error(`Unknown drone: ${id}`);
      return member;
    });
    for (const member of members) member.speedMph = speed;
  }

  takeBatch(ids: string[], speed: number): void {
    const unique = [...new Set(ids)];
    if (!unique.length) throw new Error("Select at least one drone for batch control.");
    // Validate before releasing or changing anything.
    if (unique.some(id => !this.drones.has(id)) || !Number.isFinite(speed) || speed <= 0) throw new Error("Choose deployed drones and a positive batch speed.");
    this.releaseBatch();
    this.stopReplay();
    this.manualBatch = unique.map(id => this.drones.get(id)!);
    const color = this.manualBatch[0].colorHex;
    this.setBatchSpeed(unique, speed);
    for (const member of this.manualBatch) { member.setColor(color); member.setManualControl(true); }
  }

  moveBatch(east: number, north: number, up: number, seconds: number, heading: number): void {
    const moves = this.manualBatch.map(member => member.prepareManualMove(east, north, up, seconds, heading));
    if (moves.some(move => !move)) {
      for (const member of this.manualBatch) { member.stopManualMotion(); member.collisionBlocked = true; }
      return;
    }
    this.manualBatch.forEach((member, i) => member.applyManualMove(moves[i]!));
  }

  get blockedCount(): number { return this.replayFlights.filter(flight => flight.drone.replayBlocked).length; }

  releaseBatch(): void {
    if (!this.manualBatch.length) return;
    const members = this.manualBatch;
    for (const member of members) member.setManualControl(false);
    const positions = members.map(member => {
      const p = member.snapshot(); return Cesium.Cartesian3.fromDegrees(p.longitude, p.latitude, p.altitude);
    });
    const id = `batch_pickup_${++this.batchMarkerNumber}`;
    const markerColor = new Cesium.CallbackProperty(() => Cesium.Color.fromCssColorString(members[0].colorHex), false);
    this.viewer.entities.add({
      id,
      position: Cesium.BoundingSphere.fromPoints(positions).center,
      point: { pixelSize: 18, color: markerColor, disableDepthTestDistance: Number.POSITIVE_INFINITY },
      label: { text: `PICK UP BATCH (${members.length})`, font: "600 13px system-ui", showBackground: true, fillColor: markerColor, pixelOffset: new Cesium.Cartesian2(0, -26), disableDepthTestDistance: Number.POSITIVE_INFINITY },
    });
    this.batchMarkers.set(id, members.map(member => member.id));
    this.manualBatch = [];
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
    if (!this.replay.running || this.paused) return;
    this.replay.elapsed = Math.min(this.replay.duration, Math.max(0, nowSeconds - this.replayStart));
    this.replay.arrived = 0;
    for (const flight of this.replayFlights) {
      flight.drone.replayAt(this.replay.elapsed, this.groupFlight);
      if (!flight.drone.replayBlocked && flight.drone.replayComplete) this.replay.arrived++;
    }
    this.replay.running = this.replay.arrived + this.blockedCount < this.replay.total;
  }

  stopReplay(): void {
    this.paused = false;
    if (this.groupFlight) for (const flight of this.replayFlights) flight.drone.stopCommand();
    this.groupFlight = false;
    for (const drone of this.drones.values()) drone.hideReplay();
    this.replayFlights = [];
    this.replay = { running: false, elapsed: 0, duration: 0, total: 0, arrived: 0 };
  }
}
