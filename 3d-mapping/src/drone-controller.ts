import * as Cesium from "cesium";
import type { Coordinates, MissionCommand, MissionStep } from "./mission";
import { movementBlocked } from "./collision";

export type DroneSnapshot = Coordinates & { state: string; currentStep: number; totalSteps: number };

export const METERS_PER_SECOND_PER_MPH = 0.44704;
export type DroneType = "normal" | "survey";

function destinationPoint(origin: Coordinates, eastMeters: number, northMeters: number): Coordinates {
  const center = Cesium.Cartesian3.fromDegrees(origin.longitude, origin.latitude, origin.altitude);
  const moved = Cesium.Matrix4.multiplyByPoint(Cesium.Transforms.eastNorthUpToFixedFrame(center), new Cesium.Cartesian3(eastMeters, northMeters, 0), new Cesium.Cartesian3());
  const geographic = Cesium.Cartographic.fromCartesian(moved);
  return {
    latitude: Cesium.Math.toDegrees(geographic.latitude),
    longitude: Cesium.Math.toDegrees(geographic.longitude),
    altitude: origin.altitude,
  };
}

function distanceMeters(a: Coordinates, b: Coordinates): number {
  return Cesium.Cartesian3.distance(
    Cesium.Cartesian3.fromDegrees(a.longitude, a.latitude, a.altitude),
    Cesium.Cartesian3.fromDegrees(b.longitude, b.latitude, b.altitude),
  );
}

function interpolate(a: Coordinates, b: Coordinates, fraction: number): Coordinates {
  return {
    latitude: Cesium.Math.lerp(a.latitude, b.latitude, fraction),
    longitude: Cesium.Math.lerp(a.longitude, b.longitude, fraction),
    altitude: Cesium.Math.lerp(a.altitude, b.altitude, fraction),
  };
}

export class DroneController {
  collisionBlocked = false;
  replayBlocked = false;
  private replayDistance = 0;
  private readonly home: Coordinates;
  private position: Coordinates;
  private mission: MissionCommand | null = null;
  private stepIndex = 0;
  private stepElapsed = 0;
  private orbitCenter: Coordinates | null = null;
  private state = "IDLE";
  private manualHeading = 0;
  private manualVelocity = new Cesium.Cartesian3();
  private configuredSpeedMph = 60;
  private readonly entity: Cesium.Entity;
  private readonly originEntity: Cesium.Entity;
  private readonly trailEntity: Cesium.Entity;
  private trailPoints: Cesium.Cartesian3[] = [];
  private preserveTrailEndpoint = false;
  private trailDistances: number[] = [];
  private readonly arrows: Cesium.Entity[] = [];
  private readonly releases: Cesium.Entity[] = [];
  private lastRenderedPosition: Cesium.Cartesian3 | undefined;
  private travelDirection: Cesium.Cartesian3 | undefined;
  private replayEntity: Cesium.Entity | undefined;
  private replayPosition = new Cesium.Cartesian3();
  private replayPoints: Cesium.Cartesian3[] = [];
  private replayDistances: number[] = [];
  private replaySpeed = 0;
  private readonly surveyCone?: Cesium.Entity;
  private readonly crashIndicator: Cesium.Entity;
  private crashUntil = 0;
  private crashPosition = new Cesium.Cartesian3();
  private coverage: Cesium.Entity[] = [];
  private lastCoveragePosition?: Cesium.Cartesian3;
  private coverageNumber = 0;

