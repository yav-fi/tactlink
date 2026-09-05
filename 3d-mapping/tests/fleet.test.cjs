const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");
const Cesium = require("cesium");

// Load the actual TypeScript controllers without a DOM/WebGL context.
function loadSource(name) {
  const file = path.resolve(__dirname, "../src", `${name}.ts`);
  const code = ts.transpileModule(fs.readFileSync(file, "utf8"), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;
  const module = { exports: {} };
  new Function("require", "module", "exports", code)(
    (id) => id.startsWith("./") ? loadSource(id.slice(2)) : require(id), module, module.exports,
  );
  return module.exports;
}
const { Fleet, DRONE_COLORS } = loadSource("fleet");
const home = { latitude: 38.889, longitude: -77.036, altitude: 80 };

test("synchronized replay uses assigned speeds and stops at the last arrival", () => {
  const entities = new Cesium.EntityCollection();
  const fleet = new Fleet({ entities });
  const first = fleet.deploy(home), second = fleet.deploy(home);
  fleet.deploy(home); // No path: excluded from the completion timer.
  for (const drone of [first, second]) {
    drone.setManualControl(true);
    for (let i = 0; i < 240; i++) drone.moveManually(1, 0, 0, 1 / 60);
    // Turn and climb so playback must follow segments, not a straight shortcut.
    for (let i = 0; i < 120; i++) drone.moveManually(0, 1, 1, 1 / 60);
    drone.setManualControl(false);
  }
  first.speedMph = 30;
  second.speedMph = 60;
  const trail = entities.getById(`${first.id}_trail`).polyline.positions.getValue().map(p => Cesium.Cartesian3.clone(p));
  const length = trail.slice(1).reduce((sum, p, i) => sum + Cesium.Cartesian3.distance(trail[i], p), 0);
  const expectedDuration = length / (30 * 0.44704);
  fleet.startReplay(100);
  assert.equal(fleet.replay.total, 2);
  assert.equal(fleet.replay.elapsed, 0);
  assert(Math.abs(fleet.replay.duration - expectedDuration) < 1e-9);
  for (const drone of [first, second]) {
    const replay = entities.getById(`${drone.id}_replay`);
    assert(replay.box);
    assert.equal(replay.position.isConstant, false);
    assert(Cesium.Cartesian3.equals(replay.position.getValue(), trail[0]));
  }
  fleet.updateReplay(101);
  const distance = drone => Cesium.Cartesian3.distance(trail[0], entities.getById(`${drone.id}_replay`).position.getValue());
  assert(Math.abs(distance(first) - 30 * 0.44704) < 0.01);
  assert(Math.abs(distance(second) - 60 * 0.44704) < 0.01);
  fleet.updateReplay(100 + expectedDuration * 0.75);
  assert.equal(fleet.replay.arrived, 1);
  assert.equal(fleet.replay.running, true);
  assert(Cesium.Cartesian3.equalsEpsilon(entities.getById(`${second.id}_replay`).position.getValue(), trail.at(-1), 1e-12, 1e-8));
  // A delayed frame must land exactly, without adding the delay to the final time.
  fleet.updateReplay(100 + expectedDuration + 10);
  assert.equal(fleet.replay.running, false);
  assert.equal(fleet.replay.elapsed, expectedDuration);
  assert.equal(fleet.replay.arrived, 2);
  fleet.updateReplay(1000);
  assert.equal(fleet.replay.elapsed, expectedDuration);
  assert.deepEqual(entities.getById(`${first.id}_trail`).polyline.positions.getValue(), trail);
  fleet.startReplay(1001);
  assert(Cesium.Cartesian3.equals(entities.getById(`${first.id}_replay`).position.getValue(), trail[0]));
  fleet.stopReplay();
  assert(!entities.getById(`${first.id}_replay`));
  assert.equal(entities.getById(first.id).show, true);
  assert.deepEqual(entities.getById(`${first.id}_trail`).polyline.positions.getValue(), trail);
  fleet.clear();
  assert.equal(entities.values.length, 0);
});

test("empty and stationary routes do not start a timer", () => {
  const fleet = new Fleet({ entities: new Cesium.EntityCollection() });
  fleet.startReplay(0);
  assert.equal(fleet.replay.running, false);
  const drone = fleet.deploy(home);
  drone.setManualControl(true);
  drone.setManualControl(false);
  fleet.startReplay(10);
  assert.equal(fleet.replay.total, 0);
  fleet.updateReplay(100);
  assert.equal(fleet.replay.elapsed, 0);
});

test("60 mph equals 26.8224 meters per second on the georeferenced map", () => {
  function traveled(hz, diagonal = false) {
    const entities = new Cesium.EntityCollection();
    const drone = new Fleet({ entities }).deploy(home);
    assert.equal(drone.speedMph, 60);
    drone.setManualControl(true);
    for (let i = 0; i < hz * 3; i++) drone.moveManually(1, diagonal ? 1 : 0, 0, 1 / hz);
    const start = entities.getById(drone.id).position.getValue();
    for (let i = 0; i < hz; i++) drone.moveManually(1, diagonal ? 1 : 0, 0, 1 / hz);
    const finish = entities.getById(drone.id).position.getValue();
    return Cesium.Cartesian3.distance(start, finish);
  }
  for (const hz of [30, 60, 144]) {
    assert(Math.abs(traveled(hz) - 26.8224) < 0.01);
    assert(Math.abs(traveled(hz, true) - 26.8224) < 0.01);
  }
});

test("typed speeds are per-drone, validated and applied to real displacement", () => {
  const entities = new Cesium.EntityCollection();
  const fleet = new Fleet({ entities });
  const drone = fleet.deploy(home);
  const other = fleet.deploy(home);
  drone.speedMph = 120.5;
  assert.equal(other.speedMph, 60);
  for (const invalid of [NaN, Infinity, 0, -1]) assert.throws(() => { drone.speedMph = invalid; });
  assert.equal(drone.speedMph, 120.5);
  drone.setManualControl(true);
  for (let i = 0; i < 180; i++) drone.moveManually(0, 1, 0, 1 / 60);
  const start = entities.getById(drone.id).position.getValue();
  for (let i = 0; i < 60; i++) drone.moveManually(0, 1, 0, 1 / 60);
  assert(Math.abs(Cesium.Cartesian3.distance(start, entities.getById(drone.id).position.getValue()) - 120.5 * 0.44704) < 0.01);
});

test("sample mission accepts orbit/hover and rejects invalid speed overrides", () => {
  const { parseMission, sampleMission } = loadSource("mission");
  assert.deepEqual(parseMission(JSON.stringify(sampleMission)), sampleMission);
  assert.doesNotThrow(() => parseMission(JSON.stringify({ drone_id: "drone_1", mission: [{ action: "hover", duration_s: 2 }] })));
  assert.throws(() => parseMission(JSON.stringify({ drone_id: "drone_1", mission: [{ action: "return_home", speed_mps: -2 }] })));
});

test("empty startup, unique drone IDs and eight-color wraparound", () => {
  const entities = new Cesium.EntityCollection();
  const fleet = new Fleet({ entities });
  assert.equal(entities.values.length, 0);
  assert.equal(fleet.drones.size, 0);
  for (let i = 0; i < 9; i++) {
    const drone = fleet.deploy({ ...home, longitude: home.longitude + i * 0.001 });
    assert.equal(drone.id, `drone_${i + 1}`);
    const leader = entities.getById(drone.id);
    const expected = Cesium.Color.fromCssColorString(DRONE_COLORS[i % 8].hex);
    assert(Cesium.Color.equals(leader.polyline.material.color.getValue(), expected));
    assert.equal(entities.getById(`${drone.id}_trail`).show, false);
    const positions = leader.polyline.positions.getValue();
    assert(Math.abs(Cesium.Cartesian3.distance(...positions) - 3) < 0.0001);
  }
  assert.equal(fleet.drones.size, 9);
});

test("independent movement, release prisms, preserved routes and full cleanup", () => {
  const entities = new Cesium.EntityCollection();
  const fleet = new Fleet({ entities });
  const unrelated = entities.add({ id: "unrelated-map-marker" });
  const first = fleet.deploy(home);
  const second = fleet.deploy({ ...home, longitude: -77.03 });
  const secondStart = second.snapshot();
  first.setManualControl(true);
  for (let i = 0; i < 120; i++) first.moveManually(1, 0, 0, 1 / 60);
  first.setManualControl(false);
  assert.deepEqual(second.snapshot(), secondStart);
  const release = entities.getById(`${first.id}_release_1`);
  assert(release.box);
  assert.equal(release.ellipsoid, undefined);
  assert(Cesium.Cartesian3.equals(release.box.dimensions.getValue(), new Cesium.Cartesian3(16, 10, 4)));
  const trail = entities.getById(`${first.id}_trail`);
  const points = trail.polyline.positions.getValue().slice();
  first.setManualControl(true);
  assert.deepEqual(trail.polyline.positions.getValue(), points);
  for (let i = 0; i < 60; i++) first.moveManually(0, 1, 0, 1 / 60);
  assert(Cesium.Cartesian3.equals(trail.polyline.positions.getValue().at(-1), entities.getById(first.id).position.getValue()));
  second.setManualControl(true);
  second.moveManually(0, 1, 0, 0.1);
  second.setManualControl(false);
  assert(entities.getById(`${second.id}_release_1`));
  fleet.clear();
  assert.equal(fleet.drones.size, 0);
  assert.deepEqual(entities.values, [unrelated]);
  assert.equal(fleet.deploy(home).id, "drone_1");
});
