import * as Cesium from "cesium";
import type { GeoFrame } from "../frame";
import { aircraftIcon, lastKnownIcon } from "../icons";
import type { PresentedDrone } from "../interpolator";
import { HEALTH_STYLES, ROLE_STYLES, deriveHealth, deriveRole, shortId, type HealthKey, type RoleKey } from "../taxonomy";
import type { RuntimeDrone, RuntimeSnapshot, RuntimeTask } from "../types";

/**
 * Aircraft rendering: model, far-zoom icon, label, altitude cues, trail,
 * planned route, localization estimate and last-known marker.
 *
 * Entities are created once per drone and then driven by callback properties
 * reading mutable records, so a frame update touches plain objects instead of
 * rebuilding Cesium properties.
 */

const MODEL_URI = `${import.meta.env.BASE_URL ?? "/"}models/uav.glb`.replace(/\/{2,}/g, "/");
const TRAIL_LIMIT = 320;
const TRAIL_MIN_SPACING_M = 2.5;
const MODEL_FAR_M = 2600;

export type DroneVisualOptions = {
  showTrails: boolean;
  showPlans: boolean;
  showAltitudeCues: boolean;
  showEstimates: boolean;
  labelDetail: "minimal" | "standard";
};

type Visual = {
  id: string;
  role: RoleKey;
  health: HealthKey;
  selected: boolean;
  position: Cesium.Cartesian3;
  ground: Cesium.Cartesian3;
  estimate: Cesium.Cartesian3;
  forward: Cesium.Cartesian3;
  orientation: Cesium.Quaternion;
  stem: Cesium.Cartesian3[];
  trail: Cesium.Cartesian3[];
  plan: Cesium.Cartesian3[];
  planPoints: Cesium.Cartesian3[];
  label: string;
  uncertainty: number;
  altitude: number;
  online: boolean;
  lastKnown: Cesium.Cartesian3;
  hasLastKnown: boolean;
  color: Cesium.Color;
  planColor: Cesium.Color;
  planDash: number;
  trailColor: Cesium.Color;
};

const cartesian = () => new Cesium.Cartesian3();
const scratchHpr = new Cesium.HeadingPitchRoll();

export class DroneLayer {
  private readonly visuals = new Map<string, Visual>();
  private readonly entities = new Map<string, Cesium.Entity[]>();
  private options: DroneVisualOptions = {
    showTrails: true,
    showPlans: true,
    showAltitudeCues: true,
    showEstimates: false,
    labelDetail: "standard",
  };
  private selectedId: string | null = null;

  constructor(private readonly viewer: Cesium.Viewer, private readonly frame: GeoFrame) {}

  setOptions(options: Partial<DroneVisualOptions>): void {
    this.options = { ...this.options, ...options };
    for (const visual of this.visuals.values()) this.applyLabel(visual);
  }

  setSelected(id: string | null): void {
    this.selectedId = id;
    for (const visual of this.visuals.values()) {
      visual.selected = visual.id === id;
      this.applyLabel(visual);
    }
  }

  /** ECEF position of a drone, for the camera and inspector. */
  positionOf(id: string): Cesium.Cartesian3 | undefined {
    return this.visuals.get(id)?.position;
  }

  forwardOf(id: string): Cesium.Cartesian3 | undefined {
    return this.visuals.get(id)?.forward;
  }

  entityIdFor(id: string): string {
    return `runtime-drone-${id}`;
  }

  /** Maps any entity id owned by this layer back to a drone id. */
  droneIdFromEntity(entityId: string): string | null {
    const match = /^runtime-(?:drone|stem|shadow|trail|plan|estimate|lastknown)-(.+)$/.exec(entityId);
    return match ? match[1] : null;
  }

  update(snapshot: RuntimeSnapshot, presented: Map<string, PresentedDrone>, taskById: Map<string, RuntimeTask>): void {
    const seen = new Set<string>();
    for (const drone of snapshot.drones) {
      const id = drone.identity.node_id;
      seen.add(id);
      const state = presented.get(id);
      if (!state) continue;
      this.updateOne(drone, state, taskById);
    }
    for (const [id, entities] of this.entities) {
      if (seen.has(id)) continue;
      for (const entity of entities) this.viewer.entities.remove(entity);
      this.entities.delete(id);
      this.visuals.delete(id);
    }
  }