  get routeLength(): number {
    return this.trailPoints.slice(1).reduce((sum, p, i) => sum + Cesium.Cartesian3.distance(this.trailPoints[i], p), 0);
  }
  get coverageCount(): number { return this.coverage.length; }
  capture() {
    return { id: this.id, home: this.homeCoordinates, type: this.droneType, position: { ...this.position }, heading: this.manualHeading, color: this.colorHex, speed: this.speedMph,
      points: this.trailPoints.map(p => Cesium.Cartesian3.clone(p)), releases: [...this.releases], coverage: [...this.coverage], coverageNumber: this.coverageNumber };
  }
  restore(data: ReturnType<DroneController["capture"]>): void {
    this.position = { ...data.position }; this.manualHeading = data.heading; this.speedMph = data.speed;
    this.trailPoints = data.points.map(p => Cesium.Cartesian3.clone(p));
    for (const entity of data.releases) this.releases.push(this.viewer.entities.add(entity));
    for (const entity of data.coverage) this.coverage.push(this.viewer.entities.add(entity));
    this.coverageNumber = data.coverageNumber;
    this.originEntity.position = new Cesium.ConstantPositionProperty(this.trailPoints[0]);
    this.originEntity.show = this.trailEntity.show = this.trailPoints.length > 0;
    this.lastRenderedPosition = this.visualPosition(); this.lastCoveragePosition = this.visualPosition();
    this.state = "HOVERING"; this.preserveTrailEndpoint = true;
    this.setColor(data.color); this.updateTrailArrows();
  }

  private recordCoverage(): void {
    if (this.droneType !== "survey") return;
    const position = this.visualPosition();
    if (this.lastCoveragePosition && Cesium.Cartesian3.distance(position, this.lastCoveragePosition) < 10) return;
    this.lastCoveragePosition = Cesium.Cartesian3.clone(position);
    // Approximate footprint: project the cone's far rim onto the map, without visibility analysis.
    const direction = this.viewDirection();
    const right = Cesium.Cartesian3.normalize(Cesium.Cartesian3.cross(direction, Cesium.Cartesian3.mostOrthogonalAxis(direction, new Cesium.Cartesian3()), new Cesium.Cartesian3()), new Cesium.Cartesian3());
    const up = Cesium.Cartesian3.cross(direction, right, new Cesium.Cartesian3());
    const center = Cesium.Cartesian3.add(position, Cesium.Cartesian3.multiplyByScalar(direction, 80, new Cesium.Cartesian3()), new Cesium.Cartesian3());
    const points = Array.from({ length: 24 }, (_, i) => {
      const angle = i / 24 * Math.PI * 2;
      const rim = Cesium.Cartesian3.add(center, Cesium.Cartesian3.add(Cesium.Cartesian3.multiplyByScalar(right, 30 * Math.cos(angle), new Cesium.Cartesian3()), Cesium.Cartesian3.multiplyByScalar(up, 30 * Math.sin(angle), new Cesium.Cartesian3()), new Cesium.Cartesian3()), new Cesium.Cartesian3());
      const geo = Cesium.Cartographic.fromCartesian(rim);
      return Cesium.Cartesian3.fromRadians(geo.longitude, geo.latitude);
    });
    this.coverage.push(this.viewer.entities.add({ id: `${this.id}_coverage_${++this.coverageNumber}`, polygon: { hierarchy: points, material: this.color.withAlpha(0.15), classificationType: Cesium.ClassificationType.BOTH } }));
    if (this.coverage.length > 500) this.viewer.entities.remove(this.coverage.shift()!);
  }

