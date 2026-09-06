/**
 * Generates the bundled UAV model used by runtime mode.
 *
 * The airframe is laid out in a convenient authoring frame (nose +X, up +Y)
 * and then rotated -90 degrees about Y on write, so the exported glTF has its
 * nose on +Z -- the forward axis Cesium assumes for glTF. After Cesium's axis
 * correction that lands as:
 *   glTF +Z (nose)  -> entity local +X   (heading axis, bearing = 90deg + heading)
 *   glTF +Y (up)    -> entity local +Z   (up)
 *   glTF +X (port)  -> entity local +Y   (port)
 * which gives HeadingPitchRoll its natural aircraft meaning: positive pitch is
 * nose up, positive roll is a right bank. `tests/aircraft.test.cjs` asserts
 * these axis facts against the installed Cesium build.
 *
 * Run: node scripts/generate-uav-glb.mjs
 */
import { writeFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const positions = [];
const normals = [];
const indices = [];
/** @type {{material: number, first: number, count: number}[]} */
const primitives = [];
let primitiveStart = 0;
let primitiveMaterial = 0;

function beginPrimitive(material) {
  primitiveStart = indices.length;
  primitiveMaterial = material;
}

function endPrimitive() {
  primitives.push({ material: primitiveMaterial, first: primitiveStart, count: indices.length - primitiveStart });
}

/** Authoring frame (nose +X, up +Y) -> export frame (nose +Z, up +Y). */
function toExportFrame([x, y, z]) {
  return [-z, y, x];
}

function pushVertex(position, normal) {
  const index = positions.length / 3;
  const p = toExportFrame(position);
  const n = toExportFrame(normal);
  positions.push(p[0], p[1], p[2]);
  normals.push(n[0], n[1], n[2]);
  return index;
}

function pushQuad(a, b, c, d, normal) {
  const ia = pushVertex(a, normal);
  const ib = pushVertex(b, normal);
  const ic = pushVertex(c, normal);
  const id = pushVertex(d, normal);
  indices.push(ia, ib, ic, ia, ic, id);
}

/** Axis-aligned box between two corners. */
function box(min, max) {
  const [x0, y0, z0] = min;
  const [x1, y1, z1] = max;
  pushQuad([x1, y0, z0], [x1, y0, z1], [x1, y1, z1], [x1, y1, z0], [1, 0, 0]);
  pushQuad([x0, y0, z1], [x0, y0, z0], [x0, y1, z0], [x0, y1, z1], [-1, 0, 0]);
  pushQuad([x0, y1, z0], [x1, y1, z0], [x1, y1, z1], [x0, y1, z1], [0, 1, 0]);
  pushQuad([x0, y0, z1], [x1, y0, z1], [x1, y0, z0], [x0, y0, z0], [0, -1, 0]);
  pushQuad([x0, y0, z1], [x0, y1, z1], [x1, y1, z1], [x1, y0, z1], [0, 0, 1]);
  pushQuad([x1, y0, z0], [x1, y1, z0], [x0, y1, z0], [x0, y0, z0], [0, 0, -1]);
}

/** Rounded shell with smooth vertex normals. */
function ellipsoid(center, radii, latitudeSegments = 10, longitudeSegments = 20) {
  const [cx, cy, cz] = center;
  const [rx, ry, rz] = radii;
  for (let latitude = 0; latitude < latitudeSegments; latitude += 1) {
    const v0 = -Math.PI / 2 + latitude / latitudeSegments * Math.PI;
    const v1 = -Math.PI / 2 + (latitude + 1) / latitudeSegments * Math.PI;
    for (let longitude = 0; longitude < longitudeSegments; longitude += 1) {
      const u0 = longitude / longitudeSegments * Math.PI * 2;
      const u1 = (longitude + 1) / longitudeSegments * Math.PI * 2;
      const vertex = (v, u) => {
        const local = [rx * Math.cos(v) * Math.cos(u), ry * Math.sin(v), rz * Math.cos(v) * Math.sin(u)];
        const rawNormal = [local[0] / (rx * rx), local[1] / (ry * ry), local[2] / (rz * rz)];
        const length = Math.hypot(...rawNormal) || 1;
        return pushVertex([cx + local[0], cy + local[1], cz + local[2]], rawNormal.map(value => value / length));
      };
      const a = vertex(v0, u0), b = vertex(v0, u1), c = vertex(v1, u1), d = vertex(v1, u0);
      indices.push(a, c, b, a, d, c);
    }
  }
}

/** Box rotated about the vertical authoring axis. */
function rotatedBox(center, halfSize, angle) {
  const [cx, cy, cz] = center;
  const [hx, hy, hz] = halfSize;
  const rotate = ([x, y, z]) => [cx + x * Math.cos(angle) - z * Math.sin(angle), cy + y, cz + x * Math.sin(angle) + z * Math.cos(angle)];
  const normal = ([x, y, z]) => [x * Math.cos(angle) - z * Math.sin(angle), y, x * Math.sin(angle) + z * Math.cos(angle)];
  const p = (x, y, z) => rotate([x, y, z]);
  pushQuad(p(hx, -hy, -hz), p(hx, -hy, hz), p(hx, hy, hz), p(hx, hy, -hz), normal([1, 0, 0]));
  pushQuad(p(-hx, -hy, hz), p(-hx, -hy, -hz), p(-hx, hy, -hz), p(-hx, hy, hz), normal([-1, 0, 0]));
  pushQuad(p(-hx, hy, -hz), p(hx, hy, -hz), p(hx, hy, hz), p(-hx, hy, hz), [0, 1, 0]);
  pushQuad(p(-hx, -hy, hz), p(hx, -hy, hz), p(hx, -hy, -hz), p(-hx, -hy, -hz), [0, -1, 0]);
  pushQuad(p(-hx, -hy, hz), p(-hx, hy, hz), p(hx, hy, hz), p(hx, -hy, hz), normal([0, 0, 1]));
  pushQuad(p(hx, -hy, -hz), p(hx, hy, -hz), p(-hx, hy, -hz), p(-hx, -hy, -hz), normal([0, 0, -1]));
}

/** Convex quad/triangle fan patch used for the nose and fin wedges. */
function triangle(a, b, c) {
  const ux = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
  const vx = [c[0] - a[0], c[1] - a[1], c[2] - a[2]];
  const n = [ux[1] * vx[2] - ux[2] * vx[1], ux[2] * vx[0] - ux[0] * vx[2], ux[0] * vx[1] - ux[1] * vx[0]];
  const length = Math.hypot(n[0], n[1], n[2]) || 1;
  const normal = [n[0] / length, n[1] / length, n[2] / length];
  const ia = pushVertex(a, normal);
  const ib = pushVertex(b, normal);
  const ic = pushVertex(c, normal);
  indices.push(ia, ib, ic);
}

/** Short vertical cylinder used for rotor hubs and motor pods. */
function cylinder(center, radius, height, segments = 14) {
  const [cx, cy, cz] = center;
  const bottom = cy - height / 2;
  const top = cy + height / 2;
  for (let step = 0; step < segments; step += 1) {
    const a0 = (step / segments) * Math.PI * 2;
    const a1 = ((step + 1) / segments) * Math.PI * 2;
    const p0 = [cx + Math.cos(a0) * radius, cz + Math.sin(a0) * radius];
    const p1 = [cx + Math.cos(a1) * radius, cz + Math.sin(a1) * radius];
    const mid = [(Math.cos(a0) + Math.cos(a1)) / 2, (Math.sin(a0) + Math.sin(a1)) / 2];
    const length = Math.hypot(mid[0], mid[1]) || 1;
    const normal = [mid[0] / length, 0, mid[1] / length];
    pushQuad([p0[0], bottom, p0[1]], [p1[0], bottom, p1[1]], [p1[0], top, p1[1]], [p0[0], top, p0[1]], normal);
    triangle([cx, top, cz], [p0[0], top, p0[1]], [p1[0], top, p1[1]]);
    triangle([cx, bottom, cz], [p1[0], bottom, p1[1]], [p0[0], bottom, p0[1]]);
  }
}

const MATERIAL_SHELL = 0;
const MATERIAL_ACCENT = 1;
const MATERIAL_ROTOR = 2;
const MATERIAL_DARK = 3;
const MATERIAL_PANEL = 4;

// --- Airframe ---------------------------------------------------------------
// Nose points +X. The proportions follow a compact modern camera quadcopter:
// a low aerodynamic center body, exposed carbon arms and actual slim blades.
beginPrimitive(MATERIAL_SHELL);
ellipsoid([-0.03, 0.01, 0], [0.78, 0.25, 0.38], 12, 24);
endPrimitive();

beginPrimitive(MATERIAL_PANEL);
ellipsoid([0.18, 0.20, 0], [0.46, 0.14, 0.27], 10, 22); // smoked upper canopy
ellipsoid([0.57, -0.17, 0], [0.17, 0.13, 0.16], 8, 16); // stabilized camera pod
endPrimitive();

beginPrimitive(MATERIAL_ACCENT);
rotatedBox([0.05, 0.335, 0], [0.47, 0.018, 0.032], 0); // thin red center streak
ellipsoid([0.69, 0.015, 0], [0.16, 0.11, 0.20], 7, 16); // readable red nose cap
endPrimitive();

beginPrimitive(MATERIAL_DARK);
for (const [ax, az] of [[0.78, 0.72], [0.78, -0.72], [-0.70, 0.72], [-0.70, -0.72]]) {
  const length = Math.hypot(ax, az);
  rotatedBox([ax * 0.53, 0.03, az * 0.53], [length * 0.53, 0.045, 0.045], Math.atan2(az, ax));
  cylinder([ax, 0.12, az], 0.13, 0.24, 18);
}
rotatedBox([-0.05, -0.42, -0.30], [0.47, 0.035, 0.035], 0); // landing rails
rotatedBox([-0.05, -0.42, 0.30], [0.47, 0.035, 0.035], 0);
box([-0.33, -0.39, -0.33], [-0.27, -0.16, -0.27]);
box([0.25, -0.39, -0.33], [0.31, -0.16, -0.27]);
box([-0.33, -0.39, 0.27], [-0.27, -0.16, 0.33]);
box([0.25, -0.39, 0.27], [0.31, -0.16, 0.33]);
endPrimitive();

beginPrimitive(MATERIAL_ROTOR);
rotatedBox([0, 0, 0], [0.52, 0.012, 0.035], Math.PI / 4);
rotatedBox([0, 0.002, 0], [0.52, 0.012, 0.035], Math.PI * 3 / 4);
endPrimitive();

// --- glTF assembly ----------------------------------------------------------
const positionArray = new Float32Array(positions);
const normalArray = new Float32Array(normals);
const indexArray = new Uint32Array(indices);
const animationFrames = 25;
const animationTimes = new Float32Array(Array.from({ length: animationFrames }, (_, index) => index / (animationFrames - 1)));
const rotorRotations = (direction) => new Float32Array(Array.from({ length: animationFrames }, (_, index) => {
  const angle = direction * 6 * Math.PI * 2 * index / (animationFrames - 1);
  return [0, Math.sin(angle / 2), 0, Math.cos(angle / 2)];
}).flat());
const clockwiseRotations = rotorRotations(1);
const counterClockwiseRotations = rotorRotations(-1);
const align4 = (value) => (value + 3) & ~3;

const positionOffset = 0;
const normalOffset = align4(positionOffset + positionArray.byteLength);
const indexOffset = align4(normalOffset + normalArray.byteLength);
const animationTimeOffset = align4(indexOffset + indexArray.byteLength);
const clockwiseOffset = align4(animationTimeOffset + animationTimes.byteLength);
const counterClockwiseOffset = align4(clockwiseOffset + clockwiseRotations.byteLength);
const binaryLength = align4(counterClockwiseOffset + counterClockwiseRotations.byteLength);
const binary = Buffer.alloc(binaryLength);
Buffer.from(positionArray.buffer, positionArray.byteOffset, positionArray.byteLength).copy(binary, positionOffset);
Buffer.from(normalArray.buffer, normalArray.byteOffset, normalArray.byteLength).copy(binary, normalOffset);
Buffer.from(indexArray.buffer, indexArray.byteOffset, indexArray.byteLength).copy(binary, indexOffset);
Buffer.from(animationTimes.buffer, animationTimes.byteOffset, animationTimes.byteLength).copy(binary, animationTimeOffset);
Buffer.from(clockwiseRotations.buffer, clockwiseRotations.byteOffset, clockwiseRotations.byteLength).copy(binary, clockwiseOffset);
Buffer.from(counterClockwiseRotations.buffer, counterClockwiseRotations.byteOffset, counterClockwiseRotations.byteLength).copy(binary, counterClockwiseOffset);

const min = [Infinity, Infinity, Infinity];
const max = [-Infinity, -Infinity, -Infinity];
for (let index = 0; index < positions.length; index += 3) {
  for (let axis = 0; axis < 3; axis += 1) {
    min[axis] = Math.min(min[axis], positions[index + axis]);
    max[axis] = Math.max(max[axis], positions[index + axis]);
  }
}

const vertexCount = positions.length / 3;
const accessors = [
  { bufferView: 0, componentType: 5126, count: vertexCount, type: "VEC3", min, max },
  { bufferView: 1, componentType: 5126, count: vertexCount, type: "VEC3" },
];
const meshPrimitives = primitives.map((primitive) => {
  accessors.push({
    bufferView: 2,
    byteOffset: primitive.first * 4,
    componentType: 5125,
    count: primitive.count,
    type: "SCALAR",
  });
  return { attributes: { POSITION: 0, NORMAL: 1 }, indices: accessors.length - 1, material: primitive.material };
});
const rotorPrimitive = meshPrimitives.at(-1);
const bodyPrimitives = meshPrimitives.slice(0, -1);
const animationInputAccessor = accessors.push({ bufferView: 3, componentType: 5126, count: animationFrames, type: "SCALAR", min: [0], max: [1] }) - 1;
const clockwiseAccessor = accessors.push({ bufferView: 4, componentType: 5126, count: animationFrames, type: "VEC4" }) - 1;
const counterClockwiseAccessor = accessors.push({ bufferView: 5, componentType: 5126, count: animationFrames, type: "VEC4" }) - 1;
const rotorCenters = [[0.78, 0.265, 0.72], [0.78, 0.265, -0.72], [-0.70, 0.265, 0.72], [-0.70, 0.265, -0.72]];
const nodes = [
  { mesh: 0, name: "uav_body" },
  ...rotorCenters.map((center, index) => ({ mesh: 1, name: `rotor_${index + 1}`, translation: toExportFrame(center) })),
];
const animationSamplers = rotorCenters.map((_, index) => ({
  input: animationInputAccessor,
  output: index % 2 ? counterClockwiseAccessor : clockwiseAccessor,
  interpolation: "LINEAR",
}));

const gltf = {
  asset: { version: "2.0", generator: "dnhacks26 generate-uav-glb" },
  scene: 0,
  scenes: [{ nodes: nodes.map((_, index) => index) }],
  nodes,
  meshes: [{ name: "uav_body", primitives: bodyPrimitives }, { name: "rotor_blades", primitives: [rotorPrimitive] }],
  animations: [{
    name: "spin_propellers",
    samplers: animationSamplers,
    channels: animationSamplers.map((_, index) => ({ sampler: index, target: { node: index + 1, path: "rotation" } })),
  }],
  materials: [
    { name: "shell", pbrMetallicRoughness: { baseColorFactor: [0.075, 0.082, 0.095, 1], metallicFactor: 0.38, roughnessFactor: 0.48 } },
    { name: "accent", pbrMetallicRoughness: { baseColorFactor: [0.95, 0.012, 0.035, 1], metallicFactor: 0.3, roughnessFactor: 0.18 }, emissiveFactor: [0.8, 0.008, 0.018], doubleSided: true },
    { name: "rotor", pbrMetallicRoughness: { baseColorFactor: [0.025, 0.03, 0.038, 1], metallicFactor: 0.55, roughnessFactor: 0.32 }, doubleSided: true },
    { name: "dark", pbrMetallicRoughness: { baseColorFactor: [0.035, 0.04, 0.05, 1], metallicFactor: 0.82, roughnessFactor: 0.2 } },
    { name: "blackPanel", pbrMetallicRoughness: { baseColorFactor: [0.006, 0.008, 0.012, 1], metallicFactor: 0.62, roughnessFactor: 0.3 } },
  ],
  bufferViews: [
    { buffer: 0, byteOffset: positionOffset, byteLength: positionArray.byteLength, target: 34962 },
    { buffer: 0, byteOffset: normalOffset, byteLength: normalArray.byteLength, target: 34962 },
    { buffer: 0, byteOffset: indexOffset, byteLength: indexArray.byteLength, target: 34963 },
    { buffer: 0, byteOffset: animationTimeOffset, byteLength: animationTimes.byteLength },
    { buffer: 0, byteOffset: clockwiseOffset, byteLength: clockwiseRotations.byteLength },
    { buffer: 0, byteOffset: counterClockwiseOffset, byteLength: counterClockwiseRotations.byteLength },
  ],
  accessors,
  buffers: [{ byteLength: binaryLength }],
};

const jsonBuffer = Buffer.from(JSON.stringify(gltf), "utf8");
const jsonPadded = Buffer.concat([jsonBuffer, Buffer.alloc(align4(jsonBuffer.length) - jsonBuffer.length, 0x20)]);
const header = Buffer.alloc(12);
header.writeUInt32LE(0x46546c67, 0);
header.writeUInt32LE(2, 4);
header.writeUInt32LE(12 + 8 + jsonPadded.length + 8 + binary.length, 8);
const jsonHeader = Buffer.alloc(8);
jsonHeader.writeUInt32LE(jsonPadded.length, 0);
jsonHeader.writeUInt32LE(0x4e4f534a, 4);
const binHeader = Buffer.alloc(8);
binHeader.writeUInt32LE(binary.length, 0);
binHeader.writeUInt32LE(0x004e4942, 4);

const output = resolve(dirname(fileURLToPath(import.meta.url)), "..", "public", "models", "uav.glb");
mkdirSync(dirname(output), { recursive: true });
writeFileSync(output, Buffer.concat([header, jsonHeader, jsonPadded, binHeader, binary]));
console.log(`wrote ${output} (${vertexCount} vertices, ${indexArray.length / 3} triangles, ${binaryLength + jsonPadded.length} bytes)`);