  private updateOne(drone: RuntimeDrone, state: PresentedDrone, taskById: Map<string, RuntimeTask>): void {
    const id = drone.identity.node_id;
    let visual = this.visuals.get(id);
    if (!visual) {
      visual = {
        id,
        role: "IDLE",
        health: "NOMINAL",
        selected: this.selectedId === id,
        position: cartesian(),
        ground: cartesian(),
        estimate: cartesian(),
        forward: cartesian(),
        orientation: new Cesium.Quaternion(),
        stem: [cartesian(), cartesian()],
        trail: [],
        plan: [],
        planPoints: [],
        label: id,
        uncertainty: 1.5,
        altitude: 0,
        online: true,
        lastKnown: cartesian(),
        hasLastKnown: false,
        color: Cesium.Color.WHITE.clone(),
        planColor: Cesium.Color.WHITE.clone(),
        planDash: 0xffff,
        trailColor: Cesium.Color.WHITE.clone(),
      };
      this.visuals.set(id, visual);
      this.entities.set(id, this.createEntities(visual));
    }

    const role = deriveRole(drone, taskById);
    const health = deriveHealth(drone);
    visual.role = role;
    visual.health = health;
    visual.online = drone.truth.online;
    visual.altitude = state.position.z;
    visual.uncertainty = Math.max(1, drone.estimated.position_uncertainty);

    const roleStyle = ROLE_STYLES[role];
    const base = Cesium.Color.fromCssColorString(roleStyle.color);
    visual.color = drone.truth.online ? base : Cesium.Color.fromCssColorString(HEALTH_STYLES[health].color);
    visual.planColor = base.withAlpha(0.75);
    visual.planDash = roleStyle.dash;
    visual.trailColor = base.withAlpha(0.9);

    this.frame.toFixed(state.position, visual.position);
    this.frame.toFixedAt(state.position, 0, visual.ground);
    this.frame.toFixed(state.estimatedPosition, visual.estimate);
    Cesium.Cartesian3.clone(visual.ground, visual.stem[0]);
    Cesium.Cartesian3.clone(visual.position, visual.stem[1]);

    // Forward unit vector in ECEF, used both for the model attitude and for the
    // billboard's aligned axis so the icon points where the aircraft flies.
    this.frame.toFixedVector({ x: Math.cos(state.heading), y: Math.sin(state.heading), z: 0 }, visual.forward);
    Cesium.Cartesian3.normalize(visual.forward, visual.forward);

    // Cesium measures heading clockwise from north; the backend measures it
    // counter-clockwise from east. The model's nose is on the local +X axis,
    // where positive pitch is nose-up and positive roll is a right bank.
    scratchHpr.heading = -state.heading;
    scratchHpr.pitch = state.pitch;
    scratchHpr.roll = state.bank;
    Cesium.Transforms.headingPitchRollQuaternion(
      visual.position, scratchHpr, Cesium.Ellipsoid.WGS84, undefined, visual.orientation,
    );

    this.updateTrail(visual, drone.truth.online);
    this.updatePlan(visual, drone);

    if (!drone.truth.online) {
      if (!visual.hasLastKnown) {
        Cesium.Cartesian3.clone(visual.position, visual.lastKnown);
        visual.hasLastKnown = true;
      }
    } else {
      visual.hasLastKnown = false;
    }

    this.applyLabel(visual);
    this.applyAppearance(visual, drone, state);
  }

  private updateTrail(visual: Visual, online: boolean): void {
    if (!online) return;
    const last = visual.trail[visual.trail.length - 1];
    if (!last || Cesium.Cartesian3.distance(last, visual.position) >= TRAIL_MIN_SPACING_M) {
      visual.trail.push(Cesium.Cartesian3.clone(visual.position, cartesian()));
      if (visual.trail.length > TRAIL_LIMIT) visual.trail.splice(0, visual.trail.length - TRAIL_LIMIT);
    } else {
      Cesium.Cartesian3.clone(visual.position, last);
    }
  }

  private updatePlan(visual: Visual, drone: RuntimeDrone): void {
    const waypoints = drone.current_plan;
    const needed = waypoints.length + 1;
    while (visual.plan.length < needed) visual.plan.push(cartesian());
    visual.plan.length = needed;
    Cesium.Cartesian3.clone(visual.position, visual.plan[0]);
    for (let index = 0; index < waypoints.length; index += 1) {
      this.frame.toFixed(waypoints[index], visual.plan[index + 1]);
    }
    while (visual.planPoints.length < waypoints.length) visual.planPoints.push(cartesian());
    visual.planPoints.length = waypoints.length;
    for (let index = 0; index < waypoints.length; index += 1) {
      Cesium.Cartesian3.clone(visual.plan[index + 1], visual.planPoints[index]);
    }
  }

