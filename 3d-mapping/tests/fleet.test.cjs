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
const { blendHeading, fleetCameraFrame, screenRelativeMovement } = loadSource("cinematic-camera");
const { MOTION_TRACE_LIFETIME_MS, motionTraceAlpha } = loadSource("motion-trace");
const home = { latitude: 38.889, longitude: -77.036, altitude: 80 };

test("cinematic camera framing expands to keep the whole fleet visible", () => {
  const center = Cesium.Cartesian3.fromDegrees(home.longitude, home.latitude, home.altitude);
  const frame = Cesium.Transforms.eastNorthUpToFixedFrame(center);
  const near = Cesium.Matrix4.multiplyByPoint(frame, new Cesium.Cartesian3(-50, 0, 0), new Cesium.Cartesian3());
  const far = Cesium.Matrix4.multiplyByPoint(frame, new Cesium.Cartesian3(50, 0, 0), new Cesium.Cartesian3());
  const single = fleetCameraFrame([center]);
  const fleet = fleetCameraFrame([near, far]);
  assert.equal(fleetCameraFrame([]), undefined);
  assert.equal(single.range, 32);
  assert(fleet.range > single.range);
  assert(Cesium.Cartesian3.distance(fleet.center, center) < 0.01);
});

test("WASD movement follows the camera's screen axes", () => {
  const position = Cesium.Cartesian3.fromDegrees(home.longitude, home.latitude, home.altitude);
  const frame = Cesium.Transforms.eastNorthUpToFixedFrame(position);
  const east = Cesium.Matrix4.multiplyByPointAsVector(frame, Cesium.Cartesian3.UNIT_X, new Cesium.Cartesian3());
  const north = Cesium.Matrix4.multiplyByPointAsVector(frame, Cesium.Cartesian3.UNIT_Y, new Cesium.Cartesian3());
  const forward = screenRelativeMovement(position, north, east, 1, 0);
  const right = screenRelativeMovement(position, north, east, 0, 1);
  assert(Math.abs(forward.east) < 1e-9 && Math.abs(forward.north - 1) < 1e-9);
  assert(Math.abs(right.east - 1) < 1e-9 && Math.abs(right.north) < 1e-9);
});

test("cinematic heading takes the shortest turn behind a flying drone", () => {
  const almostLeft = Cesium.Math.toRadians(179);
  const almostRight = Cesium.Math.toRadians(-179);
  const blended = blendHeading(almostLeft, almostRight, 0.5);
  assert(Math.abs(Cesium.Math.toDegrees(blended) - 180) < 0.001);
});

test("motion trace fades quickly to transparent", () => {
  assert.equal(motionTraceAlpha(0), 0.72);
  assert(motionTraceAlpha(MOTION_TRACE_LIFETIME_MS / 2) < 0.3);
  assert.equal(motionTraceAlpha(MOTION_TRACE_LIFETIME_MS), 0);
});

test("first manual-flight undo keeps hidden render geometry at valid geographic positions", () => {
  for (const type of ["normal", "survey"]) {
    const entities = new Cesium.EntityCollection();
    const fleet = new Fleet({ entities });
    let drone = fleet.deploy(home, type);
    for (let cycle = 0; cycle < 3; cycle++) {
      fleet.checkpoint("manual flight");
      drone.setManualControl(true);
      for (let i = 0; i < 90; i++) drone.moveManually(1, 0, 0, 1 / 60);
      drone.setManualControl(false);
      fleet.undo(); drone = fleet.drones.get(drone.id);
      assert.deepEqual(drone.capture().points, []);
      for (const suffix of ["", "_origin", "_crash"]) {
        const position = entities.getById(`${drone.id}${suffix}`).position.getValue();
        assert(position, `${suffix} has a defined position`);
        assert(Cesium.Cartographic.fromCartesian(position), `${suffix} must not be centered at Earth's origin`);
      }
      const trail = entities.getById(`${drone.id}_trail`);
      assert.equal(trail.show, false);
      assert(trail.polyline.positions.getValue().every(point => Cesium.Cartographic.fromCartesian(point)));
      assert.equal(drone.snapshot().longitude, home.longitude);
    }
    drone.setManualControl(true);
    drone.moveManually(1, 0, 0, 0.1);
    assert(drone.snapshot().longitude > home.longitude);
    fleet.clear(); assert.equal(entities.values.length, 0);
  }
});

