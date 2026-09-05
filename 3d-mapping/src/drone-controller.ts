import * as Cesium from "cesium";
import type { Coordinates, MissionCommand, MissionStep } from "./mission";

export type DroneSnapshot = Coordinates & { state: string; currentStep: number; totalSteps: number };

const METERS_PER_DEGREE_LATITUDE = 111_320;

function destinationPoint(origin: Coordinates, eastMeters: number, northMeters: number): Coordinates {
  return {
    latitude: origin.latitude + northMeters / METERS_PER_DEGREE_LATITUDE,
    longitude: origin.longitude + eastMeters / (METERS_PER_DEGREE_LATITUDE * Math.cos(Cesium.Math.toRadians(origin.latitude))),
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
  private readonly entity: Cesium.Entity;
  private readonly originEntity: Cesium.Entity;
  private readonly trailEntity: Cesium.Entity;
  private trailPoints: Cesium.Cartesian3[] = [];

  constructor(private readonly viewer: Cesium.Viewer, home: Coordinates) {
    this.home = { ...home };
    this.position = { ...home };
    this.entity = viewer.entities.add({
      id: "drone_1",
      name: "Drone 1",
      position: new Cesium.ConstantPositionProperty(Cesium.Cartesian3.fromDegrees(home.longitude, home.latitude, home.altitude)),
      orientation: new Cesium.ConstantProperty(this.orientationAt(home)),
      box: {
        dimensions: new Cesium.Cartesian3(16, 10, 4),
        material: Cesium.Color.fromCssColorString("#35e8ff").withAlpha(0.9),
        outline: true,
        outlineColor: Cesium.Color.WHITE,
      },
      label: { text: "DRONE 1", font: "600 13px system-ui", fillColor: Cesium.Color.WHITE, showBackground: true, backgroundColor: Cesium.Color.fromAlpha(Cesium.Color.BLACK, 0.7), pixelOffset: new Cesium.Cartesian2(0, -28) },
    });
    this.originEntity = viewer.entities.add({
      id: "drone_1_origin",
      name: "Drone starting position",
      show: false,
      box: {
        dimensions: new Cesium.Cartesian3(16, 10, 4),
        material: Cesium.Color.ORANGE.withAlpha(0.3),
        outline: true,
        outlineColor: Cesium.Color.ORANGE,
      },
      label: { text: "START", font: "600 13px system-ui", fillColor: Cesium.Color.ORANGE, showBackground: true, pixelOffset: new Cesium.Cartesian2(0, 28) },
    });
    this.trailEntity = viewer.entities.add({
      id: "drone_1_trail",
      show: false,
      polyline: {
        positions: new Cesium.CallbackProperty(() => this.trailPoints, false),
        width: 3,
        material: Cesium.Color.fromCssColorString("#35e8ff"),
        arcType: Cesium.ArcType.NONE,
        clampToGround: false,
      },
    });
  }

  run(command: MissionCommand): void {
    this.mission = command;
    this.stepIndex = 0;
    this.stepElapsed = 0;
    this.orbitCenter = null;
    this.state = "TAKING_OFF";
  }

  setManualControl(enabled: boolean): void {
    if (enabled) {
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

  moveManually(east: number, north: number, up: number, seconds: number, heading = 0): void {
    if (this.state !== "MANUAL") return;
    this.manualHeading = heading;
    const scale = 45 / Math.max(1, Math.hypot(east, north, up));
    const target = new Cesium.Cartesian3(east * scale, north * scale, up * scale);
    const damping = target.equals(Cesium.Cartesian3.ZERO) ? 16 : 10;
    Cesium.Cartesian3.lerp(this.manualVelocity, target, 1 - Math.exp(-damping * seconds), this.manualVelocity);
    this.position = destinationPoint(this.position, this.manualVelocity.x * seconds, this.manualVelocity.y * seconds);
    this.position.altitude = Math.max(2, this.position.altitude + this.manualVelocity.z * seconds);
    this.syncEntity();
  }

  reset(): void {
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
      const speed = step.speed_mps ?? 25;
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
    this.entity.position = new Cesium.ConstantPositionProperty(current);
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
    }
    this.entity.orientation = new Cesium.ConstantProperty(this.orientationAt(this.position));
  }

  private orientationAt(position: Coordinates): Cesium.Quaternion {
    const cartesian = Cesium.Cartesian3.fromDegrees(position.longitude, position.latitude, position.altitude);
    return Cesium.Transforms.headingPitchRollQuaternion(cartesian, new Cesium.HeadingPitchRoll(Cesium.Math.toRadians(90) + (this.state === "MANUAL" ? this.manualHeading : 0), 0, 0));
  }
}
