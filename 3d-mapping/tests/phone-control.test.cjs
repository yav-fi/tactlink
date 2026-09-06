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
  new Function("require", "module", "exports", code)(id => id.startsWith(".") ? loadSource(path.join(path.dirname(name), id)) : require(id), module, module.exports);
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


test("metre-scale people keep measured spacing and stand 1.75 m tall", () => {
  const { GeoFrame } = loadSource("runtime/frame");
  const { OperatorLayer } = loadSource("runtime/layers/operators");
  const frame = new GeoFrame(); frame.update(38.889, -77.036, 80);
  const viewer = { entities: new Cesium.EntityCollection() };
  const layer = new OperatorLayer(viewer, frame, () => undefined);
  layer.setOptions({ humanModels: true });
  const operators = placePhones([phone(0, { flat: true }), phone(1, { flat: true })], alignment);
  layer.update({ operators });
  const time = Cesium.JulianDate.now();
  const head = viewer.entities.getById("runtime-human-0-head");
  const leg = viewer.entities.getById("runtime-human-0-leg-left");
  const headZ = frame.toLocal(head.position.getValue(time)).z + head.ellipsoid.radii.getValue(time).z;
  const footZ = frame.toLocal(leg.position.getValue(time)).z - leg.ellipsoid.radii.getValue(time).z;
  assert(Math.abs(headZ - 1.75) < 1e-6);
  assert(Math.abs(footZ) < 1e-6);
  assert(Math.abs(Cesium.Cartesian3.distance(layer.positionOf("0"), layer.positionOf("1")) - 3) < 1e-6);
  assert.equal(viewer.entities.getById("runtime-operator-0").billboard.show.getValue(time), false);
  layer.clear(); assert.equal(viewer.entities.values.length, 0);
});

test("phone flight never descends through human models or exceeds the hover band", () => {
  const { phoneFlightBand, constrainPhoneHeight } = loadSource("phone-flight");
  const home = { latitude: 38.889, longitude: -77.036, altitude: 80 };
  const drone = new Fleet({ entities: new Cesium.EntityCollection() }).deploy({ ...home, altitude: 83 });
  const [owner] = placePhones([phone(0)], alignment);
  const band = phoneFlightBand([{ position: { x: 0, y: 0, z: 1 } }]);
  for (let i = 0; i < 240; i++) {
    applyPhoneAction(drone, "land", owner, home, { x: 0, y: 0, z: drone.snapshot().altitude - home.altitude }, band);
    drone.update(1 / 60); constrainPhoneHeight(drone, home, band);
    assert(drone.snapshot().altitude >= home.altitude + band.floor);
  }
  drone.applyManualMove({ ...home, altitude: 100 });
  constrainPhoneHeight(drone, home, band);
  assert.equal(drone.snapshot().altitude, home.altitude + band.ceiling);
});

test("close camera and drone model use physical metres without screen-size enlargement", () => {
  const { GeoFrame } = loadSource("runtime/frame");
  const { startPhoneScene, phoneCameraFrame, PHONE_DRONE_SCALE } = loadSource("phone-scene");
  const frame = new GeoFrame(); frame.update(38.889, -77.036, 80);
  const render = new Cesium.Event();
  let cameraRange;
  const viewer = { entities: new Cesium.EntityCollection(), scene: { preRender: render },
    canvas: { style: {}, addEventListener() {}, removeEventListener() {} },
    camera: { lookAt(center, offset) { cameraRange = offset.range; }, lookAtTransform() {} } };
  const drone = new Fleet(viewer).deploy({ latitude: 38.889, longitude: -77.036, altitude: 83.5 });
  const scene = startPhoneScene(viewer, drone, frame);
  const model = viewer.entities.getById(drone.id).model;
  const now = Cesium.JulianDate.now();
  assert.equal(model.minimumPixelSize.getValue(now), 0);
  assert.equal(model.scale.getValue(now), PHONE_DRONE_SCALE);
  assert(Math.abs(PHONE_DRONE_SCALE * (1.48 + 2 * Math.hypot(0.52, 0.035)) - 0.8) < 1e-9);
  const points = [frame.toFixed({ x: 0, y: 0, z: 0 }), frame.toFixed({ x: 0, y: 5, z: 1.75 }), drone.cameraPosition];
  assert(phoneCameraFrame(points).range >= 12 && phoneCameraFrame(points).range < 20);
  render.raiseEvent(); assert(cameraRange < 20);
  scene.dispose(); assert.equal(render.numberOfListeners, 0);
});