test("propeller blades rotate over time while hubs stay fixed", () => {
  const { addQuadcopterParts } = loadSource("quadcopter");
  let time = 0;
  const parts = addQuadcopterParts({ entities: new Cesium.EntityCollection() }, "test", () => Cesium.Cartesian3.ZERO, () => Cesium.Quaternion.IDENTITY, () => Cesium.Color.CYAN, () => time);
  const blades = parts.filter(p => p.id.includes("_prop_"));
  const fixed = parts.filter(p => !p.id.includes("_prop_"));
  const before = parts.map(p => p.orientation.getValue());
  const centers = blades.map(p => p.position.getValue());
  time = 0.02;
  for (const part of blades) assert(!Cesium.Quaternion.equals(part.orientation.getValue(), before[parts.indexOf(part)]));
  for (const part of fixed) assert(Cesium.Quaternion.equals(part.orientation.getValue(), before[parts.indexOf(part)]));
  blades.forEach((part, i) => assert.deepEqual(part.position.getValue(), centers[i]));
});

test("four-rotor geometry follows body during flight and playback and shares color", () => {
  const entities = new Cesium.EntityCollection();
  const fleet = new Fleet({ entities });
  const drone = fleet.deploy(home);
  const parts = entities.values.filter(e => e.id.includes("_quad_"));
  assert.equal(parts.filter(e => e.id.includes("_hub_")).length, 4);
  assert.equal(parts.filter(e => e.id.includes("_prop_")).length, 8);
  drone.setManualControl(true);
  for (let i = 0; i < 120; i++) drone.moveManually(1, 0, 0, 1 / 60, 1);
  const check = center => {
    for (const part of parts) assert(Cesium.Cartesian3.distance(center, part.position.getValue()) < 0.5);
  };
  check(entities.getById(drone.id).position.getValue());
  drone.setManualControl(false);
  fleet.startReplay(0); fleet.updateReplay(0.5);
  check(entities.getById(`${drone.id}_replay`).position.getValue());
  drone.setColor("#ff6666");
  assert(parts.every(p => p.box.material.color.getValue().toCssHexString() === "#ff6666"));
  fleet.clear(); assert.equal(entities.values.length, 0);
});

test("Survey effect weakens beyond 100, 250 and 500 meters", () => {
  const { surveyStrength, surveyBand } = loadSource("survey-surface");
  assert.deepEqual([0, 100, 101, 250, 251, 500, 501, 10000].map(surveyStrength), [1, 1, 0.6, 0.6, 0.3, 0.3, 0.12, 0.12]);
  const origin = new Cesium.Cartesian3();
  const triangle = [origin, new Cesium.Cartesian3(1000, 0, 0), new Cesium.Cartesian3(0, 1000, 0)];
  const near = surveyBand(triangle, origin, 0, 100);
  assert.equal(near.length, 3);
  assert(near.every(p => Cesium.Cartesian3.distance(origin, p) <= 100.00001));
  assert.equal(surveyBand(near, origin, 250, 500).length, 0);
});

test("undo restores deployed types, paths, coverage, groups and release markers after reset", () => {
  const entities = new Cesium.EntityCollection();
  const fleet = new Fleet({ entities });
  fleet.checkpoint("deployment");
  const drone = fleet.deploy(home, "survey");
  fleet.takeBatch([drone.id], 75);
  for (let i = 0; i < 120; i++) fleet.moveBatch(1, 0, 0, 1 / 60, 0);
  fleet.releaseBatch();
  fleet.saveGroup("A", [drone.id]);
  const before = drone.capture();
  assert.equal(before.coverage.length, 0); // No map surfaces in this controller-only fixture.
  fleet.checkpoint("reset all");
  fleet.clear(); assert.equal(entities.values.length, 0);
  assert.equal(fleet.undo(), "reset all");
  const restored = fleet.drones.get(drone.id);
  assert.equal(restored.droneType, "survey");
  assert.equal(restored.speedMph, 75);
  assert.deepEqual(restored.capture().points, before.points);
  assert.deepEqual(restored.capture().position, before.position);
  assert.equal(restored.coverageCount, before.coverage.length);
  assert(entities.getById(`${drone.id}_release_1`));
  assert.equal(fleet.batchMarkers.size, 1);
  assert.deepEqual(fleet.groups.get("A").ids, [drone.id]);
  fleet.checkpoint("speed"); restored.speedMph = 10;
  fleet.undo(); assert.equal(fleet.drones.get(drone.id).speedMph, 75);
  fleet.undo(); assert.equal(fleet.drones.size, 0);
  assert.equal(entities.values.length, 0);
});

