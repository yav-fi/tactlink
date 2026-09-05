import * as Cesium from "cesium";

/**
 * Camera modes for runtime mode.
 *
 * Framing modes (overview, top, focus) are one-shot flights that hand control
 * straight back to the user. Tracking modes either use Cesium's own entity
 * tracking, which keeps free orbit available, or drive the camera per frame and
 * release it the moment the operator touches the mouse.
 */

export type CameraMode = "free" | "overview" | "follow" | "chase" | "nose" | "top" | "focus";

export type CameraTargets = {
  dronePosition: (id: string) => Cesium.Cartesian3 | undefined;
  droneForward: (id: string) => Cesium.Cartesian3 | undefined;
  droneEntity: (id: string) => Cesium.Entity | undefined;
  allPositions: () => Cesium.Cartesian3[];
  regionBounds: (regionId: string) => Cesium.Rectangle | undefined;
};

const CHASE_RANGE = 78;
const CHASE_PITCH = -0.30;
const NOSE_AHEAD = 6;
const NOSE_UP = 1.4;

export class CameraDirector {
  private mode: CameraMode = "free";
  private targetId: string | null = null;
  private focusRegion: string | null = null;
  private readonly listeners = new Set<(mode: CameraMode) => void>();
  private releasing = false;

  constructor(private readonly viewer: Cesium.Viewer, private readonly targets: CameraTargets) {
    // Any deliberate camera input drops the cinematic modes rather than
    // fighting the operator for control of the view.
    const surface = viewer.scene.canvas;
    for (const event of ["pointerdown", "wheel"] as const) {
      surface.addEventListener(event, () => {
        if (this.mode === "chase" || this.mode === "nose") this.setMode("free");
      }, { passive: true });
    }
    viewer.scene.preRender.addEventListener(() => this.tick());
  }

  onChange(listener: (mode: CameraMode) => void): void {
    this.listeners.add(listener);
  }

  get current(): CameraMode {
    return this.mode;
  }

  get target(): string | null {
    return this.targetId;
  }

  setTarget(id: string | null): void {
    this.targetId = id;
    if (this.mode === "follow") this.applyFollow();
  }

  setFocusRegion(regionId: string | null): void {
    this.focusRegion = regionId;
  }

  setMode(mode: CameraMode): void {
    if (this.mode === mode) {
      if (mode === "overview" || mode === "top" || mode === "focus") this.enter(mode);
      return;
    }
    this.release();
    this.mode = mode;
    this.enter(mode);
    for (const listener of this.listeners) listener(mode);
  }

  private release(): void {
    this.releasing = true;
    this.viewer.trackedEntity = undefined;
    this.viewer.camera.lookAtTransform(Cesium.Matrix4.IDENTITY);
    this.releasing = false;
  }

  private enter(mode: CameraMode): void {
    switch (mode) {
      case "overview":
        this.frameAll();
        break;
      case "top":
        this.frameAll(-Cesium.Math.PI_OVER_TWO + 0.001);
        break;
      case "focus":
        this.frameFocus();
        break;
      case "follow":
        this.applyFollow();
        break;
      default:
        break;
    }
  }

  private applyFollow(): void {
    const entity = this.targetId ? this.targets.droneEntity(this.targetId) : undefined;
    this.viewer.trackedEntity = entity;
  }

  private frameAll(pitch = -0.62): void {
    const positions = this.targets.allPositions();
    if (!positions.length) return;
    const sphere = Cesium.BoundingSphere.fromPoints(positions);
    sphere.radius = Math.max(sphere.radius * 1.5, 190);
    this.viewer.camera.flyToBoundingSphere(sphere, {
      duration: 1.4,
      offset: new Cesium.HeadingPitchRange(0, pitch, 0),
    });
  }

  private frameFocus(): void {
    const bounds = this.focusRegion ? this.targets.regionBounds(this.focusRegion) : undefined;
    if (!bounds) {
      this.frameAll();
      return;
    }
    this.viewer.camera.flyTo({
      destination: bounds,
      duration: 1.3,
      orientation: { heading: 0, pitch: -0.85, roll: 0 },
    });
  }

  /** Drives the per-frame cinematic modes. */
  private tick(): void {
    if (this.releasing) return;
    if (this.mode !== "chase" && this.mode !== "nose") return;
    const id = this.targetId;
    if (!id) return;
    const position = this.targets.dronePosition(id);
    const forward = this.targets.droneForward(id);
    if (!position || !forward) return;

    const bearing = bearingOf(position, forward);
    if (this.mode === "chase") {
      this.viewer.camera.lookAt(position, new Cesium.HeadingPitchRange(bearing, CHASE_PITCH, CHASE_RANGE));
      return;
    }

    const ahead = Cesium.Cartesian3.multiplyByScalar(forward, NOSE_AHEAD, new Cesium.Cartesian3());
    const up = Cesium.Cartesian3.multiplyByScalar(
      Cesium.Ellipsoid.WGS84.geodeticSurfaceNormal(position, new Cesium.Cartesian3()),
      NOSE_UP,
      new Cesium.Cartesian3(),
    );
    const eye = Cesium.Cartesian3.add(
      Cesium.Cartesian3.add(position, ahead, new Cesium.Cartesian3()), up, new Cesium.Cartesian3(),
    );
    this.viewer.camera.setView({
      destination: eye,
      orientation: { heading: bearing, pitch: -0.06, roll: 0 },
    });
  }
}

/** Compass bearing, clockwise from north, of an ECEF direction at a position. */
export function bearingOf(position: Cesium.Cartesian3, direction: Cesium.Cartesian3): number {
  const frame = Cesium.Transforms.eastNorthUpToFixedFrame(position);
  const inverse = Cesium.Matrix4.inverseTransformation(frame, new Cesium.Matrix4());
  const local = Cesium.Matrix4.multiplyByPointAsVector(inverse, direction, new Cesium.Cartesian3());
  return Math.atan2(local.x, local.y);
}
