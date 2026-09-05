import * as Cesium from "cesium";
import type { Coordinates, MissionCommand, MissionStep } from "./mission";

export type DroneSnapshot = Coordinates & { state: string; currentStep: number; totalSteps: number };

export const METERS_PER_SECOND_PER_MPH = 0.44704;

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
  private trailDistances: number[] = [];
  private readonly arrows: Cesium.Entity[] = [];
  private readonly releases: Cesium.Entity[] = [];
  private lastRenderedPosition: Cesium.Cartesian3 | undefined;
  private travelDirection: Cesium.Cartesian3 | undefined;

  constructor(private readonly viewer: Cesium.Viewer, home: Coordinates, private readonly color = Cesium.Color.fromCssColorString("#35e8ff"), readonly id = "drone_1") {
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
  }

  run(command: MissionCommand): void {
    this.mission = command;
    this.stepIndex = 0;
    this.stepElapsed = 0;
    this.orbitCenter = null;
    this.state = "TAKING_OFF";
  }

  setManualControl(enabled: boolean): void {
    if (!enabled && this.state === "MANUAL") {
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
  }

  moveManually(east: number, north: number, up: number, seconds: number, heading = 0): void {
    if (this.state !== "MANUAL") return;
    this.manualHeading = heading;
    const scale = this.configuredSpeedMph * METERS_PER_SECOND_PER_MPH / Math.max(1, Math.hypot(east, north, up));
    const target = new Cesium.Cartesian3(east * scale, north * scale, up * scale);
    const damping = target.equals(Cesium.Cartesian3.ZERO) ? 16 : 10;
    Cesium.Cartesian3.lerp(this.manualVelocity, target, 1 - Math.exp(-damping * seconds), this.manualVelocity);
    this.position = destinationPoint(this.position, this.manualVelocity.x * seconds, this.manualVelocity.y * seconds);
    this.position.altitude += this.manualVelocity.z * seconds;
    this.syncEntity();
  }

  reset(): void {
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

  private updateStep(step: MissionStep, deltaSeconds: number): void {
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
    this.syncEntity();
  }

  private advance(): void {
    this.stepIndex += 1;
    this.stepElapsed = 0;
    this.orbitCenter = null;
    if (this.mission && this.stepIndex >= this.mission.mission.length) this.state = "IDLE";
  }

  private syncEntity(): void {
    const current = Cesium.Cartesian3.fromDegrees(this.position.longitude, this.position.latitude, this.position.altitude);
    if (this.lastRenderedPosition && Cesium.Cartesian3.distance(current, this.lastRenderedPosition) > 0.001) {
      this.travelDirection = Cesium.Cartesian3.normalize(Cesium.Cartesian3.subtract(current, this.lastRenderedPosition, new Cesium.Cartesian3()), new Cesium.Cartesian3());
    }
    this.lastRenderedPosition = Cesium.Cartesian3.clone(current);
    if (this.trailPoints.length >= 2) {
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
