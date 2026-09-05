import * as Cesium from "cesium";
import type { GeoFrame } from "../frame";
import { objectiveIcon } from "../icons";
import { ROLE_STYLES, shortId } from "../taxonomy";
import type { RuntimeSnapshot, RuntimeTask } from "../types";

/**
 * Mission objective markers.
 *
 * Each active task gets one marker whose shape encodes the task type, plus its
 * planned waypoint pattern so a SEARCH sweep reads as an intent rather than a
 * dot. Completed and cancelled tasks are dropped.
 */

export type ObjectiveOptions = { show: boolean; showPatterns: boolean };

type ObjectiveVisual = {
  id: string;
  position: Cesium.Cartesian3;
  pattern: Cesium.Cartesian3[];
  label: string;
  color: Cesium.Color;
  visible: boolean;
  selected: boolean;
  kind: "search" | "watch" | "relay" | "point";
};

const iconKind = (task: RuntimeTask): ObjectiveVisual["kind"] => {
  if (task.type === "SEARCH") return "search";
  if (task.type === "WATCH") return "watch";
  if (task.type === "RELAY") return "relay";
  return "point";
};

export class ObjectiveLayer {
  private readonly visuals = new Map<string, ObjectiveVisual>();
  private readonly entities = new Map<string, Cesium.Entity[]>();
  private options: ObjectiveOptions = { show: true, showPatterns: true };
  private selectedTask: string | null = null;

  constructor(private readonly viewer: Cesium.Viewer, private readonly frame: GeoFrame) {}

  setOptions(options: Partial<ObjectiveOptions>): void {
    this.options = { ...this.options, ...options };
  }

  setSelected(taskId: string | null): void {
    this.selectedTask = taskId;
    for (const visual of this.visuals.values()) visual.selected = visual.id === taskId;
  }

  positionOf(taskId: string): Cesium.Cartesian3 | undefined {
    return this.visuals.get(taskId)?.position;
  }

  taskIdFromEntity(entityId: string): string | null {
    const match = /^runtime-objective-(.+)$/.exec(entityId);
    return match ? match[1] : null;
  }

  update(snapshot: RuntimeSnapshot): void {
    const seen = new Set<string>();
    for (const task of snapshot.missions) {
      if (!task.target.point) continue;
      if (task.status === "COMPLETED" || task.status === "CANCELLED") continue;
      seen.add(task.id);
      let visual = this.visuals.get(task.id);
      if (!visual) {
        visual = {
          id: task.id,
          position: new Cesium.Cartesian3(),
          pattern: [],
          label: "",
          color: Cesium.Color.WHITE.clone(),
          visible: true,
          selected: this.selectedTask === task.id,
          kind: iconKind(task),
        };
        this.visuals.set(task.id, visual);
        this.entities.set(task.id, this.createEntities(visual));
      }
      this.frame.toFixed(task.target.point, visual.position);

      const waypoints = task.target.waypoints;
      const needed = waypoints.length ? waypoints.length + 1 : 0;
      visual.pattern.length = 0;
      for (const waypoint of waypoints) visual.pattern.push(this.frame.toFixed(waypoint));
      if (needed) visual.pattern.push(this.frame.toFixed(waypoints[0]));

      const role = ROLE_STYLES[task.type as keyof typeof ROLE_STYLES] ?? ROLE_STYLES.GOTO;
      visual.color = Cesium.Color.fromCssColorString(role.color);
      visual.kind = iconKind(task);
      const holders = task.assigned_nodes.map(shortId).join(" ") || "UNASSIGNED";
      visual.label = [
        `${task.type}  P${task.priority}`,
        `${holders}   EFF ${Math.round(task.effectiveness * 100)}%`,
        task.status === "DEGRADED" ? "DEGRADED" : "",
      ].filter(Boolean).join("\n");
      visual.visible = this.options.show;
    }

    for (const [id, entities] of this.entities) {
      if (seen.has(id)) continue;
      for (const entity of entities) this.viewer.entities.remove(entity);
      this.entities.delete(id);
      this.visuals.delete(id);
    }
  }

  private createEntities(visual: ObjectiveVisual): Cesium.Entity[] {
    const options = () => this.options;
    const marker = this.viewer.entities.add({
      id: `runtime-objective-${visual.id}`,
      name: visual.id,
      position: new Cesium.CallbackPositionProperty(() => visual.position, false),
      billboard: {
        image: new Cesium.CallbackProperty(
          () => objectiveIcon(visual.kind, visual.selected ? "#ffffff" : visual.color.toCssColorString()),
          false,
        ),
        scale: 0.55,
        disableDepthTestDistance: Number.POSITIVE_INFINITY,
        show: new Cesium.CallbackProperty(() => visual.visible, false),
      },
      label: {
        text: new Cesium.CallbackProperty(() => visual.label, false),
        font: "600 12px ui-monospace, SFMono-Regular, Menlo, monospace",
        fillColor: new Cesium.CallbackProperty(() => (visual.selected ? Cesium.Color.WHITE : visual.color), false),
        showBackground: true,
        backgroundColor: Cesium.Color.fromCssColorString("#070c11").withAlpha(0.78),
        backgroundPadding: new Cesium.Cartesian2(8, 5),
        pixelOffset: new Cesium.Cartesian2(0, 24),
        verticalOrigin: Cesium.VerticalOrigin.TOP,
        disableDepthTestDistance: Number.POSITIVE_INFINITY,
        translucencyByDistance: new Cesium.NearFarScalar(5000, 1, 22000, 0),
        show: new Cesium.CallbackProperty(() => visual.visible, false),
      },
    });

    const pattern = this.viewer.entities.add({
      id: `runtime-pattern-${visual.id}`,
      polyline: {
        positions: new Cesium.CallbackProperty(() => visual.pattern, false),
        width: 1.8,
        arcType: Cesium.ArcType.NONE,
        material: new Cesium.PolylineDashMaterialProperty({
          color: new Cesium.CallbackProperty(
            () => visual.color.withAlpha(visual.selected ? 0.85 : 0.35),
            false,
          ),
          dashPattern: 0xf0f0,
          dashLength: 26,
        }),
        show: new Cesium.CallbackProperty(
          () => visual.visible && options().showPatterns && visual.pattern.length > 1,
          false,
        ),
      },
    });

    return [marker, pattern];
  }

  destroy(): void {
    for (const entities of this.entities.values()) {
      for (const entity of entities) this.viewer.entities.remove(entity);
    }
    this.entities.clear();
    this.visuals.clear();
  }
}