test("any phone can claim follow with a fist, release keeps following, and another fist switches target", () => {
  const control = new PhoneControl();
  let people = placePhones([phone(0, { gesture: "None" }), phone(1, { gesture: "Closed_Fist" })], alignment);
  const drone = { x: 0, y: 5 }; // phone 0 is closest, but phone 1 requests follow.
  assert.equal(control.update(people, drone, 0).action, undefined);
  let result = control.update(people, drone, 400);
  assert.equal(result.operator.operator_id, "1"); assert.equal(result.action, "follow");
  people = people.map(p => ({ ...p, gesture: "None" }));
  result = control.update(people, drone, 500);
  assert.equal(result.operator.operator_id, "1"); assert.equal(result.action, "follow"); assert(!result.stop);
  people[0].gesture = "Closed_Fist";
  control.update(people, { x: 3, y: 5 }, 600);
  result = control.update(people, { x: 3, y: 5 }, 1000);
  assert.equal(result.operator.operator_id, "0"); assert.equal(result.action, "follow"); assert(result.stop);
});

test("a continuously held fist does not steal follow back; another person's palm cancels it", () => {
  const control = new PhoneControl();
  const people = placePhones([phone(0, { gesture: "Closed_Fist" }), phone(1, { gesture: "None" })], alignment);
  const drone = { x: 0, y: 5 };
  control.update(people, drone, 0); control.update(people, drone, 400);
  people[1].gesture = "Closed_Fist";
  control.update(people, drone, 500); control.update(people, drone, 900);
  assert.equal(control.update(people, drone, 1000).operator.operator_id, "1");
  people[0].gesture = "Open_Palm";
  control.update(people, drone, 1100);
  const stop = control.update(people, drone, 1500);
  assert(stop.stop); assert.equal(stop.action, "halt"); assert.equal(control.following, "");
  assert.notEqual(control.update(people, drone, 1600).action, "follow");
});

test("lost follow position stops without silently following the remaining phone", () => {
  const control = new PhoneControl();
  const people = placePhones([phone(0, { gesture: "None" }), phone(1, { gesture: "Closed_Fist" })], alignment);
  control.update(people, { x: 0, y: 0 }, 0); control.update(people, { x: 0, y: 0 }, 400);
  const lost = control.update([people[0]], { x: 0, y: 0 }, 500);
  assert(lost.stop); assert.equal(lost.action, undefined); assert.equal(control.following, "");
});

test("a phone-visible fist claims from the farther person despite a brief detection dropout", () => {
  const control = new PhoneControl();
  const people = placePhones([phone(0, { gesture: "None" }), phone(1, { gesture: "Closed_Fist", confidence: 0.6 })], alignment);
  const drone = { x: 0, y: 5 };
  control.update(people, drone, 0);
  control.update(people, drone, 200);
  assert.equal(control.claimProgress("1"), 0.5);
  people[1].gesture = "None";
  assert.notEqual(control.update(people, drone, 300).action, "follow");
  people[1].gesture = "Closed_Fist";
  assert.notEqual(control.update(people, drone, 400).action, "follow", "missing frames don't complete the hold");
  const result = control.update(people, drone, 600);
  assert.equal(result.action, "follow"); assert.equal(result.operator.operator_id, "1");
});

test("follow survives one wrong gesture but a deliberate different command still works", () => {
  const control = new PhoneControl();
  const people = placePhones([phone(0, { gesture: "None" }), phone(1, { gesture: "Closed_Fist" })], alignment);
  const drone = { x: 0, y: 5 };
  control.update(people, drone, 0); control.update(people, drone, 400);
  people[1].gesture = "Thumb_Up";
  let result = control.update(people, drone, 500);
  assert.equal(result.action, "follow"); assert.equal(result.operator.operator_id, "1");
  people[1].gesture = "None";
  assert.equal(control.update(people, drone, 600).action, "follow");
  people[1].gesture = "Thumb_Up";
  assert.equal(control.update(people, drone, 700).action, "follow");
  result = control.update(people, drone, 1100);
  assert.equal(result.action, "takeoff"); assert.equal(result.operator.operator_id, "1"); assert(result.stop);
});

test("a losing held fist cannot steal back after flicker; a released and repeated fist can", () => {
  const control = new PhoneControl();
  const people = placePhones([phone(0, { gesture: "Closed_Fist" }), phone(1, { gesture: "None" })], alignment);
  const drone = { x: 0, y: 5 };
  control.update(people, drone, 0); control.update(people, drone, 400);
  people[1].gesture = "Closed_Fist";
  control.update(people, drone, 500); control.update(people, drone, 900);
  people[0].gesture = "Thumb_Up";
  control.update(people, drone, 1000);
  people[0].gesture = "Closed_Fist";
  control.update(people, drone, 1100);
  assert.equal(control.update(people, drone, 1500).operator.operator_id, "1");
  people[0].gesture = "None";
  control.update(people, drone, 1600);
  people[0].gesture = "Closed_Fist";
  control.update(people, drone, 1900);
  assert.equal(control.update(people, drone, 2300).operator.operator_id, "0");
});

test("a long dropout discards an incomplete fist hold", () => {
  const control = new PhoneControl();
  const people = placePhones([phone(0, { gesture: "Closed_Fist" })], alignment);
  const drone = { x: 0, y: 5 };
  control.update(people, drone, 0); control.update(people, drone, 200);
  people[0].gesture = "None";
  control.update(people, drone, 300);
  people[0].gesture = "Closed_Fist";
  assert.notEqual(control.update(people, drone, 600).action, "follow");
  assert.notEqual(control.update(people, drone, 900).action, "follow");
  assert.equal(control.update(people, drone, 1000).action, "follow");
});