  constructor(private readonly viewer: Cesium.Viewer, home: Coordinates, private color = Cesium.Color.fromCssColorString("#35e8ff"), readonly id = "drone_1", readonly droneType: DroneType = "normal") {
    this.home = { ...home };
    this.position = { ...home };
    this.entity = viewer.entities.add({
      id: this.id,
      name: this.id.replace("_", " "),
      position: new Cesium.CallbackPositionProperty((_time, result) => Cesium.Cartesian3.fromDegrees(this.position.longitude, this.position.latitude, this.position.altitude, undefined, result), false),
      orientation: new Cesium.CallbackProperty(() => this.orientationAt(this.position), false),
      polyline: {
        positions: new Cesium.CallbackProperty(() => this.leaderPositions(), false),
        width: 14,
        material: new Cesium.PolylineArrowMaterialProperty(this.color),
        arcType: Cesium.ArcType.NONE,
        clampToGround: false,
      },
      label: { text: this.id.replace("_", " ").toUpperCase(), font: "600 13px system-ui", fillColor: this.color, showBackground: true, backgroundColor: Cesium.Color.fromAlpha(Cesium.Color.BLACK, 0.7), pixelOffset: new Cesium.Cartesian2(0, -28) },
    });
    this.originEntity = viewer.entities.add({
      id: `${this.id}_origin`,
      name: "Drone starting position",
      show: false,
      box: {
        dimensions: new Cesium.Cartesian3(16, 10, 4),
        material: this.color.withAlpha(0.3),
        outline: true,
        outlineColor: this.color,
      },
      label: { text: "START", font: "600 13px system-ui", fillColor: this.color, showBackground: true, pixelOffset: new Cesium.Cartesian2(0, 28) },
    });
    this.trailEntity = viewer.entities.add({
      id: `${this.id}_trail`,
      show: false,
      polyline: {
        positions: new Cesium.CallbackProperty(() => this.trailPoints, false),
        width: 3,
        material: this.color,
        arcType: Cesium.ArcType.NONE,
        clampToGround: false,
      },
    });
    this.lastRenderedPosition = Cesium.Cartesian3.fromDegrees(home.longitude, home.latitude, home.altitude);
    this.crashIndicator = viewer.entities.add({
      id: `${id}_crash`,
      position: new Cesium.CallbackPositionProperty(() => this.crashPosition, false),
      point: { show: new Cesium.CallbackProperty(() => Date.now() < this.crashUntil, false), pixelSize: 20, color: Cesium.Color.ORANGERED, outlineColor: Cesium.Color.WHITE, outlineWidth: 2, disableDepthTestDistance: Infinity },
      label: { show: new Cesium.CallbackProperty(() => Date.now() < this.crashUntil, false), text: "⚠ COLLISION — STOPPED", font: "600 13px system-ui", fillColor: Cesium.Color.ORANGERED, showBackground: true, pixelOffset: new Cesium.Cartesian2(0, -48), disableDepthTestDistance: Infinity },
    });
    if (droneType === "survey") {
      this.surveyCone = viewer.entities.add({
        id: `${id}_survey_cone`,
        position: new Cesium.CallbackPositionProperty(() => {
          const mount = Cesium.Matrix4.multiplyByPoint(Cesium.Transforms.eastNorthUpToFixedFrame(this.visualPosition()), new Cesium.Cartesian3(0, 0, -2), new Cesium.Cartesian3());
          return Cesium.Cartesian3.add(mount, Cesium.Cartesian3.multiplyByScalar(this.viewDirection(), 40, new Cesium.Cartesian3()), new Cesium.Cartesian3());
        }, false),
        orientation: new Cesium.CallbackProperty(() => {
          const direction = this.viewDirection();
          const axis = Cesium.Cartesian3.cross(Cesium.Cartesian3.UNIT_Z, direction, new Cesium.Cartesian3());
          if (Cesium.Cartesian3.magnitudeSquared(axis) < 1e-12) return direction.z > 0 ? Cesium.Quaternion.IDENTITY : Cesium.Quaternion.fromAxisAngle(Cesium.Cartesian3.UNIT_X, Math.PI);
          return Cesium.Quaternion.fromAxisAngle(Cesium.Cartesian3.normalize(axis, axis), Math.acos(Cesium.Math.clamp(direction.z, -1, 1)));
        }, false),
        cylinder: { length: 80, bottomRadius: 0, topRadius: 30, material: new Cesium.ColorMaterialProperty(new Cesium.CallbackProperty(() => this.color.withAlpha(0.16), false)), outline: true, outlineColor: new Cesium.CallbackProperty(() => this.color.withAlpha(0.5), false), numberOfVerticalLines: 8 },
      });
    }
  }

  private visualPosition(): Cesium.Cartesian3 {
    return this.replayEntity ? this.replayPosition : Cesium.Cartesian3.fromDegrees(this.position.longitude, this.position.latitude, this.position.altitude);
  }