  private applyLabel(visual: Visual): void {
    const roleStyle = ROLE_STYLES[visual.role];
    const health = HEALTH_STYLES[visual.health];
    const lines = [`${shortId(visual.id)}  ${roleStyle.badge}`];
    if (visual.health !== "NOMINAL") lines.push(health.label.toUpperCase());
    if (visual.selected && this.options.labelDetail === "standard") {
      lines.push(`${Math.round(visual.altitude)} m AGL`);
    }
    visual.label = lines.join("\n");
  }

  private applyAppearance(visual: Visual, drone: RuntimeDrone, state: PresentedDrone): void {
    const entity = this.viewer.entities.getById(this.entityIdFor(visual.id));
    if (!entity) return;
    const offline = !drone.truth.online;
    if (entity.model) {
      entity.model.color = new Cesium.ConstantProperty(visual.color.withAlpha(offline ? 0.35 : 1));
      entity.model.silhouetteColor = new Cesium.ConstantProperty(
        visual.selected ? Cesium.Color.WHITE : visual.color.withAlpha(0.9),
      );
      entity.model.silhouetteSize = new Cesium.ConstantProperty(visual.selected ? 3 : 1.5);
    }
    if (entity.billboard) {
      entity.billboard.image = new Cesium.ConstantProperty(
        aircraftIcon(visual.role, visual.health, visual.selected),
      );
    }
    void state;
  }

  private createEntities(visual: Visual): Cesium.Entity[] {
    const viewer = this.viewer;
    const options = () => this.options;
    const position = new Cesium.CallbackPositionProperty(() => visual.position, false);
    const orientation = new Cesium.CallbackProperty(() => visual.orientation, false);

    const aircraft = viewer.entities.add({
      id: this.entityIdFor(visual.id),
      name: visual.id,
      position,
      orientation,
      model: {
        uri: MODEL_URI,
        scale: 4.5,
        minimumPixelSize: 62,
        maximumScale: 260,
        silhouetteColor: Cesium.Color.WHITE,
        silhouetteSize: 1.5,
        colorBlendMode: Cesium.ColorBlendMode.MIX,
        colorBlendAmount: 0.62,
        distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, MODEL_FAR_M),
        shadows: Cesium.ShadowMode.DISABLED,
      },
      billboard: {
        image: aircraftIcon(visual.role, visual.health, visual.selected),
        // The icon is drawn nose-up, so aligning image-up with the flight
        // vector makes the icon point exactly where the aircraft is going.
        alignedAxis: new Cesium.CallbackProperty(() => visual.forward, false),
        scale: 0.62,
        scaleByDistance: new Cesium.NearFarScalar(200, 0.42, 9000, 0.95),
        translucencyByDistance: new Cesium.NearFarScalar(300, 0.75, 4000, 1),
        disableDepthTestDistance: Number.POSITIVE_INFINITY,
        verticalOrigin: Cesium.VerticalOrigin.CENTER,
      },
      label: {
        text: new Cesium.CallbackProperty(() => visual.label, false),
        font: "600 13px ui-monospace, SFMono-Regular, Menlo, monospace",
        fillColor: new Cesium.CallbackProperty(() => (visual.selected ? Cesium.Color.WHITE : visual.color), false),
        showBackground: true,
        backgroundColor: new Cesium.CallbackProperty(
          () => (visual.selected ? Cesium.Color.fromCssColorString("#0d1620").withAlpha(0.92) : Cesium.Color.fromCssColorString("#080d13").withAlpha(0.68)),
          false,
        ),
        backgroundPadding: new Cesium.Cartesian2(9, 6),
        pixelOffset: new Cesium.Cartesian2(0, -38),
        horizontalOrigin: Cesium.HorizontalOrigin.CENTER,
        verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
        translucencyByDistance: new Cesium.NearFarScalar(2500, 1, 14000, 0),
        scaleByDistance: new Cesium.NearFarScalar(400, 1, 6000, 0.72),
        disableDepthTestDistance: Number.POSITIVE_INFINITY,
        style: Cesium.LabelStyle.FILL,
      },
    });

    const stem = viewer.entities.add({
      id: `runtime-stem-${visual.id}`,
      polyline: {
        positions: new Cesium.CallbackProperty(() => visual.stem, false),
        width: 1.6,
        arcType: Cesium.ArcType.NONE,
        material: new Cesium.PolylineDashMaterialProperty({
          color: new Cesium.CallbackProperty(() => visual.color.withAlpha(visual.selected ? 0.85 : 0.4), false),
          dashLength: 10,
        }),
        show: new Cesium.CallbackProperty(() => options().showAltitudeCues && visual.online, false),
      },
    });

