import * as Cesium from "cesium";
import type { LocalVector } from "./types";

/**
 * Converts backend local metres (x=east, y=north, z=up) into ECEF.
 *
 * The east-north-up matrix only changes when the backend origin changes, so it
 * is computed once and reused for every conversion in a frame. Every renderer
 * in runtime mode goes through this class; there is no second conversion path.
 */
export class GeoFrame {
  private matrix = Cesium.Matrix4.IDENTITY.clone();
  private inverse = Cesium.Matrix4.IDENTITY.clone();
  private key = "";
  origin = { latitude: 0, longitude: 0, altitude: 0 };

  update(latitude: number, longitude: number, altitude: number): void {
    const key = `${latitude}:${longitude}:${altitude}`;
    if (key === this.key) return;
    this.key = key;
    this.origin = { latitude, longitude, altitude };
    Cesium.Transforms.eastNorthUpToFixedFrame(
      Cesium.Cartesian3.fromDegrees(longitude, latitude, altitude),
      Cesium.Ellipsoid.WGS84,
      this.matrix,
    );
    Cesium.Matrix4.inverseTransformation(this.matrix, this.inverse);
  }

  toFixed(point: LocalVector, result?: Cesium.Cartesian3): Cesium.Cartesian3 {
    const scratch = result ?? new Cesium.Cartesian3();
    Cesium.Cartesian3.fromElements(point.x, point.y, point.z, scratch);
    return Cesium.Matrix4.multiplyByPoint(this.matrix, scratch, scratch);
  }

  /** Same as `toFixed` but with the vertical component replaced. */
  toFixedAt(point: LocalVector, z: number, result?: Cesium.Cartesian3): Cesium.Cartesian3 {
    return this.toFixed({ x: point.x, y: point.y, z }, result);
  }

  /** Rotates a local ENU direction into ECEF without translating it. */
  toFixedVector(direction: LocalVector, result?: Cesium.Cartesian3): Cesium.Cartesian3 {
    const scratch = result ?? new Cesium.Cartesian3();
    Cesium.Cartesian3.fromElements(direction.x, direction.y, direction.z, scratch);
    return Cesium.Matrix4.multiplyByPointAsVector(this.matrix, scratch, scratch);
  }

  /** Converts an ECEF map pick back to backend local ENU metres. */
  toLocal(point: Cesium.Cartesian3): LocalVector {
    const local = Cesium.Matrix4.multiplyByPoint(this.inverse, point, new Cesium.Cartesian3());
    return { x: local.x, y: local.y, z: local.z };
  }
}