  private viewDirection(): Cesium.Cartesian3 {
    const frame = Cesium.Transforms.eastNorthUpToFixedFrame(this.visualPosition());
    const velocity = this.state === "MANUAL" && !this.replayEntity ? this.manualVelocity
      : this.travelDirection ? Cesium.Matrix4.multiplyByPointAsVector(Cesium.Matrix4.inverseTransformation(frame, new Cesium.Matrix4()), this.travelDirection, new Cesium.Cartesian3()) : Cesium.Cartesian3.ZERO;
    const horizontal = Math.hypot(velocity.x, velocity.y);
    const heading = this.state === "MANUAL" && !this.replayEntity || horizontal < 0.0001 ? this.manualHeading : Math.atan2(velocity.x, velocity.y);
    const flightPitch = Cesium.Cartesian3.magnitude(velocity) < 0.0001 ? 0 : Math.atan2(velocity.z, horizontal);
    // Underside camera points down 60 degrees in level flight. Limit pitch so
    // even the cone's upper rim (half-angle ~21 degrees) remains below its mount.
    const pitch = Cesium.Math.clamp(-Math.PI / 3 + flightPitch, Cesium.Math.toRadians(-85), Cesium.Math.toRadians(-30));
    return Cesium.Matrix4.multiplyByPointAsVector(frame, new Cesium.Cartesian3(Math.sin(heading) * Math.cos(pitch), Math.cos(heading) * Math.cos(pitch), Math.sin(pitch)), new Cesium.Cartesian3());
  }

  private showCrash(at = this.visualPosition()): void {
    this.crashPosition = Cesium.Cartesian3.clone(at);
    this.crashUntil = Date.now() + 4000;
  }

  run(command: MissionCommand): void {
    this.collisionBlocked = false;
    this.hideReplay();
    this.mission = command;
    this.stepIndex = 0;
    this.stepElapsed = 0;
    this.orbitCenter = null;
    this.state = "TAKING_OFF";
  }

  setManualControl(enabled: boolean): void {
    if (enabled) this.hideReplay();
    if (!enabled && this.state === "MANUAL") {
      this.preserveTrailEndpoint = true;
      const number = this.releases.length + 1;
      this.releases.push(this.viewer.entities.add({
        id: `${this.id}_release_${number}`,
        position: Cesium.Cartesian3.fromDegrees(this.position.longitude, this.position.latitude, this.position.altitude),
        box: { dimensions: new Cesium.Cartesian3(16, 10, 4), material: this.color.withAlpha(0.8), outline: true, outlineColor: this.color },
        orientation: this.orientationAt(this.position),
        label: { text: `RELEASE ${number}`, font: "600 12px system-ui", fillColor: this.color, showBackground: true, pixelOffset: new Cesium.Cartesian2(0, 22) },
      }));
    }
    if (enabled && this.trailPoints.length === 0) {
      const origin = Cesium.Cartesian3.fromDegrees(this.position.longitude, this.position.latitude, this.position.altitude);
      this.originEntity.position = new Cesium.ConstantPositionProperty(origin);
      this.originEntity.orientation = new Cesium.ConstantProperty(this.orientationAt(this.position));
      this.originEntity.show = true;
      this.trailPoints = [origin, Cesium.Cartesian3.clone(origin)];
      this.trailEntity.show = true;
    }
    this.stopManualMotion();
    this.mission = null;
    this.stepIndex = 0;
    this.stepElapsed = 0;
    this.orbitCenter = null;
    this.state = enabled ? "MANUAL" : "HOVERING";
  }

  stopManualMotion(): void {
    this.manualVelocity = new Cesium.Cartesian3();
  }

