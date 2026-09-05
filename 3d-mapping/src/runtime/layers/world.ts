import * as Cesium from "cesium";
import type { GeoFrame } from "../frame";
import type { WorldDefinition } from "../types";

/**
 * Static world context from the optional `GET /api/world` endpoint.
 *
 * Obstacles are the reason links drop and relays reposition, so drawing them
 * turns "the network broke" into "the network broke because that block is in
 * the way". Runtime mode works without this endpoint; the layer simply stays
 * empty.
 */

export type WorldOptions = { showRegions: boolean; showObstacles: boolean };

export class WorldLayer {
  private readonly entities: Cesium.Entity[] = [];
  private options: WorldOptions = { showRegions: true, showObstacles: true };
  private definition?: WorldDefinition;

  constructor(private readonly viewer: Cesium.Viewer, private readonly frame: GeoFrame) {}

  setOptions(options: Partial<WorldOptions>): void {
    this.options = { ...this.options, ...options };
  }

  get world(): WorldDefinition | undefined {
    return this.definition;
  }

  /** Region centre in ECEF, used by the FOCUS MISSION camera. */
  regionCenter(regionId: string): Cesium.Cartesian3 | undefined {
    const region = this.definition?.regions.find((item) => item.id === regionId);
    return region ? this.frame.toFixed(region.center) : undefined;
  }

  regionRadius(regionId: string): number {
    return this.definition?.regions.find((item) => item.id === regionId)?.radius ?? 60;
  }

  build(definition: WorldDefinition): void {
    this.clear();
    this.definition = definition;
    const options = () => this.options;

    for (const region of definition.regions) {
      this.entities.push(this.viewer.entities.add({
        id: `runtime-region-${region.id}`,
        name: `Region ${region.id}`,
        position: this.frame.toFixedAt(region.center, 1),
        ellipse: {
          semiMajorAxis: region.radius,
          semiMinorAxis: region.radius,
          height: 1,
          material: Cesium.Color.fromCssColorString("#4a6b8a").withAlpha(0.05),
          outline: true,
          outlineWidth: 2,
          outlineColor: Cesium.Color.fromCssColorString("#6f93b5").withAlpha(0.55),
          show: new Cesium.CallbackProperty(() => options().showRegions, false),
        },
        label: {
          text: `REGION ${region.id}`,
          font: "600 12px ui-monospace, SFMono-Regular, Menlo, monospace",
          fillColor: Cesium.Color.fromCssColorString("#9fbcd6"),
          showBackground: true,
          backgroundColor: Cesium.Color.fromCssColorString("#070c11").withAlpha(0.72),
          backgroundPadding: new Cesium.Cartesian2(8, 5),
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
          translucencyByDistance: new Cesium.NearFarScalar(4000, 1, 20000, 0),
          show: new Cesium.CallbackProperty(() => options().showRegions, false),
        },
      }));
    }

    for (const box of definition.boxes) {
      const center = {
        x: (box.minimum.x + box.maximum.x) / 2,
        y: (box.minimum.y + box.maximum.y) / 2,
        z: (box.minimum.z + box.maximum.z) / 2,
      };
      this.entities.push(this.viewer.entities.add({
        id: `runtime-obstacle-${box.id}`,
        name: `Obstacle ${box.id}`,
        position: this.frame.toFixed(center),
        box: {
          dimensions: new Cesium.Cartesian3(
            Math.abs(box.maximum.x - box.minimum.x),
            Math.abs(box.maximum.y - box.minimum.y),
            Math.abs(box.maximum.z - box.minimum.z),
          ),
          material: Cesium.Color.fromCssColorString("#25313d").withAlpha(0.4),
          outline: true,
          outlineColor: Cesium.Color.fromCssColorString("#5b7b93").withAlpha(0.5),
          show: new Cesium.CallbackProperty(() => options().showObstacles, false),
        },
      }));
    }

    for (const circle of definition.circles) {
      this.entities.push(this.viewer.entities.add({
        id: `runtime-obstacle-${circle.id}`,
        position: this.frame.toFixedAt(circle.center, circle.height / 2),
        cylinder: {
          length: circle.height,
          topRadius: circle.radius,
          bottomRadius: circle.radius,
          material: Cesium.Color.fromCssColorString("#25313d").withAlpha(0.4),
          outline: true,
          outlineColor: Cesium.Color.fromCssColorString("#5b7b93").withAlpha(0.5),
          show: new Cesium.CallbackProperty(() => options().showObstacles, false),
        },
      }));
    }
  }

  clear(): void {
    for (const entity of this.entities) this.viewer.entities.remove(entity);
    this.entities.length = 0;
  }

  destroy(): void {
    this.clear();
  }
}