test("pause freezes playback position and elapsed time and resume excludes paused time", () => {
  const entities = new Cesium.EntityCollection();
  const fleet = new Fleet({ entities });
  const drone = fleet.deploy(home);
  fleet.commandGroup([drone.id], { ...home, longitude: home.longitude + 0.01 }, 30, 100);
  fleet.togglePause(101);
  assert.equal(fleet.paused, true);
  const before = drone.snapshot();
  fleet.updateReplay(1000);
  assert.equal(fleet.replay.elapsed, 1);
  assert.deepEqual(drone.snapshot(), before);
  fleet.togglePause(1000);
  fleet.updateReplay(1001);
  assert.equal(fleet.replay.elapsed, 2);
  assert.notEqual(drone.snapshot().longitude, before.longitude);
  fleet.togglePause(1001); fleet.stopReplay();
  assert.equal(fleet.paused, false);
  assert.equal(fleet.replay.running, false);
});

test("Survey rays extend to first surfaces and leave uncovered rays unmarked", () => {
  const { sampleSurvey, SURVEY_MAX_RANGE } = loadSource("survey-surface");
  const origin = new Cesium.Cartesian3(0, 0, 100);
  const result = sampleSurvey(origin, new Cesium.Cartesian3(0, 0, -1), ray => Cesium.Ray.getPoint(ray, -ray.origin.z / ray.direction.z));
  assert.equal(result.hits.length, 12);
  for (const sample of result.hits) assert(Math.abs(sample.hit.z) < 1e-6);
  const empty = sampleSurvey(origin, new Cesium.Cartesian3(0, 0, -1), () => undefined);
  assert.equal(empty.centerHit, undefined);
  assert(empty.hits.every(item => !item.hit && Math.abs(Cesium.Cartesian3.distance(origin, item.end) - SURVEY_MAX_RANGE) < 1e-6));
});

test("Survey mesh uses underside mount and records only surface hits", () => {
  const entities = new Cesium.EntityCollection();
  let hits = false;
  const viewer = { entities, scene: { globe: { show: false }, pickFromRay: (ray, excluded, width) => {
    assert.equal(excluded, entities.values);
    return width === 0.1 && hits ? { position: Cesium.Ray.getPoint(ray, 120) } : undefined;
  } } };
  const fleet = new Fleet(viewer);
  const survey = fleet.deploy(home, "survey");
  survey.setManualControl(true); survey.moveManually(0, 1, 0, 0.1);
  survey.updateSurvey(0);
  assert.equal(survey.coverageCount, 0);
  hits = true; survey.updateSurvey(500);
  assert.equal(survey.coverageCount, 12);
  const side = entities.getById(`${survey.id}_survey_cone_0`);
  const points = side.polygon.hierarchy.getValue().positions;
  const body = entities.getById(survey.id).position.getValue();
  const mount = Cesium.Matrix4.multiplyByPoint(Cesium.Transforms.eastNorthUpToFixedFrame(body), new Cesium.Cartesian3(0, 0, -2), new Cesium.Cartesian3());
  assert(Cesium.Cartesian3.distance(points[0], mount) < 1e-6);
  assert(Math.abs(Cesium.Cartesian3.distance(points[0], points[1]) - 100) < 1e-6);
  const patch = entities.getById(`${survey.id}_coverage_1`);
  assert(Math.abs(patch.polygon.material.color.getValue().alpha - 0.22 * 0.6) < 1e-6);
  survey.setColor("#ff6666");
  assert.equal(side.polygon.material.color.getValue().withAlpha(1).toCssHexString(), "#ff6666");
  assert(Math.abs(patch.polygon.material.color.getValue().alpha - 0.22 * 0.6) < 1e-6);
  fleet.checkpoint("reset coverage");
  fleet.clear(); fleet.undo();
  assert.equal(fleet.drones.get(survey.id).coverageCount, 12);
  assert(Math.abs(entities.getById(`${survey.id}_coverage_1`).polygon.material.color.getValue().alpha - 0.22 * 0.6) < 1e-6);
  fleet.clear(); assert.equal(entities.values.length, 0);
});