  get heading(): number { return this.manualHeading; }
  stopCommand(): void { this.stopManualMotion(); this.state = "HOVERING"; }
  get colorHex(): string { return this.color.toCssHexString(); }
  setColor(hex: string): void {
    const color = Cesium.Color.fromCssColorString(hex);
    if (!color) throw new Error("Invalid drone color.");
    this.color = color;
    for (const patch of this.coverage) patch.polygon!.material = new Cesium.ColorMaterialProperty(color.withAlpha(0.15));
    this.entity.polyline!.material = new Cesium.PolylineArrowMaterialProperty(color);
    this.trailEntity.polyline!.material = new Cesium.ColorMaterialProperty(color);
    for (const item of [this.entity, this.originEntity, ...this.releases, ...this.arrows, ...(this.replayEntity ? [this.replayEntity] : [])]) {
      if (item.label) item.label.fillColor = new Cesium.ConstantProperty(color);
      if (item.box) {
        item.box.material = new Cesium.ColorMaterialProperty(color.withAlpha(item === this.originEntity ? 0.3 : 1));
        item.box.outlineColor = new Cesium.ConstantProperty(color);
      }
      if (item !== this.entity && item.polyline) item.polyline.material = new Cesium.PolylineArrowMaterialProperty(color);
    }
  }

  commandDestination(destination: Coordinates): void {
    this.hideReplay();
    this.stopManualMotion();
    this.mission = null;
    this.state = "NAVIGATING";
    const origin = Cesium.Cartesian3.fromDegrees(this.position.longitude, this.position.latitude, this.position.altitude);
    this.originEntity.position = new Cesium.ConstantPositionProperty(origin);
    this.originEntity.show = true;
    for (const marker of [...this.releases, ...this.arrows]) this.viewer.entities.remove(marker);
    this.releases.length = 0;
    this.arrows.length = 0;
    this.trailPoints = [origin, Cesium.Cartesian3.fromDegrees(destination.longitude, destination.latitude, destination.altitude)];
    this.trailEntity.show = true;
    this.updateTrailArrows();
  }
  get homeCoordinates(): Coordinates { return { ...this.home }; }
  get speedMph(): number { return this.configuredSpeedMph; }
  set speedMph(value: number) {
    if (!Number.isFinite(value) || value <= 0) throw new Error("Enter a speed greater than zero in mph.");
    this.configuredSpeedMph = value;
  }

  destroy(): void {
    this.reset();
    this.viewer.entities.remove(this.entity);
    this.viewer.entities.remove(this.originEntity);
    this.viewer.entities.remove(this.trailEntity);
    this.viewer.entities.remove(this.crashIndicator);
    if (this.surveyCone) this.viewer.entities.remove(this.surveyCone);
  }

  moveManually(east: number, north: number, up: number, seconds: number, heading = 0): void {
    const next = this.prepareManualMove(east, north, up, seconds, heading);
    if (next) this.applyManualMove(next);
  }

  prepareManualMove(east: number, north: number, up: number, seconds: number, heading = 0): Coordinates | undefined {
    if (this.state !== "MANUAL") return;
    this.manualHeading = heading;
    const scale = this.configuredSpeedMph * METERS_PER_SECOND_PER_MPH / Math.max(1, Math.hypot(east, north, up));
    const target = new Cesium.Cartesian3(east * scale, north * scale, up * scale);
    const damping = target.equals(Cesium.Cartesian3.ZERO) ? 16 : 10;
    Cesium.Cartesian3.lerp(this.manualVelocity, target, 1 - Math.exp(-damping * seconds), this.manualVelocity);
    const next = destinationPoint(this.position, this.manualVelocity.x * seconds, this.manualVelocity.y * seconds);
    next.altitude += this.manualVelocity.z * seconds;
    this.collisionBlocked = this.blocksMove(this.position, next);
    if (this.collisionBlocked) { this.showCrash(); this.stopManualMotion(); return; }
    return next;
  }

  applyManualMove(next: Coordinates): void {
    this.position = next;
    this.syncEntity();
  }

  private blocksMove(from: Coordinates, to: Coordinates): boolean {
    return movementBlocked(this.viewer, Cesium.Cartesian3.fromDegrees(from.longitude, from.latitude, from.altitude), Cesium.Cartesian3.fromDegrees(to.longitude, to.latitude, to.altitude));
  }

