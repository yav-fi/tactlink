import * as Cesium from "cesium";
import type { DroneController } from "./drone-controller";
import type { GeoFrame } from "./runtime/frame";
import type { RuntimeOperator } from "./runtime/types";
import { constrainPhoneHeight, phoneFlightBand } from "./phone-flight";

export const PERSON_HEIGHT = 1.75;
// The authored model has 1.48 m between front/rear rotor centers and blades
// with a 0.52 x 0.035 m half-extent. Scale its full spinning envelope to 0.8 m.
export const PHONE_DRONE_SCALE = 0.8 / (1.48 + 2 * Math.hypot(0.52, 0.035));

export function phoneCameraFrame(points: Cesium.Cartesian3[]) {
  const sphere = Cesium.BoundingSphere.fromPoints(points);
  return { center: sphere.center, range: Math.max(12, sphere.radius * 3.2) };
}

export function startPhoneScene(viewer: Cesium.Viewer, target: DroneController, frame: GeoFrame) {
  let people: RuntimeOperator[] = [], follow = true, zoom = 1;
  let center: Cesium.Cartesian3 | undefined, range = 14, previous = performance.now();
  const entity = viewer.entities.getById(target.id);
  if (entity?.model) {
    entity.model.scale = new Cesium.ConstantProperty(PHONE_DRONE_SCALE);
    entity.model.minimumPixelSize = new Cesium.ConstantProperty(0);
    entity.model.maximumScale = new Cesium.ConstantProperty(PHONE_DRONE_SCALE);
  }
  // A fixed one-metre ruler in the same ENU frame as the UWB positions.
  const grid: Cesium.Entity[] = [];
  for (let i = -10; i <= 15; i++) {
    for (const vertical of [false, true]) {
      const positions = (vertical ? [{ x: i, y: -10, z: 0.02 }, { x: i, y: 15, z: 0.02 }]
        : [{ x: -10, y: i, z: 0.02 }, { x: 15, y: i, z: 0.02 }]).map(p => frame.toFixed(p));
      grid.push(viewer.entities.add({ polyline: { positions, arcType: Cesium.ArcType.NONE, width: i % 5 === 0 ? 1.4 : 0.6,
        material: Cesium.Color.fromCssColorString(i % 5 === 0 ? "#73cbbd" : "#668e88").withAlpha(i % 5 === 0 ? 0.5 : 0.22) } }));
    }
  }
  const manual = () => { follow = false; };
  viewer.canvas.addEventListener("pointerdown", manual);
  viewer.canvas.addEventListener("wheel", manual, { passive: true });
  const removeFrame = viewer.scene.preRender.addEventListener(() => {
    const now = performance.now(), dt = Math.min(0.1, (now - previous) / 1000); previous = now;
    const band = phoneFlightBand(people);
    constrainPhoneHeight(target, frame.origin, band);
    if (!follow) return;
    const points = [target.cameraPosition, frame.toFixed({ x: 0, y: 0, z: 0 })];
    for (const person of people) {
      points.push(frame.toFixed(person.position), frame.toFixed({ ...person.position, z: person.position.z + PERSON_HEIGHT }));
    }
    if (!people.length) points.push(frame.toFixed({ x: 0, y: 5, z: 0 }));
    const fitted = phoneCameraFrame(points), blend = 1 - Math.exp(-4 * dt);
    center = center ? Cesium.Cartesian3.lerp(center, fitted.center, blend, center) : fitted.center;
    range = Cesium.Math.lerp(range, fitted.range * zoom, blend);
    viewer.camera.lookAt(center, new Cesium.HeadingPitchRange(Cesium.Math.toRadians(35), Cesium.Math.toRadians(-30), range));
  });
  return {
    update(operators: RuntimeOperator[]) { people = operators; },
    focus() { follow = true; zoom = 1; },
    zoom(factor: number) { follow = true; zoom = Math.max(0.55, Math.min(2.5, zoom * factor)); },
    dispose() {
      removeFrame(); grid.forEach(entity => viewer.entities.remove(entity));
      viewer.canvas.removeEventListener("pointerdown", manual); viewer.canvas.removeEventListener("wheel", manual);
      viewer.camera.lookAtTransform(Cesium.Matrix4.IDENTITY);
    },
  };
}