test("both drone types report when no safe avoidance route exists and reset clears it", () => {
  for (const type of ["normal", "survey"]) {
    const entities = new Cesium.EntityCollection();
    const viewer = { entities, scene: { globe: { show: false }, pickFromRay: ray => ({ position: Cesium.Ray.getPoint(ray, 1) }) } };
    const drone = new Fleet(viewer).deploy(home, type);
    const marker = entities.getById(`${drone.id}_crash`);
    assert.equal(marker.point.show.getValue(), false);
    drone.setManualControl(true);
    drone.moveManually(1, 0, 0, 0.1);
    assert.equal(marker.point.show.getValue(), true);
    assert.equal(marker.label.show.getValue(), true);
    assert.equal(marker.label.text.getValue(), "⚠ NO SAFE ROUTE");
    assert(Cesium.Cartesian3.distance(marker.position.getValue(), entities.getById(drone.id).position.getValue()) < 1e-6);
    drone.reset();
    assert.equal(marker.point.show.getValue(), false);
    drone.destroy(); assert.equal(entities.values.length, 0);
  }
});

test("manual flight automatically detours around a directional obstacle", () => {
  const entities = new Cesium.EntityCollection();
  const viewer = { entities, scene: {
    globe: { show: false },
    pickFromRay(ray) {
      const frame = Cesium.Transforms.eastNorthUpToFixedFrame(ray.origin);
      const local = Cesium.Matrix4.multiplyByPointAsVector(Cesium.Matrix4.inverseTransformation(frame, new Cesium.Matrix4()), ray.direction, new Cesium.Cartesian3());
      return local.x > 0.7 ? { position: Cesium.Ray.getPoint(ray, 1) } : undefined;
    },
  } };
  const fleet = new Fleet(viewer);
  const drone = fleet.deploy(home);
  drone.setManualControl(true);
  drone.moveManually(1, 0, 0, 0.1);
  assert.equal(drone.collisionBlocked, false);
  assert.equal(drone.avoidanceActive, true);
  assert.equal(entities.getById(`${drone.id}_crash`).label.text.getValue(), "↗ AUTO-AVOIDING");
  assert.notEqual(drone.snapshot().latitude, home.latitude);

  const missionDrone = fleet.deploy(home);
  missionDrone.run({ drone_id: missionDrone.id, mission: [{ action: "goto", ...home, longitude: home.longitude + 0.01, speed_mps: 100 }] });
  missionDrone.update(0.1);
  assert.equal(missionDrone.snapshot().state, "AVOIDING");
  assert.equal(missionDrone.collisionBlocked, false);
  assert.equal(missionDrone.avoidanceActive, true);
});

test("photorealistic ground can be below the hidden fallback ellipsoid", () => {
  const viewer = { entities: new Cesium.EntityCollection(), scene: {
    pickFromRay: () => undefined,
    globe: { show: false, pick: () => { throw new Error("hidden globe must not be queried"); }, getHeight: () => 0 },
  } };
  const drone = new Fleet(viewer).deploy({ ...home, altitude: -11 });
  drone.setManualControl(true);
  drone.moveManually(1, 0, 0, 0.1);
  assert(drone.snapshot().longitude > home.longitude);
  assert.equal(drone.collisionBlocked, false);
  viewer.scene.pickFromRay = ray => ({ position: Cesium.Ray.getPoint(ray, 1) });
  drone.moveManually(1, 0, 0, 0.1);
  assert.equal(drone.collisionBlocked, true);
});