  reset(): void {
    for (const patch of this.coverage) this.viewer.entities.remove(patch);
    this.coverage = []; this.lastCoveragePosition = undefined; this.coverageNumber = 0;
    this.crashUntil = 0;
    this.collisionBlocked = this.replayBlocked = false;
    this.hideReplay();
    this.lastRenderedPosition = undefined;
    this.travelDirection = undefined;
    for (const marker of [...this.releases, ...this.arrows]) this.viewer.entities.remove(marker);
    this.releases.length = 0;
    this.arrows.length = 0;
    this.trailDistances = [];
    this.trailPoints = [];
    this.originEntity.show = false;
    this.trailEntity.show = false;
    this.position = { ...this.home };
    this.mission = null;
    this.stepIndex = 0;
    this.stepElapsed = 0;
    this.orbitCenter = null;
    this.state = "IDLE";
    this.syncEntity();
  }

  update(deltaSeconds: number): void {
    if (!this.mission || this.stepIndex >= this.mission.mission.length) return;
    const step = this.mission.mission[this.stepIndex];
    this.updateStep(step, Math.min(deltaSeconds, 0.1));
  }

  snapshot(): DroneSnapshot {
    return { ...this.position, state: this.state, currentStep: this.mission ? this.stepIndex + 1 : 0, totalSteps: this.mission?.mission.length ?? 0 };
  }

  beginReplay(): number | null {
    this.replayBlocked = false;
    this.collisionBlocked = false;
    this.replayDistance = 0;
    this.hideReplay();
    this.stopManualMotion();
    this.mission = null;
    this.replayPoints = this.trailPoints.map(point => Cesium.Cartesian3.clone(point));
    this.replayDistances = [0];
    for (let i = 1; i < this.replayPoints.length; i++) {
      this.replayDistances.push(this.replayDistances[i - 1] + Cesium.Cartesian3.distance(this.replayPoints[i - 1], this.replayPoints[i]));
    }
    const length = this.replayDistances.at(-1) ?? 0;
    if (length < 0.001) return null;
    this.replaySpeed = this.speedMph * METERS_PER_SECOND_PER_MPH;
    this.replayPosition = Cesium.Cartesian3.clone(this.replayPoints[0]);
    this.entity.show = false;
    this.replayEntity = this.viewer.entities.add({
      id: `${this.id}_replay`,
      position: new Cesium.CallbackPositionProperty((_time, result) => Cesium.Cartesian3.clone(this.replayPosition, result), false),
      box: { dimensions: new Cesium.Cartesian3(16, 10, 4), material: this.color, outline: true, outlineColor: this.color },
      label: { text: this.id.replace("_", " ").toUpperCase(), font: "600 13px system-ui", fillColor: this.color, showBackground: true, pixelOffset: new Cesium.Cartesian2(0, -28) },
    });
    return length / this.replaySpeed;
  }

  replayAt(elapsedSeconds: number, moveDrone = false): void {
    if (!this.replayEntity || this.replayBlocked) return;
    const length = this.replayDistances.at(-1)!;
    const distance = elapsedSeconds >= length / this.replaySpeed ? length : Math.max(0, elapsedSeconds) * this.replaySpeed;
    let low = 1, high = this.replayDistances.length - 1;
    while (low < high) {
      const mid = Math.floor((low + high) / 2);
      if (this.replayDistances[mid] < distance) low = mid + 1; else high = mid;
    }
    const start = this.replayDistances[low - 1];
    const span = this.replayDistances[low] - start;
    const next = Cesium.Cartesian3.lerp(this.replayPoints[low - 1], this.replayPoints[low], span > 0 ? (distance - start) / span : 0, new Cesium.Cartesian3());
    let previous = this.replayPosition;
    const crossed = this.replayPoints.filter((_, i) => this.replayDistances[i] > this.replayDistance && this.replayDistances[i] < distance);
    for (const point of [...crossed, next]) {
      if (movementBlocked(this.viewer, previous, point)) {
        this.showCrash();
        this.replayBlocked = this.collisionBlocked = true;
        this.state = "BLOCKED";
        return;
      }
      previous = point;
    }
    if (Cesium.Cartesian3.distance(this.replayPosition, next) > 0.001) this.travelDirection = Cesium.Cartesian3.normalize(Cesium.Cartesian3.subtract(next, this.replayPosition, new Cesium.Cartesian3()), new Cesium.Cartesian3());
    this.replayPosition = next;
    if (distance > this.replayDistance) this.recordCoverage();
    this.replayDistance = distance;
    if (moveDrone) {
      const geographic = Cesium.Cartographic.fromCartesian(this.replayPosition);
      this.position = { latitude: Cesium.Math.toDegrees(geographic.latitude), longitude: Cesium.Math.toDegrees(geographic.longitude), altitude: geographic.height };
      this.lastRenderedPosition = Cesium.Cartesian3.clone(this.replayPosition);
      this.state = distance >= length ? "HOVERING" : "NAVIGATING";
    }
  }

