import * as Cesium from "cesium";
import type { GeoFrame } from "../frame";
import type { CoverageCell, RegionCoverage, RuntimeSnapshot } from "../types";

/**
 * Search-coverage rendering.
 *
 * A region's whole grid is painted into one canvas and shown on a single
 * rectangle, so a 200-cell grid costs one entity and one texture upload per
 * change instead of 200 entities. Cell edges are drawn into the texture, which
 * keeps the grid legible without a second geometry pass.
 */

export type CoverageMode = "operator" | "node";

export type CoverageOptions = {
  show: boolean;
  mode: CoverageMode;
  /** Node whose direct observations are shown when `mode` is "node". */
  nodeId: string | null;
  emphasisRegion: string | null;
};

const CELL_PIXELS = 10;
const MAX_CANVAS = 1024;

type RegionVisual = {
  regionId: string;
  entity: Cesium.Entity;
  canvas: HTMLCanvasElement;
  material: Cesium.ImageMaterialProperty;
  rectangle: Cesium.Rectangle;
  signature: string;
  height: number;
  visible: boolean;
  emphasised: boolean;
};

type Grid = {
  minX: number;
  minY: number;
  size: number;
  nx: number;
  ny: number;
  z: number;
};

function measure(cells: CoverageCell[]): Grid | null {
  if (!cells.length) return null;
  const size = cells[0].size_m;
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity, z = 0;
  for (const cell of cells) {
    minX = Math.min(minX, cell.center.x - size / 2);
    minY = Math.min(minY, cell.center.y - size / 2);
    maxX = Math.max(maxX, cell.center.x + size / 2);
    maxY = Math.max(maxY, cell.center.y + size / 2);
    z = Math.max(z, cell.center.z);
  }
  return {
    minX, minY, size, z,
    nx: Math.max(1, Math.round((maxX - minX) / size)),
    ny: Math.max(1, Math.round((maxY - minY) / size)),
  };
}

export class CoverageLayer {
  private readonly regions = new Map<string, RegionVisual>();
  private options: CoverageOptions = { show: true, mode: "operator", nodeId: null, emphasisRegion: null };

  constructor(private readonly viewer: Cesium.Viewer, private readonly frame: GeoFrame) {}

  setOptions(options: Partial<CoverageOptions>): void {
    const before = this.options;
    this.options = { ...before, ...options };
    if (before.mode !== this.options.mode || before.nodeId !== this.options.nodeId) {
      for (const visual of this.regions.values()) visual.signature = "";
    }
    for (const visual of this.regions.values()) {
      visual.emphasised = visual.regionId === this.options.emphasisRegion;
    }
  }

  update(snapshot: RuntimeSnapshot): void {
    const seen = new Set<string>();
    for (const region of snapshot.world_knowledge.regions) {
      seen.add(region.region_id);
      this.updateRegion(region);
    }
    for (const [id, visual] of this.regions) {
      if (seen.has(id)) continue;
      this.viewer.entities.remove(visual.entity);
      this.regions.delete(id);
    }
  }

  /** Geographic bounds of a region's grid, for the FOCUS MISSION camera. */
  boundsOf(regionId: string): Cesium.Rectangle | undefined {
    return this.regions.get(regionId)?.rectangle;
  }

  private updateRegion(region: RegionCoverage): void {
    const grid = measure(region.cells);
    if (!grid) return;
    let visual = this.regions.get(region.region_id);
    if (!visual) visual = this.createRegion(region.region_id, grid);
    if (!visual) return;

    const signature = region.cells
      .map((cell) => `${cell.cell_id}:${cell.last_observed === null ? "u" : cell.freshness.toFixed(2)}:${cell.confidence.toFixed(2)}:${cell.observed_by ?? ""}`)
      .join("|");
    if (signature !== visual.signature) {
      visual.signature = signature;
      this.paint(visual, region, grid);
      // Reassigning the image forces Cesium to re-upload the texture.
      visual.material.image = new Cesium.ConstantProperty(visual.canvas);
    }
    visual.visible = this.options.show;
    visual.emphasised = region.region_id === this.options.emphasisRegion;
  }