test("failed building queries do not freeze drones or leave the camera in an offscreen view", () => {
  const mainView = {};
  const viewer = { entities: new Cesium.EntityCollection(), scene: {
    view: mainView,
    pickFromRay() { this.view = {}; throw new Error("depth framebuffer unavailable"); },
    globe: { pick: () => undefined, getHeight: () => 0 },
  } };
  const fleet = new Fleet(viewer);
  const members = fleet.deployBulk(home, 2, 30);
  fleet.takeBatch(members.map(d => d.id), 60);
  const before = members.map(d => d.snapshot());
  fleet.moveBatch(1, 0, 0, 0.1, 0);
  members.forEach((d, i) => {
    assert(d.snapshot().longitude > before[i].longitude);
    assert.equal(d.collisionBlocked, false);
  });
  assert.equal(viewer.scene.view, mainView);
  // A failed building query must not bypass a real ground collision.
  viewer.scene.globe.getHeight = () => 79.9;
  fleet.moveBatch(0, 0, -1, 0.1, 0);
  assert(members.every(d => d.collisionBlocked));
});

test("low-clearance drones can climb out, but cannot descend through ground", () => {
  const viewer = { entities: new Cesium.EntityCollection(), scene: {
    pickFromRay: () => undefined,
    globe: { pick: () => undefined, getHeight: () => 79.9 },
  } };
  const drone = new Fleet(viewer).deploy(home);
  drone.setManualControl(true);
  drone.moveManually(0, 0, 1, 0.1);
  assert(drone.snapshot().altitude > home.altitude);
  assert.equal(drone.collisionBlocked, false);
  drone.stopManualMotion();
  const altitude = drone.snapshot().altitude;
  drone.moveManually(0, 0, -1, 0.1);
  assert.equal(drone.snapshot().altitude, altitude);
  assert.equal(drone.collisionBlocked, true);
});

test("wall and ground checks block manual motion, batches, missions and replay", () => {
  const entities = new Cesium.EntityCollection();
  let obstacle = false;
  const viewer = { entities, scene: {
    globe: { getHeight: () => 0, pick: () => undefined },
    pickFromRay(ray, excluded, width) {
      assert.equal(excluded, entities.values);
      assert.equal(width, 1);
      return obstacle ? { position: Cesium.Ray.getPoint(ray, 1) } : undefined;
    },
  } };
  const fleet = new Fleet(viewer);
  const members = fleet.deployBulk(home, 2, 30);
  fleet.takeBatch(members.map(d => d.id), 1000);
  const before = members.map(d => d.snapshot());
  obstacle = true;
  fleet.moveBatch(1, 0, 0, 0.1, 0);
  members.forEach((d, i) => { assert.deepEqual(d.snapshot(), before[i]); assert(d.collisionBlocked); });
  obstacle = false;
  fleet.moveBatch(-1, 0, 0, 0.1, 0);
  assert(members[0].snapshot().longitude < before[0].longitude);
  fleet.releaseBatch();
  const drone = members[0];
  const parked = drone.snapshot();
  obstacle = true;
  const destination = { ...home, longitude: home.longitude + 0.01 };
  drone.run({ drone_id: drone.id, mission: [{ action: "goto", ...destination, speed_mps: 10000 }] });
  drone.update(0.1);
  assert.equal(drone.snapshot().state, "BLOCKED");
  assert.equal(drone.snapshot().longitude, parked.longitude);
  fleet.commandGroup([drone.id], destination, 30, 0);
  fleet.updateReplay(1000);
  assert.equal(fleet.blockedCount, 1);
  assert.equal(fleet.replay.arrived, 0);
  assert.equal(fleet.replay.running, false);
  assert.equal(drone.snapshot().longitude, parked.longitude);
  obstacle = false;
  viewer.scene.globe.getHeight = () => 75;
  drone.setManualControl(true);
  drone.moveManually(0, 0, -1, 0.1);
  assert.equal(drone.collisionBlocked, true);
});