  hideReplay(): void {
    if (this.replayEntity) this.viewer.entities.remove(this.replayEntity);
    this.replayEntity = undefined;
    this.entity.show = true;
  }

  private updateStep(step: MissionStep, deltaSeconds: number): void {
    const previous = { ...this.position };
    if (step.action === "goto" || step.action === "return_home") {
      const target = step.action === "goto" ? step : this.home;
      const speed = step.speed_mps ?? this.configuredSpeedMph * METERS_PER_SECOND_PER_MPH;
      const distance = distanceMeters(this.position, target);
      const fraction = distance === 0 ? 1 : Math.min(1, (speed * deltaSeconds) / distance);
      this.position = interpolate(this.position, target, fraction);
      this.state = step.action === "goto" ? "NAVIGATING" : "RETURNING_HOME";
      if (fraction === 1) this.advance();
    } else if (step.action === "hover") {
      this.state = "HOVERING";
      this.stepElapsed += deltaSeconds;
      if (this.stepElapsed >= step.duration_s) this.advance();
    } else {
      this.state = "ORBITING";
      this.orbitCenter ??= { ...this.position };
      this.stepElapsed += deltaSeconds;
      const direction = step.clockwise === false ? -1 : 1;
      const angle = direction * ((this.stepElapsed / step.duration_s) * Cesium.Math.TWO_PI);
      this.position = destinationPoint(this.orbitCenter, Math.cos(angle) * step.radius_m, Math.sin(angle) * step.radius_m);
      if (this.stepElapsed >= step.duration_s) this.advance();
    }
    this.collisionBlocked = this.blocksMove(previous, this.position);
    if (this.collisionBlocked) {
      this.position = previous;
      this.showCrash();
      this.mission = null;
      this.state = "BLOCKED";
      this.stopManualMotion();
    }
    this.syncEntity();
  }

  private advance(): void {
    this.stepIndex += 1;
    this.stepElapsed = 0;
    this.orbitCenter = null;
    if (this.mission && this.stepIndex >= this.mission.mission.length) this.state = "IDLE";
  }

  private syncEntity(): void {
    if (this.state === "MANUAL" || this.mission) this.recordCoverage();
    const current = Cesium.Cartesian3.fromDegrees(this.position.longitude, this.position.latitude, this.position.altitude);
    if (this.lastRenderedPosition && Cesium.Cartesian3.distance(current, this.lastRenderedPosition) > 0.001) {
      this.travelDirection = Cesium.Cartesian3.normalize(Cesium.Cartesian3.subtract(current, this.lastRenderedPosition, new Cesium.Cartesian3()), new Cesium.Cartesian3());
    }
    this.lastRenderedPosition = Cesium.Cartesian3.clone(current);
    if (this.trailPoints.length >= 2) {
      if (this.preserveTrailEndpoint) {
        this.trailPoints.push(Cesium.Cartesian3.clone(this.trailPoints[this.trailPoints.length - 1]));
        this.preserveTrailEndpoint = false;
      }
      // Keep an exact live endpoint, sampling the route at two-meter intervals.
      this.trailPoints[this.trailPoints.length - 1] = current;
      if (Cesium.Cartesian3.distance(this.trailPoints[this.trailPoints.length - 2], current) >= 2) {
        this.trailPoints.push(Cesium.Cartesian3.clone(current));
      }
      // Bound geometry while retaining the starting point and live endpoint.
      if (this.trailPoints.length > 4096) {
        const last = this.trailPoints.length - 1;
        this.trailPoints = this.trailPoints.filter((_, index) => index % 2 === 0 || index === last);
      }
      this.updateTrailArrows();
    }
  }

