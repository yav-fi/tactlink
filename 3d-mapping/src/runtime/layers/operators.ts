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

export type OperatorOptions = { show: boolean; showControlLinks: boolean };

const ACTIVE = "#f2b134";      // commanding at least one drone
const IDLE = "#8fa6bd";        // present, not currently in charge
const ANCHOR_RING = "#4fd1c5"; // the phone the group frame is pinned to

const FACING_METRES = 6;
const STALE_SECONDS = 1.5;     // marker dims once a phone stops reporting

type Visual = {
  id: string;
  position: Cesium.Cartesian3;
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
  private options: OperatorOptions = { show: true, showControlLinks: true };

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

  update(snapshot: RuntimeSnapshot): void {
    const seen = new Set<string>();
    for (const operator of snapshot.operators ?? []) {
      if (!Number.isFinite(operator.position.x) || !Number.isFinite(operator.position.y)) continue;
      seen.add(operator.operator_id);
      let visual = this.visuals.get(operator.operator_id);
      if (!visual) {
        visual = {
          id: operator.operator_id,
          position: new Cesium.Cartesian3(),
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
        x: operator.position.x + FACING_METRES * Math.cos(heading),
        y: operator.position.y + FACING_METRES * Math.sin(heading),
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
        show: new Cesium.CallbackProperty(() => visual.visible, false),
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
        pixelOffset: new Cesium.Cartesian2(0, 20),
        verticalOrigin: Cesium.VerticalOrigin.TOP,
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
        clampToGround: true,
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

    return [marker, facing, control];
  }

  clear(): void {
    for (const entities of this.entities.values()) {
      for (const entity of entities) this.viewer.entities.remove(entity);
    }
    this.entities.clear();
    this.visuals.clear();
  }
}
