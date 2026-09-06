import * as Cesium from "cesium";
import type { GeoFrame } from "../frame";
import { operatorIcon } from "../icons";
import { shortId } from "../taxonomy";
import type { RuntimeOperator, RuntimeSnapshot } from "../types";

/**
 * The people on the ground.
 *
 * Each phone in the UWB room becomes one marker standing on terrain, with a
 * facing arrow and, when that person is the nearest operator to a drone, a
 * leader line drawn to every drone currently listening to them. The line is the
 * point of this layer: "who is flying what" is otherwise invisible, and it is
 * the one thing an observer needs when control silently hands off as a drone
 * crosses between two people.
 *
 * Positions arrive already placed in scene metres by the backend
 * (`simulation/operators.py`), so this layer does no frame maths beyond the
 * shared ENU conversion every other runtime layer uses.
 */

export type OperatorOptions = { show: boolean; showControlLinks: boolean; humanModels: boolean };

const ACTIVE = "#f2b134";      // commanding at least one drone
const IDLE = "#8fa6bd";        // present, not currently in charge
const ANCHOR_RING = "#4fd1c5"; // the phone the group frame is pinned to

const FACING_METRES = 6;
const STALE_SECONDS = 1.5;     // marker dims once a phone stops reporting

type Visual = {
  id: string;
  position: Cesium.Cartesian3;
  ground: { x: number; y: number; z: number };
  heading: number;
  facing: Cesium.Cartesian3[];
  links: Cesium.Cartesian3[];
  label: string;
  color: string;
  commanding: boolean;
  isAnchor: boolean;
  stale: boolean;
  visible: boolean;
};

export class OperatorLayer {
  private readonly visuals = new Map<string, Visual>();
  private readonly entities = new Map<string, Cesium.Entity[]>();
  private options: OperatorOptions = { show: true, showControlLinks: true, humanModels: false };

  constructor(
    private readonly viewer: Cesium.Viewer,
    private readonly frame: GeoFrame,
    private readonly dronePosition: (nodeId: string) => Cesium.Cartesian3 | undefined,
  ) {}

  setOptions(options: Partial<OperatorOptions>): void {
    this.options = { ...this.options, ...options };
  }

  positionOf(operatorId: string): Cesium.Cartesian3 | undefined {
    return this.visuals.get(operatorId)?.position;
  }

  operatorIdFromEntity(entityId: string): string | null {
    const match = /^runtime-(?:operator|facing|control)-(.+)$/.exec(entityId);
    return match ? match[1] : null;
  }

  update(snapshot: Pick<RuntimeSnapshot, "operators">): void {
    const seen = new Set<string>();
    for (const operator of snapshot.operators ?? []) {
      if (!Number.isFinite(operator.position.x) || !Number.isFinite(operator.position.y)) continue;
      seen.add(operator.operator_id);
      let visual = this.visuals.get(operator.operator_id);
      if (!visual) {
        visual = {
          id: operator.operator_id,
          position: new Cesium.Cartesian3(),
          ground: { x: 0, y: 0, z: 0 }, heading: 0,
          facing: [],
          links: [],
          label: "",
          color: IDLE,
          commanding: false,
          isAnchor: false,
          stale: false,
          visible: true,
        };
        this.visuals.set(operator.operator_id, visual);
        this.entities.set(operator.operator_id, this.createEntities(visual));
      }
      this.updateOne(visual, operator);
    }

    for (const [id, entities] of this.entities) {
      if (seen.has(id)) continue;
      for (const entity of entities) this.viewer.entities.remove(entity);
      this.entities.delete(id);
      this.visuals.delete(id);
    }
  }

  private updateOne(visual: Visual, operator: RuntimeOperator): void {
    visual.ground = { ...operator.position };
    visual.heading = operator.heading;
    // Lift the marker slightly so it sits on the lawn rather than inside it.
    this.frame.toFixedAt(operator.position, operator.position.z + 1.7, visual.position);

    visual.commanding = operator.controls.length > 0;
    visual.isAnchor = operator.is_anchor;
    visual.stale = operator.age > STALE_SECONDS;
    visual.color = visual.commanding ? ACTIVE : IDLE;
    visual.visible = this.options.show;

    // A short arrow on the ground: which way this person is actually facing is
    // what makes a "forward" dash predictable to anyone watching the map.
    const heading = operator.heading;
    visual.facing = [
      this.frame.toFixedAt(operator.position, operator.position.z + 0.3),
      this.frame.toFixedAt({
        x: operator.position.x + (this.options.humanModels ? 1.2 : FACING_METRES) * Math.cos(heading),
        y: operator.position.y + (this.options.humanModels ? 1.2 : FACING_METRES) * Math.sin(heading),
        z: operator.position.z,
      }, operator.position.z + 0.3),
    ];

    visual.links = [];
    if (this.options.showControlLinks) {
      for (const nodeId of operator.controls) {
        const drone = this.dronePosition(nodeId);
        if (!drone) continue;
        visual.links.push(visual.position, drone);
      }
    }

    const gesture = operator.gesture === "None" ? "--" : operator.gesture;
    const action = operator.action ? `  ${operator.action.toUpperCase()}` : "";
    const commanding = operator.controls.length
      ? operator.controls.map(shortId).join(" ")
      : "observing";
    visual.label = [
      `${operator.name}${operator.is_anchor ? "  [ANCHOR]" : ""}`,
      `${gesture}${action}`,
      visual.stale ? "SIGNAL LOST" : commanding,
    ].join("\n");
  }

