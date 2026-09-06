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
  let people: RuntimeOperator[] = [], follow = true;
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
  let heading = Cesium.Math.toRadians(35), pitch = Cesium.Math.toRadians(-30);
  let drag: { id: number; x: number; y: number } | undefined;
  const priorTouchAction = viewer.canvas.style.touchAction;
  viewer.canvas.style.touchAction = "none";
  const down = (event: PointerEvent) => {
    if (event.button !== 0 || drag) return;
    follow = false; drag = { id: event.pointerId, x: event.clientX, y: event.clientY };
    viewer.canvas.setPointerCapture(event.pointerId); event.preventDefault();
  };
  const move = (event: PointerEvent) => {
    if (!drag || event.pointerId !== drag.id) return;
    heading -= (event.clientX - drag.x) * 0.006;
    pitch = Cesium.Math.clamp(pitch + (event.clientY - drag.y) * 0.006, Cesium.Math.toRadians(-80), Cesium.Math.toRadians(-8));
    drag.x = event.clientX; drag.y = event.clientY; event.preventDefault();
  };
  const up = (event: PointerEvent) => {
    if (!drag || event.pointerId !== drag.id) return;
    if (viewer.canvas.hasPointerCapture(event.pointerId)) viewer.canvas.releasePointerCapture(event.pointerId);
    drag = undefined;
  };
  const zoomBy = (factor: number) => { follow = false; range = Math.max(3, Math.min(120, range * factor)); };
  const wheel = (event: WheelEvent) => {
    const units = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? viewer.canvas.clientHeight : 1;
    zoomBy(Math.exp(Cesium.Math.clamp(event.deltaY * units, -200, 200) * 0.0015));
    event.preventDefault();
  };
  viewer.canvas.addEventListener("pointerdown", down);
  viewer.canvas.addEventListener("pointermove", move);
  viewer.canvas.addEventListener("pointerup", up);
  viewer.canvas.addEventListener("pointercancel", up);
  viewer.canvas.addEventListener("lostpointercapture", up);
  viewer.canvas.addEventListener("wheel", wheel, { passive: false });
  const removeFrame = viewer.scene.preRender.addEventListener(() => {
    const now = performance.now(), dt = Math.min(0.1, (now - previous) / 1000); previous = now;
    const band = phoneFlightBand(people);
    constrainPhoneHeight(target, frame.origin, band);

    const points = [target.cameraPosition, frame.toFixed({ x: 0, y: 0, z: 0 })];
    for (const person of people) {
      points.push(frame.toFixed(person.position), frame.toFixed({ ...person.position, z: person.position.z + PERSON_HEIGHT }));
    }
    if (!people.length) points.push(frame.toFixed({ x: 0, y: 5, z: 0 }));
    const fitted = phoneCameraFrame(points), blend = 1 - Math.exp(-4 * dt);
    if (follow || !center) {
      center = center ? Cesium.Cartesian3.lerp(center, fitted.center, blend, center) : fitted.center;
      if (follow) range = Cesium.Math.lerp(range, fitted.range, blend);
    }
    viewer.camera.lookAt(center, new Cesium.HeadingPitchRange(heading, pitch, range));
  });
  return {
    update(operators: RuntimeOperator[]) { people = operators; },
    focus() { follow = true; },
    zoom: zoomBy,
    dispose() {
      removeFrame(); grid.forEach(entity => viewer.entities.remove(entity));
      if (drag && viewer.canvas.hasPointerCapture(drag.id)) viewer.canvas.releasePointerCapture(drag.id);
      viewer.canvas.removeEventListener("pointerdown", down); viewer.canvas.removeEventListener("pointermove", move);
      viewer.canvas.removeEventListener("pointerup", up); viewer.canvas.removeEventListener("pointercancel", up);
      viewer.canvas.removeEventListener("lostpointercapture", up); viewer.canvas.removeEventListener("wheel", wheel);
      viewer.canvas.style.touchAction = priorTouchAction;
      viewer.camera.lookAtTransform(Cesium.Matrix4.IDENTITY);
    },
  };
}
