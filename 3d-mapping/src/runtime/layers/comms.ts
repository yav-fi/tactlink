import * as Cesium from "cesium";
import type { GeoFrame } from "../frame";
import type { LocalVector, RuntimeSnapshot } from "../types";

/**
 * Observed communications terrain.
 *
 * Cells come directly from ``snapshot.adaptive.communication_map``. That map is
 * assembled from node-local packet-arrival evidence; this layer never samples
 * simulator link truth or synthesizes a second RF model in the browser.
 */

export type CommOptions = { show: boolean };

const CELL_M = 40;
const MAX_CELLS = 96;

type Bin = { quality: number; samples: number };

export class CommTerrainLayer {
  private readonly bins = new Map<string, Bin>();
  private entity?: Cesium.Entity;
  private canvas?: HTMLCanvasElement;
  private material?: Cesium.ImageMaterialProperty;
  private bounds = { minX: -400, minY: -400, maxX: 400, maxY: 400 };
  private nx = 0;
  private ny = 0;
  private visible = false;
  private dirty = false;
  private lastPaint = 0;

  constructor(private readonly viewer: Cesium.Viewer, private readonly frame: GeoFrame) {}

  setOptions(options: Partial<CommOptions>): void {
    if (options.show !== undefined) this.visible = options.show;
  }

  /** Narrows the sampling grid once the world bounds are known. */
  setWorldBounds(minimum: LocalVector, maximum: LocalVector): void {
    const span = Math.max(maximum.x - minimum.x, maximum.y - minimum.y);
    if (span <= 0 || span / CELL_M > MAX_CELLS * 2) return;
    this.bounds = { minX: minimum.x, minY: minimum.y, maxX: maximum.x, maxY: maximum.y };
    this.reset();
  }

  get sampleCount(): number {
    return this.bins.size;
  }

  update(snapshot: RuntimeSnapshot, now: number): void {
    const next = new Map<string, Bin>();
    for (const cell of snapshot.adaptive.communication_map) {
      const [xText, yText] = cell.cell.split(":");
      const localX = Number(xText) * CELL_M;
      const localY = Number(yText) * CELL_M;
      const ix = Math.floor((localX - this.bounds.minX) / CELL_M);
      const iy = Math.floor((localY - this.bounds.minY) / CELL_M);
      if (!Number.isFinite(ix) || !Number.isFinite(iy) || ix < 0 || iy < 0) continue;
      next.set(`${ix}:${iy}`, { quality: cell.quality, samples: Math.max(1, cell.samples) });
    }
    if (JSON.stringify([...next]) !== JSON.stringify([...this.bins])) {
      this.bins.clear();
      for (const [key, value] of next) this.bins.set(key, value);
      this.dirty = true;
    }
    if (!this.visible || !this.dirty || now - this.lastPaint < 600) return;
    this.lastPaint = now;
    this.dirty = false;
    this.paint();
  }

  private ensureEntity(): void {
    if (this.entity) return;
    this.nx = Math.max(1, Math.ceil((this.bounds.maxX - this.bounds.minX) / CELL_M));
    this.ny = Math.max(1, Math.ceil((this.bounds.maxY - this.bounds.minY) / CELL_M));
    const canvas = document.createElement("canvas");
    canvas.width = Math.min(1024, this.nx * 8);
    canvas.height = Math.min(1024, this.ny * 8);
    this.canvas = canvas;
    this.material = new Cesium.ImageMaterialProperty({ image: canvas, transparent: true });

    const southWest = Cesium.Cartographic.fromCartesian(
      this.frame.toFixed({ x: this.bounds.minX, y: this.bounds.minY, z: 0 }),
    );
    const northEast = Cesium.Cartographic.fromCartesian(
      this.frame.toFixed({ x: this.bounds.minX + this.nx * CELL_M, y: this.bounds.minY + this.ny * CELL_M, z: 0 }),
    );
    if (!southWest || !northEast) return;

    this.entity = this.viewer.entities.add({
      id: "runtime-comm-terrain",
      name: "Observed communications terrain",
      rectangle: {
        coordinates: new Cesium.Rectangle(
          Math.min(southWest.longitude, northEast.longitude),
          Math.min(southWest.latitude, northEast.latitude),
          Math.max(southWest.longitude, northEast.longitude),
          Math.max(southWest.latitude, northEast.latitude),
        ),
        height: 2,
        material: this.material,
        show: new Cesium.CallbackProperty(() => this.visible, false),
      },
    });
  }

  private paint(): void {
    this.ensureEntity();
    const canvas = this.canvas;
    if (!canvas || !this.material) return;
    const context = canvas.getContext("2d")!;
    context.clearRect(0, 0, canvas.width, canvas.height);
    const cellW = canvas.width / this.nx;
    const cellH = canvas.height / this.ny;
    for (const [key, bin] of this.bins) {
      const [ixText, iyText] = key.split(":");
      const ix = Number(ixText);
      const iy = Number(iyText);
      if (ix >= this.nx || iy >= this.ny) continue;
      const confidence = Math.min(1, bin.samples / 25);
      const alpha = (0.1 + 0.34 * confidence).toFixed(3);
      const quality = bin.quality;
      const colour = quality >= 0.55
        ? `rgba(64,190,255,${alpha})`
        : quality >= 0.25
          ? `rgba(255,190,90,${alpha})`
          : `rgba(255,96,96,${alpha})`;
      context.fillStyle = colour;
      context.fillRect(ix * cellW, (this.ny - 1 - iy) * cellH, cellW, cellH);
    }
    this.material.image = new Cesium.ConstantProperty(canvas);
  }

  reset(): void {
    this.bins.clear();
    if (this.entity) this.viewer.entities.remove(this.entity);
    this.entity = undefined;
    this.canvas = undefined;
    this.material = undefined;
    this.dirty = true;
  }

  destroy(): void {
    this.reset();
  }
}