  private createEntities(visual: Visual): Cesium.Entity[] {
    const options = () => this.options;
    const alpha = () => (visual.stale ? 0.35 : 1);

    const marker = this.viewer.entities.add({
      id: `runtime-operator-${visual.id}`,
      name: visual.id,
      position: new Cesium.CallbackPositionProperty(() => visual.position, false),
      billboard: {
        image: new Cesium.CallbackProperty(() => operatorIcon(visual.color, visual.commanding), false),
        scale: 0.5,
        color: new Cesium.CallbackProperty(() => Cesium.Color.WHITE.withAlpha(alpha()), false),
        disableDepthTestDistance: Number.POSITIVE_INFINITY,
        show: new Cesium.CallbackProperty(() => visual.visible && !options().humanModels, false),
      },
      label: {
        text: new Cesium.CallbackProperty(() => visual.label, false),
        font: "600 12px ui-monospace, SFMono-Regular, Menlo, monospace",
        fillColor: new Cesium.CallbackProperty(
          () => Cesium.Color.fromCssColorString(visual.isAnchor ? ANCHOR_RING : visual.color).withAlpha(alpha()),
          false,
        ),
        showBackground: true,
        backgroundColor: Cesium.Color.fromCssColorString("#070c11").withAlpha(0.78),
        backgroundPadding: new Cesium.Cartesian2(8, 5),
        pixelOffset: new Cesium.CallbackProperty(() => new Cesium.Cartesian2(0, options().humanModels ? -12 : 20), false),
        verticalOrigin: new Cesium.CallbackProperty(() => options().humanModels ? Cesium.VerticalOrigin.BOTTOM : Cesium.VerticalOrigin.TOP, false),
        disableDepthTestDistance: Number.POSITIVE_INFINITY,
        translucencyByDistance: new Cesium.NearFarScalar(3000, 1, 14000, 0),
        show: new Cesium.CallbackProperty(() => visual.visible, false),
      },
    });

    const facing = this.viewer.entities.add({
      id: `runtime-facing-${visual.id}`,
      polyline: {
        positions: new Cesium.CallbackProperty(() => visual.facing, false),
        width: 3,
        material: new Cesium.ColorMaterialProperty(
          new Cesium.CallbackProperty(
            () => Cesium.Color.fromCssColorString(visual.color).withAlpha(0.75 * alpha()), false,
          ),
        ),
        clampToGround: new Cesium.CallbackProperty(() => !options().humanModels, false),
        show: new Cesium.CallbackProperty(() => visual.visible, false),
      },
    });

    const control = this.viewer.entities.add({
      id: `runtime-control-${visual.id}`,
      polyline: {
        positions: new Cesium.CallbackProperty(() => visual.links, false),
        width: 1.6,
        material: new Cesium.PolylineDashMaterialProperty({
          color: new Cesium.CallbackProperty(
            () => Cesium.Color.fromCssColorString(ACTIVE).withAlpha(0.55 * alpha()), false,
          ),
          dashLength: 14,
        }),
        show: new Cesium.CallbackProperty(
          () => visual.visible && options().showControlLinks && visual.links.length > 0, false,
        ),
      },
    });

    // Metre-sized 3D figures: head, torso, arms and legs, aligned with the phone heading.
    const parts = [
      { name: "head", side: 0, z: 1.57, radii: [0.16, 0.16, 0.18], skin: true },
      { name: "body", side: 0, z: 1.12, radii: [0.16, 0.25, 0.34], skin: false },
      { name: "arm-left", side: -0.31, z: 1.08, radii: [0.09, 0.09, 0.32], skin: false },
      { name: "arm-right", side: 0.31, z: 1.08, radii: [0.09, 0.09, 0.32], skin: false },
      { name: "leg-left", side: -0.13, z: 0.43, radii: [0.105, 0.105, 0.43], skin: false },
      { name: "leg-right", side: 0.13, z: 0.43, radii: [0.105, 0.105, 0.43], skin: false },
    ];
    const humans = parts.map(part => this.viewer.entities.add({
      id: `runtime-human-${visual.id}-${part.name}`,
      position: new Cesium.CallbackPositionProperty(() => this.frame.toFixed({
        x: visual.ground.x - Math.sin(visual.heading) * part.side,
        y: visual.ground.y + Math.cos(visual.heading) * part.side,
        z: visual.ground.z + part.z,
      }), false),
      orientation: new Cesium.CallbackProperty(() => Cesium.Transforms.headingPitchRollQuaternion(
        this.frame.toFixed(visual.ground), new Cesium.HeadingPitchRoll(-visual.heading, 0, 0)), false),
      ellipsoid: {
        radii: new Cesium.Cartesian3(...part.radii),
        material: new Cesium.ColorMaterialProperty(new Cesium.CallbackProperty(() =>
          Cesium.Color.fromCssColorString(part.skin ? "#dfb99a" : visual.color).withAlpha(alpha()), false)),
        stackPartitions: 12, slicePartitions: 12,
        show: new Cesium.CallbackProperty(() => visual.visible && options().humanModels, false),
      },
    }));
    return [marker, facing, control, ...humans];
  }

  clear(): void {
    for (const entities of this.entities.values()) {
      for (const entity of entities) this.viewer.entities.remove(entity);
    }
    this.entities.clear();
    this.visuals.clear();
  }
}