test("batch control shares speed and movement, releases markers and resumes preserved paths", () => {
  const entities = new Cesium.EntityCollection();
  const fleet = new Fleet({ entities });
  const members = fleet.deployBulk(home, 10, 30);
  const outsider = fleet.deploy(home);
  const parked = outsider.snapshot();
  const ids = members.map(d => d.id);
  const position = d => entities.getById(d.id).position.getValue();
  const trail = d => entities.getById(`${d.id}_trail`).polyline.positions.getValue();
  const spacing = Cesium.Cartesian3.distance(position(members[0]), position(members[1]));
  fleet.takeBatch(ids, 75.5);
  assert.equal(new Set(members.map(d => d.colorHex)).size, 1);
  assert(members.every(d => d.speedMph === 75.5 && d.snapshot().state === "MANUAL"));
  for (let i = 0; i < 180; i++) fleet.moveBatch(1, 0, 0, 1 / 60, Math.PI / 2);
  const starts = members.map(position);
  for (let i = 0; i < 60; i++) fleet.moveBatch(1, 0, 0, 1 / 60, Math.PI / 2);
  members.forEach((d, i) => assert(Math.abs(Cesium.Cartesian3.distance(starts[i], position(d)) - 75.5 * 0.44704) < 0.01));
  assert(Math.abs(Cesium.Cartesian3.distance(position(members[0]), position(members[1])) - spacing) < 0.01);
  assert.deepEqual(outsider.snapshot(), parked);
  fleet.releaseBatch();
  assert.equal(fleet.manualBatch.length, 0);
  const [markerId, markerIds] = [...fleet.batchMarkers][0];
  assert.deepEqual(markerIds, ids);
  assert(entities.getById(markerId).point);
  assert(members.every(d => entities.getById(`${d.id}_release_1`).box));
  const paths = members.map(d => trail(d).map(p => Cesium.Cartesian3.clone(p)));
  const positions = members.map(position);
  fleet.releaseBatch(); // Releasing twice must not duplicate markers.
  assert.equal(fleet.batchMarkers.size, 1);
  fleet.takeBatch(markerIds, members[0].speedMph);
  members.forEach((d, i) => {
    assert.deepEqual(position(d), positions[i]);
    assert.deepEqual(trail(d), paths[i]);
  });
  for (let i = 0; i < 120; i++) fleet.moveBatch(0, 1, 1, 1 / 60, 0);
  members.forEach((d, i) => {
    assert(trail(d).length > paths[i].length);
    assert.deepEqual(trail(d).slice(0, paths[i].length), paths[i]);
    assert.deepEqual(trail(d).at(-1), position(d));
  });
  fleet.releaseBatch();
  assert.equal(fleet.batchMarkers.size, 2);
  assert(members.every(d => entities.getById(`${d.id}_release_2`).box));
  fleet.takeBatch(ids, 60);
  fleet.clear();
  assert.equal(fleet.manualBatch.length, 0);
  assert.equal(fleet.batchMarkers.size, 0);
  assert.equal(entities.values.length, 0);
});

test("batch speed validation is atomic and only changes selected drones", () => {
  const fleet = new Fleet({ entities: new Cesium.EntityCollection() });
  const members = fleet.deployBulk(home, 3, 30);
  const ids = members.slice(0, 2).map(d => d.id);
  fleet.setBatchSpeed(ids, 90.25);
  assert.deepEqual(members.map(d => d.speedMph), [90.25, 90.25, 60]);
  for (const speed of [NaN, Infinity, 0, -2]) assert.throws(() => fleet.setBatchSpeed(ids, speed));
  assert.throws(() => fleet.setBatchSpeed([ids[0], "missing"], 100));
  assert.deepEqual(members.map(d => d.speedMph), [90.25, 90.25, 60]);
  fleet.takeBatch(ids, 90.25);
  assert.throws(() => fleet.takeBatch([], 60));
  assert.throws(() => fleet.takeBatch(["missing"], 60));
  assert.deepEqual(fleet.manualBatch.map(d => d.id), ids);
  fleet.setBatchSpeed(ids, 45);
  assert.deepEqual(members.map(d => d.speedMph), [45, 45, 60]);
});