test("fist caller retains up/down control even while the other phone remains nearer", () => {
  const control = new PhoneControl();
  const people = placePhones([phone(0, { gesture: "Thumb_Up" }), phone(1, { gesture: "Closed_Fist" })], alignment);
  const drone = { x: 0, y: 5 };
  control.update(people, drone, 0); control.update(people, drone, 400);
  people[1].gesture = "Pointing_Up";
  control.update(people, drone, 500);
  let result = control.update(people, drone, 900);
  assert.equal(result.action, "takeoff"); assert.equal(result.operator.operator_id, "1");
  result = control.update(people, drone, 1000);
  assert.equal(result.action, "takeoff"); assert.equal(result.operator.operator_id, "1");
  people[1].gesture = "Pointing_Down";
  control.update(people, drone, 1100);
  result = control.update(people, drone, 1500);
  assert.equal(result.action, "land"); assert.equal(result.operator.operator_id, "1");
  people[1].gesture = "None";
  result = control.update(people, drone, 1600);
  assert.equal(result.action, undefined); assert(result.stop);
  people[0].gesture = "Open_Palm";
  control.update(people, drone, 1700);
  assert.equal(control.update(people, drone, 2100).action, "halt");
});

test("follow flies above the selected person, settles, and tracks a changed mapped position", () => {
  const { GeoFrame } = loadSource("runtime/frame");
  const home = { latitude: 38.889, longitude: -77.036, altitude: 80 };
  const frame = new GeoFrame(); frame.update(home.latitude, home.longitude, home.altitude);
  const drone = new Fleet({ entities: new Cesium.EntityCollection() }).deploy({ ...home, altitude: 83.5 });
  const [owner] = placePhones([phone(0, { gesture: "Closed_Fist" })], alignment);
  function advance() {
    for (let i = 0; i < 720; i++) {
      if (i % 6 === 0) applyPhoneAction(drone, "follow", owner, home, frame.toLocal(drone.cameraPosition));
      drone.update(1 / 60);
    }
    return frame.toLocal(drone.cameraPosition);
  }
  let end = advance();
  assert(Math.hypot(end.x - owner.position.x, end.y - owner.position.y) < 0.2);
  assert(Math.abs(end.z - 3.5) < 0.1);
  owner.position.x += 3;
  end = advance();
  assert(Math.hypot(end.x - owner.position.x, end.y - owner.position.y) < 0.2);
  const stopped = drone.snapshot(); drone.update(1 / 60);
  assert.deepEqual(drone.snapshot(), stopped);
});


test("drag rotates the close phone camera without changing range or jumping back to the monument", () => {
  const { GeoFrame } = loadSource("runtime/frame");
  const { startPhoneScene } = loadSource("phone-scene");
  const frame = new GeoFrame(); frame.update(38.889, -77.036, 80);
  const render = new Cesium.Event(), events = new Map();
  let pose, captured;
  const canvas = { style: { touchAction: "auto" }, clientHeight: 800,
    addEventListener(name, handler) { events.set(name, handler); }, removeEventListener(name) { events.delete(name); },
    setPointerCapture(id) { captured = id; }, hasPointerCapture(id) { return captured === id; }, releasePointerCapture() { captured = undefined; } };
  const viewer = { entities: new Cesium.EntityCollection(), scene: { preRender: render }, canvas,
    camera: { lookAt(center, offset) { pose = { center: Cesium.Cartesian3.clone(center), ...offset }; }, lookAtTransform() {} } };
  const drone = new Fleet(viewer).deploy({ latitude: 38.889, longitude: -77.036, altitude: 83.5 });
  const scene = startPhoneScene(viewer, drone, frame);
  render.raiseEvent(); const before = pose;
  const event = { pointerId: 1, button: 0, clientX: 100, clientY: 100, preventDefault() {} };
  events.get("pointerdown")(event);
  events.get("pointermove")({ ...event, clientX: 180, clientY: 125 });
  render.raiseEvent();
  assert.equal(pose.range, before.range);
  assert.deepEqual(pose.center, before.center);
  assert.notEqual(pose.heading, before.heading);
  assert(pose.range < 20);
  events.get("pointerup")(event);
  const afterDrag = pose;
  events.get("pointermove")({ ...event, clientX: 400 }); render.raiseEvent();
  assert.equal(pose.heading, afterDrag.heading);
  events.get("wheel")({ deltaMode: 0, deltaY: -100, preventDefault() {} }); render.raiseEvent();
  assert(pose.range < afterDrag.range); assert.deepEqual(pose.center, afterDrag.center);
  scene.dispose(); assert.equal(events.size, 0); assert.equal(canvas.style.touchAction, "auto");
});