  private updateTrailArrows(): void {
    this.trailDistances = [0];
    for (let i = 1; i < this.trailPoints.length; i++) {
      this.trailDistances.push(this.trailDistances[i - 1] + Cesium.Cartesian3.distance(this.trailPoints[i - 1], this.trailPoints[i]));
    }
    const length = this.trailDistances.at(-1) ?? 0;
    const count = length < 4 ? 0 : Math.min(128, Math.max(1, Math.ceil(length / 45)));
    while (this.arrows.length < count) {
      const index = this.arrows.length;
      this.arrows.push(this.viewer.entities.add({
        id: `${this.id}_arrow_${index}`,
        polyline: {
          positions: new Cesium.CallbackProperty(() => this.arrowPositions(index), false),
          width: 12,
          material: new Cesium.PolylineArrowMaterialProperty(this.color),
          arcType: Cesium.ArcType.NONE,
          clampToGround: false,
        },
      }));
    }
  }

  private arrowPositions(index: number): Cesium.Cartesian3[] {
    const length = this.trailDistances.at(-1) ?? 0;
    if (length < 4) return [];
    const spacing = length / this.arrows.length;
    // Advance from earlier to later recorded points, independently of the simulation clock.
    const head = ((performance.now() / 1000 * 12) % spacing) + index * spacing;
    const tail = Math.max(0, head - Math.min(10, spacing * 0.45));
    if (head - tail < 0.1) return [];
    return [this.pointAlongTrail(tail), this.pointAlongTrail((head + tail) / 2), this.pointAlongTrail(head)];
  }

  private leaderPositions(): Cesium.Cartesian3[] {
    const head = Cesium.Cartesian3.fromDegrees(this.position.longitude, this.position.latitude, this.position.altitude);
    const direction = this.travelDirection ?? Cesium.Matrix4.multiplyByPointAsVector(Cesium.Transforms.eastNorthUpToFixedFrame(head), new Cesium.Cartesian3(Math.sin(this.manualHeading), Math.cos(this.manualHeading), 0), new Cesium.Cartesian3());
    const tail = Cesium.Cartesian3.subtract(head, Cesium.Cartesian3.multiplyByScalar(direction, 3, new Cesium.Cartesian3()), new Cesium.Cartesian3());
    return [tail, head];
  }

  private pointAlongTrail(distance: number): Cesium.Cartesian3 {
    let low = 1;
    let high = this.trailDistances.length - 1;
    while (low < high) {
      const mid = Math.floor((low + high) / 2);
      if (this.trailDistances[mid] < distance) low = mid + 1;
      else high = mid;
    }
    const start = this.trailDistances[low - 1];
    const span = this.trailDistances[low] - start;
    return Cesium.Cartesian3.lerp(this.trailPoints[low - 1], this.trailPoints[low], span > 0 ? (distance - start) / span : 0, new Cesium.Cartesian3());
  }

  private orientationAt(position: Coordinates): Cesium.Quaternion {
    const cartesian = Cesium.Cartesian3.fromDegrees(position.longitude, position.latitude, position.altitude);
    return Cesium.Transforms.headingPitchRollQuaternion(cartesian, new Cesium.HeadingPitchRoll(Cesium.Math.toRadians(90) + (this.state === "MANUAL" ? this.manualHeading : 0), 0, 0));
  }
}
