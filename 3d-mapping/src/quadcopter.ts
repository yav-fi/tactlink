import * as Cesium from "cesium";

export const DRONE_BODY_SIZE = new Cesium.Cartesian3(0.36, 0.24, 0.12);

export function addQuadcopterParts(viewer: Cesium.Viewer, id: string, position: () => Cesium.Cartesian3, orientation: () => Cesium.Quaternion, color: () => Cesium.Color): Cesium.Entity[] {
  const parts: Cesium.Entity[] = [];
  function part(name: string, offset: Cesium.Cartesian3, size: Cesium.Cartesian3, angle: () => number) {
    parts.push(viewer.entities.add({
      id: `${id}_quad_${name}`,
      position: new Cesium.CallbackPositionProperty(() => {
        const rotation = Cesium.Matrix3.fromQuaternion(orientation());
        return Cesium.Cartesian3.add(position(), Cesium.Matrix3.multiplyByVector(rotation, offset, new Cesium.Cartesian3()), new Cesium.Cartesian3());
      }, false),
      orientation: new Cesium.CallbackProperty(() => Cesium.Quaternion.multiply(orientation(), Cesium.Quaternion.fromAxisAngle(Cesium.Cartesian3.UNIT_Z, angle()), new Cesium.Quaternion()), false),
      box: { dimensions: size, material: new Cesium.ColorMaterialProperty(new Cesium.CallbackProperty(color, false)) },
    }));
  }
  for (const x of [-1, 1]) for (const y of [-1, 1]) {
    const name = `${x}_${y}`;
    part(`arm_${name}`, new Cesium.Cartesian3(x * 0.14, y * 0.14, 0), new Cesium.Cartesian3(0.4, 0.035, 0.03), () => Math.atan2(y, x));
    part(`hub_${name}`, new Cesium.Cartesian3(x * 0.28, y * 0.28, 0.04), new Cesium.Cartesian3(0.055, 0.055, 0.06), () => 0);
    // Each rotor has two crossed, thin propeller blades, with a 32 cm diameter.
    for (let blade = 0; blade < 2; blade++) {
      part(`prop_${name}_${blade}`, new Cesium.Cartesian3(x * 0.28, y * 0.28, 0.08), new Cesium.Cartesian3(0.32, 0.025, 0.012), () => blade * Math.PI / 2 + Math.PI / 4);
    }
  }
  return parts;
}
