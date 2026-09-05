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

/** Vertical-axis disc (rotor) centred on `center`, normal +Y, drawn both faces. */
function disc(center, radius, segments = 24) {
  const [cx, cy, cz] = center;
  for (const [normal, order] of [[[0, 1, 0], 1], [[0, -1, 0], -1]]) {
    const hub = pushVertex([cx, cy, cz], normal);
    let previous = null;
    for (let step = 0; step <= segments; step += 1) {
      const angle = (step / segments) * Math.PI * 2 * order;
      const index = pushVertex([cx + Math.cos(angle) * radius, cy, cz + Math.sin(angle) * radius], normal);
      if (previous !== null) indices.push(hub, previous, index);
      previous = index;
    }
  }
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

// --- Airframe ---------------------------------------------------------------
// Nose points +X. Span is ~2.6 m so entity scale reads directly in metres.
beginPrimitive(MATERIAL_SHELL);
box([-0.85, -0.16, -0.30], [0.62, 0.20, 0.30]); // fuselage
box([-1.02, -0.10, -0.16], [-0.80, 0.12, 0.16]); // tail boom root
for (const [ax, az] of [[0.72, 0.72], [0.72, -0.72], [-0.72, 0.72], [-0.72, -0.72]]) {
  const x0 = Math.min(0, ax), x1 = Math.max(0, ax);
  const z0 = Math.min(0, az), z1 = Math.max(0, az);
  box([x0 - 0.07, -0.05, z0 - 0.07], [x1 + 0.07, 0.05, z1 + 0.07]); // arm
}
endPrimitive();

// Wedge nose: three faces converging on the tip so heading is readable head-on.
beginPrimitive(MATERIAL_ACCENT);
const tip = [1.05, 0.02, 0.0];
triangle(tip, [0.62, 0.20, 0.30], [0.62, 0.20, -0.30]);
triangle(tip, [0.62, -0.16, -0.30], [0.62, -0.16, 0.30]);
triangle(tip, [0.62, 0.20, -0.30], [0.62, -0.16, -0.30]);
triangle(tip, [0.62, -0.16, 0.30], [0.62, 0.20, 0.30]);
box([-1.30, -0.04, -0.04], [-1.00, 0.46, 0.04]); // vertical fin, breaks nose/tail symmetry
endPrimitive();

beginPrimitive(MATERIAL_DARK);
for (const [ax, az] of [[0.72, 0.72], [0.72, -0.72], [-0.72, 0.72], [-0.72, -0.72]]) {
  cylinder([ax, 0.10, az], 0.14, 0.22); // motor pod
}
box([-0.55, -0.42, -0.42], [0.35, -0.34, -0.34]); // port skid
box([-0.55, -0.42, 0.34], [0.35, -0.34, 0.42]); // starboard skid
box([-0.10, -0.34, -0.40], [0.02, -0.16, 0.40]); // skid brace
endPrimitive();

beginPrimitive(MATERIAL_ROTOR);
for (const [ax, az] of [[0.72, 0.72], [0.72, -0.72], [-0.72, 0.72], [-0.72, -0.72]]) {
  disc([ax, 0.24, az], 0.62);
}
endPrimitive();

// --- glTF assembly ----------------------------------------------------------
const positionArray = new Float32Array(positions);
const normalArray = new Float32Array(normals);
const indexArray = new Uint32Array(indices);
const align4 = (value) => (value + 3) & ~3;

const positionOffset = 0;
const normalOffset = align4(positionOffset + positionArray.byteLength);
const indexOffset = align4(normalOffset + normalArray.byteLength);
const binaryLength = align4(indexOffset + indexArray.byteLength);
const binary = Buffer.alloc(binaryLength);
Buffer.from(positionArray.buffer, positionArray.byteOffset, positionArray.byteLength).copy(binary, positionOffset);
Buffer.from(normalArray.buffer, normalArray.byteOffset, normalArray.byteLength).copy(binary, normalOffset);
Buffer.from(indexArray.buffer, indexArray.byteOffset, indexArray.byteLength).copy(binary, indexOffset);

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

const gltf = {
  asset: { version: "2.0", generator: "dnhacks26 generate-uav-glb" },
  scene: 0,
  scenes: [{ nodes: [0] }],
  nodes: [{ mesh: 0, name: "uav" }],
  meshes: [{ name: "uav", primitives: meshPrimitives }],
  materials: [
    { name: "shell", pbrMetallicRoughness: { baseColorFactor: [0.78, 0.83, 0.88, 1], metallicFactor: 0.25, roughnessFactor: 0.45 } },
    { name: "accent", pbrMetallicRoughness: { baseColorFactor: [1, 1, 1, 1], metallicFactor: 0.1, roughnessFactor: 0.35 }, emissiveFactor: [0.35, 0.38, 0.42], doubleSided: true },
    { name: "rotor", pbrMetallicRoughness: { baseColorFactor: [0.85, 0.9, 0.95, 0.28], metallicFactor: 0, roughnessFactor: 0.9 }, alphaMode: "BLEND", doubleSided: true },
    { name: "dark", pbrMetallicRoughness: { baseColorFactor: [0.14, 0.16, 0.19, 1], metallicFactor: 0.5, roughnessFactor: 0.5 } },
  ],
  bufferViews: [
    { buffer: 0, byteOffset: positionOffset, byteLength: positionArray.byteLength, target: 34962 },
    { buffer: 0, byteOffset: normalOffset, byteLength: normalArray.byteLength, target: 34962 },
    { buffer: 0, byteOffset: indexOffset, byteLength: indexArray.byteLength, target: 34963 },
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
