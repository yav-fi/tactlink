const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");
const Cesium = require("cesium");
function loadSource(name) {
  const source = fs.readFileSync(path.resolve(__dirname, "../src", `${name}.ts`), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
  const module = { exports: {} };
  new Function("require", "module", "exports", code)(id => id.startsWith("./") ? loadSource(id.slice(2)) : require(id), module, module.exports);
  return module.exports;
}
const { placePhones, PhoneControl } = loadSource("phone-control");
const { applyPhoneAction } = loadSource("phone-flight");
const { Fleet } = loadSource("fleet");
const alignment = { anchor: "0", rotation: 0, mirror: false };
const phone = (id, extra = {}) => ({ id: String(id), name: `Phone ${id}`, room: "room", pos: [id * 3, 0], z: id,
  heading: 0, compassValid: true, gesture: "Thumb_Up", confidence: 0.95, flat: false, geometryAge: 0, age: 0, ...extra });

test("three-phone flat and five-phone XYZ placement preserve measured relative metres", () => {
  for (const count of [3, 5]) {
    const input = Array.from({ length: count }, (_, i) => phone(i, { flat: count === 3 }));
    const placed = placePhones(input, { ...alignment, rotation: 90, mirror: true });
    assert.equal(placed.length, count);
    assert.equal(placed[0].position.y, 5);
    assert(Math.abs(placed[1].position.y - 8) < 1e-8);
    assert.equal(placed[1].position.z, count === 3 ? 0 : 1);
    assert.equal(placed[1].heading, 0, "compass facing is independent of UWB axis calibration");
  }
});

test("missing anchor, stale geometry and different rooms never invent a formation", () => {
  assert.deepEqual(placePhones([phone(0, { pos: null })], alignment), []);
  assert.deepEqual(placePhones([phone(0, { age: 2 })], alignment), []);
  const placed = placePhones([phone(0), phone(1, { room: "other" }), phone(2, { geometryAge: 9 })], alignment);
  assert.equal(placed.length, 1);
});

test("only the nearest phone commands the actual drone after a deliberate hold", () => {
  const control = new PhoneControl();
  const operators = placePhones([phone(0), phone(1, { gesture: "Thumb_Down" }), phone(2)], alignment);
  const drone = { x: 3, y: 5 };
  assert.equal(control.update(operators, drone, 0).action, undefined);
  const command = control.update(operators, drone, 400);
  assert.equal(command.operator.operator_id, "1");
  assert.equal(command.action, "land");
  assert.equal(operators.filter(operator => operator.controls.length).length, 0, "selection never imports backend-drone assignments");
});

test("handoff resets gesture hold; release, confidence loss and stale owner stop motion", () => {
  const control = new PhoneControl();
  let operators = placePhones([phone(0), phone(1)], alignment);
  control.update(operators, { x: 0, y: 5 }, 0);
  assert.equal(control.update(operators, { x: 0, y: 5 }, 400).action, "takeoff");
  const handoff = control.update(operators, { x: 3, y: 5 }, 500);
  assert(handoff.stop);
  assert.equal(handoff.action, undefined);
  assert.equal(control.update(operators, { x: 3, y: 5 }, 900).action, "takeoff");
  operators = placePhones([phone(0), phone(1, { confidence: 0.2 })], alignment);
  assert(control.update(operators, { x: 3, y: 5 }, 1000).stop);
  assert.equal(control.update(operators.map(p => ({ ...p, age: 2 })), { x: 3, y: 5 }, 1100).operator, undefined);
});

test("phone camera labels move the production browser drone and stop it on release", () => {
  const home = { latitude: 38.889, longitude: -77.036, altitude: 80 };
  const fleet = new Fleet({ entities: new Cesium.EntityCollection() });
  const drone = fleet.deploy(home);
  const control = new PhoneControl();
  let operators = placePhones([phone(0, { gesture: "Three_Finger_Forward", heading: 0 }), phone(1), phone(2)], alignment);
  control.update(operators, { x: 0, y: 0 }, 0);
  const command = control.update(operators, { x: 0, y: 0 }, 400);
  applyPhoneAction(drone, command.action, command.operator, home, { x: 0, y: 0, z: 0 });
  for (let i = 0; i < 60; i++) drone.update(1 / 60);
  assert(drone.snapshot().longitude > home.longitude, "east-facing operator flies east");
  assert.equal(fleet.drones.size, 1);
  operators = operators.map(operator => ({ ...operator, gesture: "None" }));
  if (control.update(operators, { x: 0, y: 0 }, 1400).stop) drone.stopCommand();
  const stopped = drone.snapshot().longitude;
  for (let i = 0; i < 60; i++) drone.update(1 / 60);
  assert.equal(drone.snapshot().longitude, stopped);
});

test("phone takeoff ceiling prevents continued ascent", () => {
  const home = { latitude: 38.889, longitude: -77.036, altitude: 80 };
  const drone = new Fleet({ entities: new Cesium.EntityCollection() }).deploy(home);
  const [owner] = placePhones([phone(0)], alignment);
  applyPhoneAction(drone, "takeoff", owner, home, { x: 0, y: 0, z: 0 });
  drone.update(0.1);
  assert(drone.snapshot().altitude > home.altitude);
  applyPhoneAction(drone, "takeoff", owner, home, { x: 0, y: 0, z: 10 });
  const altitude = drone.snapshot().altitude;
  drone.update(0.1);
  assert.equal(drone.snapshot().altitude, altitude);
});

test("phone return-home gesture starts actual flight instead of only recording a route", () => {
  const home = { latitude: 38.889, longitude: -77.036, altitude: 80 };
  const start = { ...home, longitude: home.longitude + 0.0001, altitude: 82 };
  const drone = new Fleet({ entities: new Cesium.EntityCollection() }).deploy(start);
  const [owner] = placePhones([phone(0)], alignment);
  applyPhoneAction(drone, "return_home", owner, home, { x: 10, y: 0, z: 2 });
  for (let i = 0; i < 60; i++) drone.update(1 / 60);
  assert(drone.snapshot().longitude < start.longitude);
});

test("two-phone assumed-axis positions preserve the one distance and enter gesture control", () => {
  const phones = [phone(0, { pos: [0, -2], z: 0, flat: true, twoPhone: true }), phone(1, { pos: [0, 2], z: 0, flat: true, twoPhone: true })];
  const placed = placePhones(phones, alignment);
  assert.equal(placed.length, 2);
  assert.equal(placed[1].position.y - placed[0].position.y, 4);
  assert(placed.every(p => p.position.x === 0 && p.position.z === 0));
  const control = new PhoneControl();
  control.update(placed, { x: 0, y: 0 }, 0);
  assert.equal(control.update(placed, { x: 0, y: 0 }, 400).action, "takeoff");
  assert.equal(placePhones([...phones, phone(2, { flat: true })], alignment).length, 2);
});