test("bulk deployment preserves individual control and grid spacing", () => {
  const entities = new Cesium.EntityCollection();
  const fleet = new Fleet({ entities });
  const batch = fleet.deployBulk(home, 10, 30);
  assert.equal(batch.length, 10);
  assert.equal(fleet.drones.size, 10);
  for (let i = 0; i < batch.length; i++) for (let j = i + 1; j < batch.length; j++) {
    assert(Cesium.Cartesian3.distance(entities.getById(batch[i].id).position.getValue(), entities.getById(batch[j].id).position.getValue()) > 29.9);
  }
  assert(batch.every(d => !entities.getById(`${d.id}_trail`).show));
  const before = batch[1].snapshot();
  batch[0].setManualControl(true); batch[0].moveManually(1, 0, 0, 0.1);
  assert.deepEqual(batch[1].snapshot(), before);
  assert.throws(() => fleet.deployBulk(home, 0, 30));
  assert.throws(() => fleet.deployBulk(home, 3, 0));
  assert.equal(fleet.drones.size, 10);
});

test("named groups launch together, share color and actually arrive at separate destinations", () => {
  const entities = new Cesium.EntityCollection();
  const fleet = new Fleet({ entities });
  const batch = fleet.deployBulk(home, 10, 30);
  fleet.saveGroup("Alpha", batch.slice(0, 4).map(d => d.id));
  assert.equal(new Set(batch.slice(0, 4).map(d => d.colorHex)).size, 1);
  const outsider = batch[9].snapshot();
  const center = { ...home, longitude: home.longitude + 0.01 };
  batch[0].speedMph = 30;
  const ids = fleet.groups.get("Alpha").ids;
  fleet.commandGroup(ids, center, 40, 100, "Alpha");
  assert.equal(fleet.replay.total, 4);
  const duration = fleet.replay.duration;
  fleet.updateReplay(101);
  assert.notEqual(batch[0].snapshot().longitude, home.longitude);
  fleet.updateReplay(100 + duration + 1);
  assert.equal(fleet.replay.arrived, 4);
  const { formationSlots } = loadSource("formation");
  const slots = formationSlots(center, 4, 40);
  for (let i = 0; i < 4; i++) {
    const state = batch[i].snapshot();
    assert(Math.abs(state.longitude - slots[i].longitude) < 1e-9);
    assert(Math.abs(state.latitude - slots[i].latitude) < 1e-9);
    assert.equal(state.state, "HOVERING");
    const originColor = entities.getById(`${batch[i].id}_origin`).box.material.color.getValue();
    assert.equal(originColor.withAlpha(1).toCssHexString(), batch[i].colorHex);
  }
  assert.deepEqual(batch[9].snapshot(), outsider);
  // An ad-hoc selection also gets one color and starts from its real current positions.
  fleet.commandGroup([batch[0].id, batch[4].id], home, 30, 200);
  assert.equal(batch[0].colorHex, batch[4].colorHex);
  fleet.updateReplay(201);
  const stopped = batch[0].snapshot();
  fleet.stopReplay(); fleet.updateReplay(300);
  assert.equal(batch[0].snapshot().longitude, stopped.longitude);
  assert.equal(batch[0].snapshot().state, "HOVERING");
  fleet.clear(); assert.equal(fleet.groups.size, 0); assert.equal(entities.values.length, 0);
});

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
    assert(Cesium.Color.equals(leader.box.material.color.getValue(), expected));
    assert.equal(entities.getById(`${drone.id}_trail`).show, false);
    assert.equal(leader.polyline, undefined);
    assert(Cesium.Cartesian3.equals(leader.box.dimensions.getValue(), new Cesium.Cartesian3(0.36, 0.24, 0.12)));
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

test("backend ENU conversion preserves metre-scale east north and up offsets", () => {
  const { localToFixed } = loadSource("coordinates");
  const origin = { latitude: 38.8895, longitude: -77.0353, altitude: 20 };
  const anchor = localToFixed(origin, { x: 0, y: 0, z: 0 });
  for (const point of [{ x: 100, y: 0, z: 0 }, { x: 0, y: 100, z: 0 }, { x: 0, y: 0, z: 100 }]) {
    assert(Math.abs(Cesium.Cartesian3.distance(anchor, localToFixed(origin, point)) - 100) < 0.001);
  }
});