  private createRegion(regionId: string, grid: Grid): RegionVisual | undefined {
    const cellPixels = Math.max(3, Math.min(CELL_PIXELS, Math.floor(MAX_CANVAS / Math.max(grid.nx, grid.ny))));
    const canvas = document.createElement("canvas");
    canvas.width = grid.nx * cellPixels;
    canvas.height = grid.ny * cellPixels;

    const southWest = Cesium.Cartographic.fromCartesian(
      this.frame.toFixed({ x: grid.minX, y: grid.minY, z: grid.z }),
    );
    const northEast = Cesium.Cartographic.fromCartesian(
      this.frame.toFixed({ x: grid.minX + grid.nx * grid.size, y: grid.minY + grid.ny * grid.size, z: grid.z }),
    );
    if (!southWest || !northEast) return undefined;
    const rectangle = new Cesium.Rectangle(
      Math.min(southWest.longitude, northEast.longitude),
      Math.min(southWest.latitude, northEast.latitude),
      Math.max(southWest.longitude, northEast.longitude),
      Math.max(southWest.latitude, northEast.latitude),
    );

    const material = new Cesium.ImageMaterialProperty({ image: canvas, transparent: true });
    const visual: RegionVisual = {
      regionId,
      canvas,
      material,
      rectangle,
      signature: "",
      height: Math.max(0.5, grid.z - 8),
      visible: this.options.show,
      emphasised: regionId === this.options.emphasisRegion,
      entity: undefined as unknown as Cesium.Entity,
    };

    visual.entity = this.viewer.entities.add({
      id: `runtime-coverage-${regionId}`,
      name: `${regionId} coverage`,
      rectangle: {
        coordinates: rectangle,
        height: visual.height,
        material,
        outline: true,
        outlineColor: new Cesium.CallbackProperty(
          () => Cesium.Color.fromCssColorString("#5f7d99").withAlpha(visual.emphasised ? 0.95 : 0.35),
          false,
        ),
        outlineWidth: 2,
        show: new Cesium.CallbackProperty(() => visual.visible, false),
      },
    });
    this.regions.set(regionId, visual);
    return visual;
  }

  private paint(visual: RegionVisual, region: RegionCoverage, grid: Grid): void {
    const context = visual.canvas.getContext("2d")!;
    const cellW = visual.canvas.width / grid.nx;
    const cellH = visual.canvas.height / grid.ny;
    context.clearRect(0, 0, visual.canvas.width, visual.canvas.height);

    const nodeId = this.options.nodeId;
    const nodeMode = this.options.mode === "node" && nodeId !== null;

    for (const cell of region.cells) {
      const ix = Math.round((cell.center.x - grid.size / 2 - grid.minX) / grid.size);
      const iy = Math.round((cell.center.y - grid.size / 2 - grid.minY) / grid.size);
      if (ix < 0 || iy < 0 || ix >= grid.nx || iy >= grid.ny) continue;
      const x = ix * cellW;
      const y = (grid.ny - 1 - iy) * cellH;

      const mine = !nodeMode || cell.observed_by === nodeId;
      const observed = cell.last_observed !== null && mine;
      if (!observed) {
        context.fillStyle = "rgba(126,148,170,0.09)";
      } else if (cell.freshness >= 0.5) {
        context.fillStyle = `rgba(61,220,151,${(0.16 + 0.5 * cell.confidence).toFixed(3)})`;
      } else {
        context.fillStyle = `rgba(255,180,84,${(0.14 + 0.42 * cell.confidence).toFixed(3)})`;
      }
      context.fillRect(x, y, cellW, cellH);

      context.strokeStyle = observed ? "rgba(6,12,18,0.42)" : "rgba(126,148,170,0.14)";
      context.lineWidth = 1;
      context.strokeRect(x + 0.5, y + 0.5, cellW - 1, cellH - 1);
    }
  }

  destroy(): void {
    for (const visual of this.regions.values()) this.viewer.entities.remove(visual.entity);
    this.regions.clear();
  }
}
