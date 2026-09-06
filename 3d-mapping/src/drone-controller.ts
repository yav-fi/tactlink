import * as Cesium from "cesium";
import type { Coordinates, MissionCommand, MissionStep } from "./mission";
import { MotionGuard } from "./collision";
import { plannedFlightStep } from "./flight-motion";
import { MOTION_TRACE_LIFETIME_MS, motionTraceAlpha } from "./motion-trace";
import { sampleSurvey, surfaceHit, SURVEY_RAYS, SURVEY_DISTANCE_BANDS, surveyStrength, surveyBand } from "./survey-surface";

export type DroneSnapshot = Coordinates & { state: string; currentStep: number; totalSteps: number };

export const METERS_PER_SECOND_PER_MPH = 0.44704;
export type DroneType = "normal" | "survey";
const MODEL_URI = "/models/uav.glb";
const TRAIL_COLOR = Cesium.Color.fromCssColorString("#ff203d");

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
  avoidanceActive = false;
  replayBlocked = false;
  replayComplete = false;
  private replayDistance = 0;
  private readonly home: Coordinates;
  private position: Coordinates;
  private mission: MissionCommand | null = null;
  private stepIndex = 0;
  private stepElapsed = 0;
  private orbitCenter: Coordinates | null = null;
  private orbitStartAngle = 0;
  private missionSpeedMps = 0;
  private missionWaypoint: Coordinates | null = null;
  private gestureMotion: { east: number; north: number; up: number; speed: number } | null = null;
  private headingTurn: { from: number; delta: number; elapsed: number; duration: number } | null = null;
  private state = "IDLE";
  private manualHeading = 0;
  private manualVelocity = new Cesium.Cartesian3();
  private configuredSpeedMph = 60;
  private readonly entity: Cesium.Entity;
  private readonly originEntity: Cesium.Entity;
  private readonly trailEntity: Cesium.Entity;
  private trailPoints: Cesium.Cartesian3[] = [];
  private readonly motionTrace: Cesium.Entity[] = [];
  private traceSamples: { position: Cesium.Cartesian3; time: number }[] = [];
  private traceSequence = 0;
  private preserveTrailEndpoint = false;
  private readonly releases: Cesium.Entity[] = [];
  private lastRenderedPosition: Cesium.Cartesian3 | undefined;
  private travelDirection: Cesium.Cartesian3 | undefined;
  private replayEntity: Cesium.Entity | undefined;
  private replayPosition = new Cesium.Cartesian3();
  private replayPoints: Cesium.Cartesian3[] = [];
  private replayDistances: number[] = [];
  private replaySpeed = 0;
  private surveySides: Cesium.Entity[] = [];
  private surveyEnds: Cesium.Cartesian3[] = [];
  private surveyOrigin = new Cesium.Cartesian3();
  private surveySampleTime = -Infinity;
  private surveyCoveragePending = false;
  private readonly crashIndicator: Cesium.Entity;
  private crashUntil = 0;
  private crashPosition = new Cesium.Cartesian3();
  private avoidanceText = "";
  private avoidanceColor = Cesium.Color.ORANGE;
  private coverage: Cesium.Entity[] = [];
  private lastCoveragePosition?: Cesium.Cartesian3;
  private coverageNumber = 0;
  private renderVersion = 0;
  private renderedHeading = 0;
  private readonly guard = new MotionGuard();
  private readonly replayGuard = new MotionGuard();
  private stepSeconds = 1 / 60;

  get routeLength(): number {
    return this.trailPoints.slice(1).reduce((sum, p, i) => sum + Cesium.Cartesian3.distance(this.trailPoints[i], p), 0);
  }
  get cameraPosition(): Cesium.Cartesian3 { return Cesium.Cartesian3.clone(this.visualPosition()); }
  get horizontalFlightHeading(): number | undefined {
    if (!this.travelDirection) return undefined;
    const inverse = Cesium.Matrix4.inverseTransformation(Cesium.Transforms.eastNorthUpToFixedFrame(this.visualPosition()), new Cesium.Matrix4());
    const local = Cesium.Matrix4.multiplyByPointAsVector(inverse, this.travelDirection, new Cesium.Cartesian3());
    return Math.hypot(local.x, local.y) > 0.01 ? Math.atan2(local.x, local.y) : undefined;
  }
  get coverageCount(): number { return this.coverage.length; }
  capture() {
    return { id: this.id, home: this.homeCoordinates, type: this.droneType, position: { ...this.position }, heading: this.manualHeading, color: this.colorHex, speed: this.speedMph,
      points: this.trailPoints.map(p => Cesium.Cartesian3.clone(p)), releases: [...this.releases], coverage: [...this.coverage], coverageNumber: this.coverageNumber };
  }
  restore(data: ReturnType<DroneController["capture"]>): void {
    this.guard.clear();
    this.replayGuard.clear();
    this.position = { ...data.position }; this.manualHeading = this.renderedHeading = data.heading; this.speedMph = data.speed;
    this.trailPoints = data.points.map(p => Cesium.Cartesian3.clone(p));
    for (const entity of data.releases) this.releases.push(this.viewer.entities.add(entity));
    for (const entity of data.coverage) this.coverage.push(this.viewer.entities.add(entity));
    this.coverageNumber = data.coverageNumber;
    this.originEntity.position = new Cesium.ConstantPositionProperty(this.trailPoints[0] ?? Cesium.Cartesian3.fromDegrees(this.home.longitude, this.home.latitude, this.home.altitude));
    this.originEntity.show = this.trailPoints.length > 0;
    this.trailEntity.show = false;
    this.lastRenderedPosition = this.visualPosition(); this.lastCoveragePosition = this.visualPosition();
    this.state = "HOVERING"; this.preserveTrailEndpoint = true;
    this.setColor(data.color);
  }

  private recordCoverage(): void { this.surveyCoveragePending = true; }

  updateSurvey(now: number): void {
    if (this.droneType !== "survey" || now - this.surveySampleTime < 500) return;
    this.surveySampleTime = now;
    this.surveyOrigin = Cesium.Matrix4.multiplyByPoint(Cesium.Transforms.eastNorthUpToFixedFrame(this.visualPosition()), new Cesium.Cartesian3(0, 0, -2), new Cesium.Cartesian3());
    const sample = sampleSurvey(this.surveyOrigin, this.viewDirection(), ray => surfaceHit(this.viewer, ray));
    this.surveyEnds = sample.hits.map(item => item.end);
    if (!this.surveyCoveragePending || !sample.centerHit) return;
    const position = this.visualPosition();
    if (this.lastCoveragePosition && Cesium.Cartesian3.distance(position, this.lastCoveragePosition) < 10) return;
    this.lastCoveragePosition = Cesium.Cartesian3.clone(position);
    this.surveyCoveragePending = false;
    for (let i = 0; i < sample.hits.length; i++) {
      const a = sample.hits[i].hit, b = sample.hits[(i + 1) % sample.hits.length].hit;
      if (!a || !b) continue;
      // Only mark triangles whose sampled vertices actually met map surfaces.
      this.coverage.push(this.viewer.entities.add({ id: `${this.id}_coverage_${++this.coverageNumber}`, polygon: {
        hierarchy: [sample.centerHit, a, b], perPositionHeight: true,
        material: this.color.withAlpha(0.22 * surveyStrength(Math.max(...[sample.centerHit, a, b].map(p => Cesium.Cartesian3.distance(this.surveyOrigin, p))))),
      } }));
      if (this.coverage.length > 500) this.viewer.entities.remove(this.coverage.shift()!);
    }
  }

  constructor(private readonly viewer: Cesium.Viewer, home: Coordinates, private color = Cesium.Color.fromCssColorString("#35e8ff"), readonly id = "drone_1", readonly droneType: DroneType = "normal") {
    this.home = { ...home };
    this.position = { ...home };
    this.crashPosition = Cesium.Cartesian3.fromDegrees(home.longitude, home.latitude, home.altitude);
    this.entity = viewer.entities.add({
      id: this.id,
      name: this.id.replace("_", " "),
      position: new Cesium.CallbackPositionProperty((_time, result) => Cesium.Cartesian3.fromDegrees(this.position.longitude, this.position.latitude, this.position.altitude, undefined, result), false),
      orientation: new Cesium.CallbackProperty(() => this.visualOrientation(), false),
      model: {
        uri: MODEL_URI,
        scale: 1.2,
        minimumPixelSize: 42,
        maximumScale: 30,
        silhouetteColor: this.color,
        silhouetteSize: 0.65,
        runAnimations: true,
        clampAnimations: false,
        shadows: Cesium.ShadowMode.DISABLED,
      },
      label: { text: this.id.replace("_", " ").toUpperCase(), font: "700 12px ui-monospace, monospace", fillColor: this.color, showBackground: true, backgroundColor: Cesium.Color.fromAlpha(Cesium.Color.BLACK, 0.76), pixelOffset: new Cesium.Cartesian2(0, -38) },
    });
    this.originEntity = viewer.entities.add({
      id: `${this.id}_origin`,
      position: Cesium.Cartesian3.fromDegrees(home.longitude, home.latitude, home.altitude),
      name: "Drone starting position",
      show: false,
      label: { text: "START", font: "600 13px system-ui", fillColor: this.color, style: Cesium.LabelStyle.FILL_AND_OUTLINE, outlineColor: Cesium.Color.BLACK, outlineWidth: 3 },
    });
    this.trailEntity = viewer.entities.add({
      id: `${this.id}_trail`,
      show: false,
      polyline: {
        positions: new Cesium.CallbackProperty(() => this.trailPoints.length >= 2 ? this.trailPoints : [this.visualPosition(), this.visualPosition()], false),
        width: 3,
        material: new Cesium.PolylineGlowMaterialProperty({ color: TRAIL_COLOR, glowPower: 0.32, taperPower: 0.7 }),
        arcType: Cesium.ArcType.NONE,
        clampToGround: false,
      },
    });
    for (let index = 0; index < 72; index++) {
      // Reused per particle: allocating colours every frame churns the heap
      // 144 objects deep per drone and shows up as stutter with a full fleet.
      const fill = Cesium.Color.clone(TRAIL_COLOR);
      const outline = Cesium.Color.clone(TRAIL_COLOR);
      this.motionTrace.push(viewer.entities.add({
        id: `${this.id}_trace_${index}`,
        position: new Cesium.CallbackPositionProperty((_time, result) => Cesium.Cartesian3.clone(this.tracePoint(index), result), false),
        point: {
          show: new Cesium.CallbackProperty(() => this.traceAlpha(index) > 0.01, false),
          pixelSize: new Cesium.CallbackProperty(() => 1.4 + 3.6 * this.traceAlpha(index), false),
          color: new Cesium.CallbackProperty(() => Cesium.Color.fromAlpha(TRAIL_COLOR, this.traceAlpha(index), fill), false),
          outlineColor: new Cesium.CallbackProperty(() => Cesium.Color.fromAlpha(TRAIL_COLOR, this.traceAlpha(index) * 0.45, outline), false),
          outlineWidth: 1,
          disableDepthTestDistance: Infinity,
        },
      }));
    }
    this.lastRenderedPosition = Cesium.Cartesian3.fromDegrees(home.longitude, home.latitude, home.altitude);
    this.crashIndicator = viewer.entities.add({
      id: `${id}_crash`,
      position: new Cesium.CallbackPositionProperty(() => this.crashPosition, false),
      point: { show: new Cesium.CallbackProperty(() => Date.now() < this.crashUntil, false), pixelSize: 20, color: new Cesium.CallbackProperty(() => this.avoidanceColor, false), outlineColor: Cesium.Color.WHITE, outlineWidth: 2, disableDepthTestDistance: Infinity },
      label: { show: new Cesium.CallbackProperty(() => Date.now() < this.crashUntil, false), text: new Cesium.CallbackProperty(() => this.avoidanceText, false), font: "600 13px system-ui", fillColor: new Cesium.CallbackProperty(() => this.avoidanceColor, false), showBackground: true, pixelOffset: new Cesium.Cartesian2(0, -48), disableDepthTestDistance: Infinity },
    });
    if (droneType === "survey") {
      for (let i = 0; i < SURVEY_RAYS; i++) {
        for (let band = 0; band < SURVEY_DISTANCE_BANDS.length - 1; band++) {
        const geometry = () => {
          if (!this.surveyEnds.length) return [];
          const mount = Cesium.Matrix4.multiplyByPoint(Cesium.Transforms.eastNorthUpToFixedFrame(this.visualPosition()), new Cesium.Cartesian3(0, 0, -2), new Cesium.Cartesian3());
          return surveyBand([mount, this.surveyEnds[i], this.surveyEnds[(i + 1) % SURVEY_RAYS]], mount, SURVEY_DISTANCE_BANDS[band], SURVEY_DISTANCE_BANDS[band + 1]);
        };
        this.surveySides.push(viewer.entities.add({
          id: `${id}_survey_cone_${i}${band ? `_band_${band}` : ""}`,
          polygon: {
            show: new Cesium.CallbackProperty(() => geometry().length > 0, false),
            hierarchy: new Cesium.CallbackProperty(() => new Cesium.PolygonHierarchy(geometry()), false),
            perPositionHeight: true,
            material: new Cesium.ColorMaterialProperty(new Cesium.CallbackProperty(() => this.color.withAlpha(0.12 * surveyStrength(SURVEY_DISTANCE_BANDS[band] + 1)), false)),
          },
        }));
        }
      }
    }
  }

  private visualPosition(): Cesium.Cartesian3 {
    return this.replayEntity ? this.replayPosition : Cesium.Cartesian3.fromDegrees(this.position.longitude, this.position.latitude, this.position.altitude);
  }

  private visualOrientation(): Cesium.Quaternion {
    const position = this.visualPosition();
    return Cesium.Transforms.headingPitchRollQuaternion(position, new Cesium.HeadingPitchRoll(Math.PI / 2 + this.renderedHeading, 0, 0));
  }

  private smoothRenderedHeading(direction: Cesium.Cartesian3, position: Cesium.Cartesian3, rate = 9): void {
    // Blend by elapsed time so the turn rate does not change with frame rate.
    const amount = 1 - Math.exp(-rate * Math.max(0.001, Math.min(0.1, this.stepSeconds)));
    const local = Cesium.Matrix4.multiplyByPointAsVector(
      Cesium.Matrix4.inverseTransformation(Cesium.Transforms.eastNorthUpToFixedFrame(position), new Cesium.Matrix4()),
      direction,
      new Cesium.Cartesian3(),
    );
    if (Math.hypot(local.x, local.y) <= 0.0001) return;
    const target = Math.atan2(local.x, local.y);
    const delta = Math.atan2(Math.sin(target - this.renderedHeading), Math.cos(target - this.renderedHeading));
    this.renderedHeading = Cesium.Math.negativePiToPi(this.renderedHeading + delta * amount);
    this.manualHeading = this.renderedHeading;
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

  private showAvoidance(success: boolean, at = this.visualPosition()): void {
    this.crashPosition = Cesium.Cartesian3.clone(at);
    this.avoidanceText = success ? "↗ AUTO-AVOIDING" : "⚠ NO SAFE ROUTE";
    this.avoidanceColor = success ? Cesium.Color.ORANGE : Cesium.Color.ORANGERED;
    this.crashUntil = Date.now() + (success ? 1200 : 4000);
  }

  run(command: MissionCommand): void {
    this.guard.clear();
    this.collisionBlocked = false;
    this.avoidanceActive = false;
    this.hideReplay();
    this.mission = command;
    this.stepIndex = 0;
    this.stepElapsed = 0;
    this.orbitCenter = null;
    this.orbitStartAngle = 0;
    this.missionSpeedMps = 0;
    this.missionWaypoint = null;
    this.headingTurn = null;
    this.gestureMotion = null;
    this.state = "TAKING_OFF";
    if (this.trailPoints.length === 0) {
      const origin = Cesium.Cartesian3.fromDegrees(this.position.longitude, this.position.latitude, this.position.altitude);
      this.originEntity.position = new Cesium.ConstantPositionProperty(origin);
      this.originEntity.show = true;
      this.trailPoints = [origin, Cesium.Cartesian3.clone(origin)];
    }
    this.trailEntity.show = false;
  }

  setManualControl(enabled: boolean): void {
    this.guard.clear();
    if (enabled) this.hideReplay();
    if (!enabled && this.state === "MANUAL") {
      this.preserveTrailEndpoint = true;
      const number = this.releases.length + 1;
      this.releases.push(this.viewer.entities.add({
        id: `${this.id}_release_${number}`,
        position: Cesium.Cartesian3.fromDegrees(this.position.longitude, this.position.latitude, this.position.altitude),
        label: { text: `RELEASE ${number}`, font: "600 12px system-ui", fillColor: this.color, style: Cesium.LabelStyle.FILL_AND_OUTLINE, outlineColor: Cesium.Color.BLACK, outlineWidth: 3 },
      }));
    }
    if (enabled && this.trailPoints.length === 0) {
      const origin = Cesium.Cartesian3.fromDegrees(this.position.longitude, this.position.latitude, this.position.altitude);
      this.originEntity.position = new Cesium.ConstantPositionProperty(origin);
      this.originEntity.orientation = new Cesium.ConstantProperty(this.orientationAt(this.position));
      this.originEntity.show = true;
      this.trailPoints = [origin, Cesium.Cartesian3.clone(origin)];
      this.trailEntity.show = false;
    }
    this.stopManualMotion();
    this.mission = null;
    this.stepIndex = 0;
    this.stepElapsed = 0;
    this.orbitCenter = null;
    this.orbitStartAngle = 0;
    this.missionSpeedMps = 0;
    this.missionWaypoint = null;
    this.headingTurn = null;
    this.gestureMotion = null;
    this.state = enabled ? "MANUAL" : "HOVERING";
  }

  stopManualMotion(): void {
    this.manualVelocity = new Cesium.Cartesian3();
  }

  get heading(): number { return this.manualHeading; }
  startGestureMotion(east: number, north: number, up: number, speedMps: number): void {
    const magnitude = Math.hypot(east, north, up);
    if (magnitude < 0.0001) return;
    const next = { east: east / magnitude, north: north / magnitude, up: up / magnitude, speed: Math.max(0.1, speedMps) };
    const sameDirection = this.gestureMotion
      && Math.hypot(this.gestureMotion.east - next.east, this.gestureMotion.north - next.north, this.gestureMotion.up - next.up) < 0.01;
    this.hideReplay();
    this.mission = null;
    this.headingTurn = null;
    this.missionSpeedMps = 0;
    this.missionWaypoint = null;
    if (!sameDirection) { this.stopManualMotion(); this.guard.clear(); }
    this.gestureMotion = next;
    this.state = "GESTURE_CONTROL";
  }

  rotateHeading(degrees = 90): void {
    if (!Number.isFinite(degrees) || degrees === 0) return;
    this.guard.clear();
    this.hideReplay();
    this.stopManualMotion();
    this.mission = null;
    this.stepIndex = 0;
    this.stepElapsed = 0;
    this.orbitCenter = null;
    this.missionSpeedMps = 0;
    this.missionWaypoint = null;
    this.gestureMotion = null;
    this.travelDirection = undefined;
    this.headingTurn = {
      from: this.manualHeading,
      delta: Cesium.Math.toRadians(degrees),
      elapsed: 0,
      duration: Math.max(0.45, Math.min(1.4, Math.abs(degrees) / 115)),
    };
    this.state = "TURNING";
  }
  stopCommand(): void {
    this.guard.clear();
    this.stopManualMotion();
    this.mission = null;
    this.stepIndex = 0;
    this.stepElapsed = 0;
    this.orbitCenter = null;
    this.missionSpeedMps = 0;
    this.missionWaypoint = null;
    this.headingTurn = null;
    this.gestureMotion = null;
    this.travelDirection = undefined;
    this.state = "HOVERING";
  }
  get colorHex(): string { return this.color.toCssHexString(); }
  setColor(hex: string): void {
    const color = Cesium.Color.fromCssColorString(hex);
    if (!color) throw new Error("Invalid drone color.");
    this.color = color;
    this.renderVersion++;
    for (const patch of this.coverage) {
      const alpha = patch.polygon!.material!.getValue(Cesium.JulianDate.now()).color.alpha;
      patch.polygon!.material = new Cesium.ColorMaterialProperty(color.withAlpha(alpha));
    }
    if (this.entity.model) this.entity.model.silhouetteColor = new Cesium.ConstantProperty(color);
    for (const item of [this.entity, this.originEntity, ...this.releases, ...(this.replayEntity ? [this.replayEntity] : [])]) {
      if (item.label) item.label.fillColor = new Cesium.ConstantProperty(color);
      if (item.box) {
        item.box.material = new Cesium.ColorMaterialProperty(color.withAlpha(item === this.originEntity ? 0.3 : 1));
        item.box.outlineColor = new Cesium.ConstantProperty(color);
      }
      if (item !== this.entity && item.polyline) item.polyline.material = new Cesium.PolylineArrowMaterialProperty(color);
    }
  }

  commandDestination(destination: Coordinates): void {
    this.guard.clear();
    this.hideReplay();
    this.stopManualMotion();
    this.mission = null;
    this.state = "NAVIGATING";
    const origin = Cesium.Cartesian3.fromDegrees(this.position.longitude, this.position.latitude, this.position.altitude);
    this.originEntity.position = new Cesium.ConstantPositionProperty(origin);
    this.originEntity.show = true;
    for (const marker of this.releases) this.viewer.entities.remove(marker);
    this.releases.length = 0;
    this.trailPoints = [origin, Cesium.Cartesian3.fromDegrees(destination.longitude, destination.latitude, destination.altitude)];
    this.trailEntity.show = false;
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
    for (const trace of this.motionTrace) this.viewer.entities.remove(trace);
    this.viewer.entities.remove(this.crashIndicator);
    for (const side of this.surveySides) this.viewer.entities.remove(side);
  }

  moveManually(east: number, north: number, up: number, seconds: number, heading = 0): void {
    const next = this.prepareManualMove(east, north, up, seconds, heading);
    if (next) this.applyManualMove(next);
  }

  prepareManualMove(east: number, north: number, up: number, seconds: number, heading = 0): Coordinates | undefined {
    if (this.state !== "MANUAL") return;
    this.manualHeading = this.renderedHeading = heading;
    const scale = this.configuredSpeedMph * METERS_PER_SECOND_PER_MPH / Math.max(1, Math.hypot(east, north, up));
    const target = new Cesium.Cartesian3(east * scale, north * scale, up * scale);
    const damping = target.equals(Cesium.Cartesian3.ZERO) ? 16 : 10;
    Cesium.Cartesian3.lerp(this.manualVelocity, target, 1 - Math.exp(-damping * seconds), this.manualVelocity);
    const next = destinationPoint(this.position, this.manualVelocity.x * seconds, this.manualVelocity.y * seconds);
    next.altitude += this.manualVelocity.z * seconds;
    this.stepSeconds = seconds;
    const resolved = this.resolveMove(this.position, next, seconds);
    this.collisionBlocked = !resolved;
    this.avoidanceActive = resolved?.detoured ?? false;
    if (!resolved) { this.showAvoidance(false); this.stopManualMotion(); return; }
    if (resolved.detoured) this.showAvoidance(true, resolved.cartesian);
    return resolved.coordinates;
  }

  applyManualMove(next: Coordinates): void {
    this.position = next;
    this.syncEntity();
  }

  private resolveMove(from: Coordinates, to: Coordinates, seconds: number): { coordinates: Coordinates; cartesian: Cesium.Cartesian3; detoured: boolean } | undefined {
    const start = Cesium.Cartesian3.fromDegrees(from.longitude, from.latitude, from.altitude);
    const requested = Cesium.Cartesian3.fromDegrees(to.longitude, to.latitude, to.altitude);
    const result = this.guard.move(this.viewer, start, requested, seconds);
    if (!result) return undefined;
    if (!result.detoured) return { coordinates: { ...to }, cartesian: requested, detoured: false };
    const geographic = Cesium.Cartographic.fromCartesian(result.position);
    return {
      coordinates: {
        latitude: Cesium.Math.toDegrees(geographic.latitude),
        longitude: Cesium.Math.toDegrees(geographic.longitude),
        altitude: geographic.height,
      },
      cartesian: result.position,
      detoured: result.detoured,
    };
  }

  reset(): void {
    this.guard.clear();
    this.replayGuard.clear();
    for (const patch of this.coverage) this.viewer.entities.remove(patch);
    this.coverage = []; this.lastCoveragePosition = undefined; this.coverageNumber = 0;
    this.surveyEnds = []; this.surveyCoveragePending = false; this.surveySampleTime = -Infinity;
    this.crashUntil = 0;
    this.collisionBlocked = this.replayBlocked = this.avoidanceActive = false;
    this.replayComplete = false;
    this.hideReplay();
    this.lastRenderedPosition = undefined;
    this.travelDirection = undefined;
    for (const marker of this.releases) this.viewer.entities.remove(marker);
    this.releases.length = 0;
    this.trailPoints = [];
    this.traceSamples = [];
    this.traceSequence = 0;
    this.originEntity.show = false;
    this.trailEntity.show = false;
    this.position = { ...this.home };
    this.mission = null;
    this.stepIndex = 0;
    this.stepElapsed = 0;
    this.orbitCenter = null;
    this.orbitStartAngle = 0;
    this.missionSpeedMps = 0;
    this.missionWaypoint = null;
    this.headingTurn = null;
    this.gestureMotion = null;
    this.manualHeading = this.renderedHeading = 0;
    this.state = "IDLE";
    this.syncEntity();
  }

  update(deltaSeconds: number): void {
    if (this.headingTurn) {
      this.headingTurn.elapsed += Math.min(deltaSeconds, 0.1);
      const linear = Math.min(1, this.headingTurn.elapsed / this.headingTurn.duration);
      const eased = linear * linear * (3 - 2 * linear);
      this.manualHeading = this.headingTurn.from + this.headingTurn.delta * eased;
      this.renderedHeading = this.manualHeading;
      this.renderVersion++;
      if (linear >= 1) {
        this.manualHeading = Cesium.Math.negativePiToPi(this.manualHeading);
        this.renderedHeading = this.manualHeading;
        this.headingTurn = null;
        this.state = "HOVERING";
      }
      return;
    }
    if (this.gestureMotion) {
      const seconds = Math.min(deltaSeconds, 0.1);
      this.stepSeconds = seconds;
      const motion = this.gestureMotion;
      const target = new Cesium.Cartesian3(motion.east * motion.speed, motion.north * motion.speed, motion.up * motion.speed);
      Cesium.Cartesian3.lerp(this.manualVelocity, target, 1 - Math.exp(-7 * seconds), this.manualVelocity);
      const next = destinationPoint(this.position, this.manualVelocity.x * seconds, this.manualVelocity.y * seconds);
      next.altitude += this.manualVelocity.z * seconds;
      const resolved = this.resolveMove(this.position, next, seconds);
      this.collisionBlocked = !resolved;
      this.avoidanceActive = resolved?.detoured ?? false;
      if (!resolved) {
        this.showAvoidance(false);
        this.stopCommand();
        this.state = "BLOCKED";
      } else {
        this.position = resolved.coordinates;
        if (resolved.detoured) this.showAvoidance(true, resolved.cartesian);
        this.syncEntity();
      }
      return;
    }
    if (!this.mission || this.stepIndex >= this.mission.mission.length) return;
    const step = this.mission.mission[this.stepIndex];
    this.stepSeconds = Math.min(deltaSeconds, 0.1);
    this.updateStep(step, this.stepSeconds);
  }

  snapshot(): DroneSnapshot {
    return { ...this.position, state: this.state, currentStep: this.mission ? this.stepIndex + 1 : 0, totalSteps: this.mission?.mission.length ?? 0 };
  }

  beginReplay(): number | null {
    this.guard.clear();
    this.replayGuard.clear();
    this.replayBlocked = false;
    this.replayComplete = false;
    this.collisionBlocked = false;
    this.avoidanceActive = false;
    this.replayDistance = 0;
    this.hideReplay();
    this.stopManualMotion();
    this.mission = null;
    this.gestureMotion = null;
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
      orientation: new Cesium.CallbackProperty(() => this.visualOrientation(), false),
      model: {
        uri: MODEL_URI,
        scale: 1.2,
        minimumPixelSize: 42,
        maximumScale: 30,
        silhouetteColor: this.color,
        silhouetteSize: 0.65,
        runAnimations: true,
        clampAnimations: false,
        shadows: Cesium.ShadowMode.DISABLED,
      },
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
    let detoured = false;
    const seconds = Math.max(0, distance - this.replayDistance) / Math.max(0.001, this.replaySpeed);
    this.stepSeconds = Math.min(0.1, Math.max(1 / 240, seconds));
    for (const point of [...crossed, next]) {
      const resolved = this.replayGuard.move(this.viewer, previous, point, seconds);
      if (!resolved) {
        this.showAvoidance(false);
        this.replayBlocked = this.collisionBlocked = true;
        this.state = "BLOCKED";
        return;
      }
      if (resolved.detoured) {
        detoured = true;
        this.showAvoidance(true, resolved.position);
      }
      previous = resolved.position;
    }
    if (Cesium.Cartesian3.distance(this.replayPosition, previous) > 0.001) {
      this.travelDirection = Cesium.Cartesian3.normalize(Cesium.Cartesian3.subtract(previous, this.replayPosition, new Cesium.Cartesian3()), new Cesium.Cartesian3());
      this.smoothRenderedHeading(this.travelDirection, this.replayPosition);
      this.recordMotionTrace(this.replayPosition, previous);
    }
    this.replayPosition = previous;
    this.renderVersion++;
    this.avoidanceActive = detoured;
    this.collisionBlocked = false;
    if (distance > this.replayDistance) this.recordCoverage();
    this.replayDistance = distance;
    this.replayComplete = distance >= length && !detoured && Cesium.Cartesian3.distance(previous, this.replayPoints.at(-1)!) < 0.01;
    if (moveDrone) {
      const geographic = Cesium.Cartographic.fromCartesian(this.replayPosition);
      this.position = { latitude: Cesium.Math.toDegrees(geographic.latitude), longitude: Cesium.Math.toDegrees(geographic.longitude), altitude: geographic.height };
      this.lastRenderedPosition = Cesium.Cartesian3.clone(this.replayPosition);
      this.state = this.replayComplete ? "HOVERING" : detoured ? "AVOIDING" : "NAVIGATING";
    }
  }

  hideReplay(): void {
    if (this.replayEntity) this.viewer.entities.remove(this.replayEntity);
    this.replayEntity = undefined;
    this.entity.show = true;
  }

  private updateStep(step: MissionStep, deltaSeconds: number): void {
    const previous = { ...this.position };
    let requested = previous;
    let completeStep = false;
    if (step.action === "goto" || step.action === "return_home") {
      const target = step.action === "goto" ? step : this.home;
      if (this.missionWaypoint && distanceMeters(this.position, this.missionWaypoint) < 0.35) this.missionWaypoint = null;
      const navigationTarget = this.missionWaypoint ?? target;
      const speed = step.speed_mps ?? this.configuredSpeedMph * METERS_PER_SECOND_PER_MPH;
      const distance = distanceMeters(this.position, navigationTarget);
      const motion = plannedFlightStep(this.missionSpeedMps, speed, distance, deltaSeconds);
      this.missionSpeedMps = motion.speed;
      const fraction = distance === 0 ? 1 : Math.min(1, motion.distance / distance);
      requested = interpolate(this.position, navigationTarget, fraction);
      this.state = this.missionWaypoint ? "AVOIDING" : step.action === "goto" ? "NAVIGATING" : "RETURNING_HOME";
      completeStep = !this.missionWaypoint && fraction === 1;
    } else if (step.action === "hover") {
      this.state = "HOVERING";
      this.stepElapsed += deltaSeconds;
      if (this.stepElapsed >= step.duration_s) this.advance();
    } else {
      this.state = "ORBITING";
      if (!this.orbitCenter) {
        this.orbitCenter = step.center ? { ...step.center } : { ...this.position };
        const center = Cesium.Cartesian3.fromDegrees(this.orbitCenter.longitude, this.orbitCenter.latitude, this.orbitCenter.altitude);
        const local = Cesium.Matrix4.multiplyByPoint(
          Cesium.Matrix4.inverseTransformation(Cesium.Transforms.eastNorthUpToFixedFrame(center), new Cesium.Matrix4()),
          Cesium.Cartesian3.fromDegrees(this.position.longitude, this.position.latitude, this.position.altitude),
          new Cesium.Cartesian3(),
        );
        this.orbitStartAngle = Math.hypot(local.x, local.y) > 1 ? Math.atan2(local.y, local.x) : 0;
      }
      this.stepElapsed += deltaSeconds;
      const direction = step.clockwise === false ? -1 : 1;
      const cruise = Math.max(16, this.configuredSpeedMph * METERS_PER_SECOND_PER_MPH);
      const angularSpeed = Math.min(0.8, Math.max(0.12, cruise / step.radius_m));
      const angle = this.orbitStartAngle + direction * this.stepElapsed * angularSpeed;
      const ideal = destinationPoint(this.orbitCenter, Math.cos(angle) * step.radius_m, Math.sin(angle) * step.radius_m);
      const distance = distanceMeters(this.position, ideal);
      const motion = plannedFlightStep(this.missionSpeedMps, cruise, distance, deltaSeconds);
      this.missionSpeedMps = motion.speed;
      requested = interpolate(this.position, ideal, distance === 0 ? 1 : Math.min(1, motion.distance / distance));
      completeStep = this.stepElapsed >= step.duration_s;
    }
    if (step.action === "hover") { this.syncEntity(); return; }
    const resolved = this.resolveMove(previous, requested, deltaSeconds);
    this.collisionBlocked = !resolved;
    this.avoidanceActive = resolved?.detoured ?? false;
    if (!resolved) {
      this.position = previous;
      this.showAvoidance(false);
      this.mission = null;
      this.missionSpeedMps = 0;
      this.missionWaypoint = null;
      this.state = "BLOCKED";
      this.stopManualMotion();
    } else {
      this.position = resolved.coordinates;
      if (resolved.detoured) {
        this.state = "AVOIDING";
        this.showAvoidance(true, resolved.cartesian);
        const start = Cesium.Cartesian3.fromDegrees(previous.longitude, previous.latitude, previous.altitude);
        const detour = Cesium.Cartesian3.subtract(resolved.cartesian, start, new Cesium.Cartesian3());
        if ((step.action === "goto" || step.action === "return_home") && Cesium.Cartesian3.magnitude(detour) > 0.001 && !this.missionWaypoint) {
          Cesium.Cartesian3.multiplyByScalar(Cesium.Cartesian3.normalize(detour, detour), 12, detour);
          const waypoint = Cesium.Cartographic.fromCartesian(Cesium.Cartesian3.add(start, detour, detour));
          this.missionWaypoint = {
            latitude: Cesium.Math.toDegrees(waypoint.latitude),
            longitude: Cesium.Math.toDegrees(waypoint.longitude),
            altitude: waypoint.height,
          };
        }
      } else if (this.missionWaypoint && distanceMeters(this.position, this.missionWaypoint) < 0.35) {
        this.missionWaypoint = null;
        this.missionSpeedMps *= 0.65;
      } else if (completeStep) {
        this.advance();
      }
    }
    this.syncEntity();
  }

  private advance(): void {
    this.stepIndex += 1;
    this.stepElapsed = 0;
    this.orbitCenter = null;
    this.orbitStartAngle = 0;
    this.missionSpeedMps = 0;
    this.missionWaypoint = null;
    if (this.mission && this.stepIndex >= this.mission.mission.length) this.state = "IDLE";
  }

  private syncEntity(): void {
    this.renderVersion++;
    if (this.state === "MANUAL" || this.mission) this.recordCoverage();
    const current = Cesium.Cartesian3.fromDegrees(this.position.longitude, this.position.latitude, this.position.altitude);
    if (this.lastRenderedPosition && Cesium.Cartesian3.distance(current, this.lastRenderedPosition) > 0.001) {
      this.travelDirection = Cesium.Cartesian3.normalize(Cesium.Cartesian3.subtract(current, this.lastRenderedPosition, new Cesium.Cartesian3()), new Cesium.Cartesian3());
      this.smoothRenderedHeading(this.travelDirection, current);
      this.recordMotionTrace(this.lastRenderedPosition, current);
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
    }
  }

  private recordMotionTrace(from: Cesium.Cartesian3, to: Cesium.Cartesian3): void {
    const now = performance.now();
    this.traceSamples = this.traceSamples.filter(sample => now - sample.time < MOTION_TRACE_LIFETIME_MS);
    if (!this.traceSamples.length) this.traceSamples.push({ position: this.sprayParticle(from), time: now - 45 });
    const previous = this.traceSamples.at(-1)!;
    if (now - previous.time < 38 && Cesium.Cartesian3.distance(previous.position, to) < 0.65) return;
    this.traceSamples.push({ position: this.sprayParticle(to), time: now });
    if (this.traceSamples.length > 72) this.traceSamples.shift();
  }

  private sprayParticle(position: Cesium.Cartesian3): Cesium.Cartesian3 {
    const sequence = this.traceSequence++;
    const spread = 0.34;
    const local = new Cesium.Cartesian3(
      Math.sin(sequence * 2.31) * spread,
      Math.cos(sequence * 1.73) * spread,
      Math.sin(sequence * 0.91) * spread * 0.55,
    );
    return Cesium.Matrix4.multiplyByPoint(Cesium.Transforms.eastNorthUpToFixedFrame(position), local, new Cesium.Cartesian3());
  }

  private tracePoint(index: number): Cesium.Cartesian3 {
    const end = this.traceSamples.length - 1 - index;
    return end < 0 ? this.visualPosition() : this.traceSamples[end].position;
  }

  private traceAlpha(index: number): number {
    const end = this.traceSamples.length - 1 - index;
    if (end < 0) return 0;
    return motionTraceAlpha(performance.now() - this.traceSamples[end].time);
  }


  private orientationAt(position: Coordinates): Cesium.Quaternion {
    const cartesian = Cesium.Cartesian3.fromDegrees(position.longitude, position.latitude, position.altitude);
    return Cesium.Transforms.headingPitchRollQuaternion(cartesian, new Cesium.HeadingPitchRoll(Cesium.Math.toRadians(90) + (this.state === "MANUAL" ? this.manualHeading : 0), 0, 0));
  }
}