    const shadow = viewer.entities.add({
      id: `runtime-shadow-${visual.id}`,
      position: new Cesium.CallbackPositionProperty(() => visual.ground, false),
      ellipse: {
        semiMajorAxis: 7,
        semiMinorAxis: 7,
        material: new Cesium.ColorMaterialProperty(
          new Cesium.CallbackProperty(() => Cesium.Color.BLACK.withAlpha(visual.online ? 0.28 : 0.1), false),
        ),
        outline: true,
        outlineWidth: 1,
        outlineColor: new Cesium.CallbackProperty(() => visual.color.withAlpha(0.45), false),
        show: new Cesium.CallbackProperty(() => options().showAltitudeCues && visual.online, false),
      },
    });

    const trail = viewer.entities.add({
      id: `runtime-trail-${visual.id}`,
      polyline: {
        positions: new Cesium.CallbackProperty(() => visual.trail, false),
        width: 2.4,
        arcType: Cesium.ArcType.NONE,
        material: new Cesium.PolylineGlowMaterialProperty({
          color: new Cesium.CallbackProperty(() => visual.trailColor.withAlpha(visual.selected ? 0.95 : 0.55), false),
          glowPower: 0.22,
          taperPower: 0.35,
        }),
        show: new Cesium.CallbackProperty(() => options().showTrails && visual.trail.length > 1, false),
      },
    });

    const plan = viewer.entities.add({
      id: `runtime-plan-${visual.id}`,
      polyline: {
        positions: new Cesium.CallbackProperty(() => visual.plan, false),
        width: 2.2,
        arcType: Cesium.ArcType.NONE,
        material: new Cesium.PolylineDashMaterialProperty({
          color: new Cesium.CallbackProperty(() => visual.planColor.withAlpha(visual.selected ? 0.95 : 0.5), false),
          dashPattern: new Cesium.CallbackProperty(() => visual.planDash, false),
          dashLength: 22,
        }),
        show: new Cesium.CallbackProperty(() => options().showPlans && visual.online && visual.plan.length > 1, false),
      },
    });

    const estimate = viewer.entities.add({
      id: `runtime-estimate-${visual.id}`,
      position: new Cesium.CallbackPositionProperty(() => visual.estimate, false),
      point: {
        pixelSize: 8,
        color: Cesium.Color.fromCssColorString("#ffd166").withAlpha(0.95),
        outlineColor: Cesium.Color.fromCssColorString("#0b1219"),
        outlineWidth: 2,
        disableDepthTestDistance: Number.POSITIVE_INFINITY,
        show: new Cesium.CallbackProperty(() => visual.online && (options().showEstimates || visual.selected), false),
      },
      ellipse: {
        semiMajorAxis: new Cesium.CallbackProperty(() => visual.uncertainty, false),
        semiMinorAxis: new Cesium.CallbackProperty(() => visual.uncertainty, false),
        height: new Cesium.CallbackProperty(() => Math.max(0.5, visual.altitude - 0.5), false),
        material: new Cesium.ColorMaterialProperty(Cesium.Color.fromCssColorString("#ffd166").withAlpha(0.1)),
        outline: true,
        outlineColor: Cesium.Color.fromCssColorString("#ffd166").withAlpha(0.6),
        show: new Cesium.CallbackProperty(() => visual.online && (options().showEstimates || visual.selected), false),
      },
    });

    const lastKnown = viewer.entities.add({
      id: `runtime-lastknown-${visual.id}`,
      position: new Cesium.CallbackPositionProperty(() => visual.lastKnown, false),
      billboard: {
        image: lastKnownIcon(),
        scale: 0.72,
        disableDepthTestDistance: Number.POSITIVE_INFINITY,
        show: new Cesium.CallbackProperty(() => visual.hasLastKnown, false),
      },
      label: {
        text: new Cesium.CallbackProperty(() => `${shortId(visual.id)} LAST KNOWN`, false),
        font: "600 12px ui-monospace, SFMono-Regular, Menlo, monospace",
        fillColor: Cesium.Color.fromCssColorString("#ff8f8f"),
        showBackground: true,
        backgroundColor: Cesium.Color.fromCssColorString("#1a0d10").withAlpha(0.85),
        backgroundPadding: new Cesium.Cartesian2(8, 5),
        pixelOffset: new Cesium.Cartesian2(0, 26),
        disableDepthTestDistance: Number.POSITIVE_INFINITY,
        show: new Cesium.CallbackProperty(() => visual.hasLastKnown, false),
      },
    });

    return [aircraft, stem, shadow, trail, plan, estimate, lastKnown];
  }

  clearTrails(): void {
    for (const visual of this.visuals.values()) visual.trail.length = 0;
  }

  destroy(): void {
    for (const entities of this.entities.values()) {
      for (const entity of entities) this.viewer.entities.remove(entity);
    }
    this.entities.clear();
    this.visuals.clear();
  }
}
