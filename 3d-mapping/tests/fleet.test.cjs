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
const { blendHeading, fleetCameraFrame, idleCameraDriftRate, screenRelativeMovement } = loadSource("cinematic-camera");
const { parseCommandInput, parseCommandSequence } = loadSource("command-console");
const { compileMissionSequence } = loadSource("mission-sequence");
const { resolveLandmark } = loadSource("landmarks");
const { MOTION_TRACE_LIFETIME_MS, motionTraceAlpha } = loadSource("motion-trace");
const { plannedFlightStep } = loadSource("flight-motion");
const { GestureHoldInterpreter } = loadSource("gesture-hold");
const { GestureCommandRepeater } = loadSource("gesture-repeat");
const { deriveFingerMotionInput, FingerMotionInterpreter, resolvePointedDirection } = loadSource("finger-motion");
const home = { latitude: 38.889, longitude: -77.036, altitude: 80 };

test("browser gestures fire once after a deliberate hold and re-arm after release", () => {
  const gestures = new GestureHoldInterpreter();
  assert.deepEqual(gestures.update("Thumb_Up", 0), { progress: 0 });
  assert.deepEqual(gestures.update("Thumb_Up", 200), { progress: 0.5 });
  assert.deepEqual(gestures.update("Thumb_Up", 400), { progress: 1, action: "takeoff" });
  assert.deepEqual(gestures.update("Thumb_Up", 800), { progress: 0 });

  gestures.update("None", 900);
  gestures.update("None", 1100);
  gestures.update("None", 1250);
  gestures.update("Thumb_Up", 1300);
  assert.equal(gestures.update("Thumb_Up", 1700).action, "takeoff");
});

test("browser gesture holds survive a brief low-confidence frame", () => {
  const gestures = new GestureHoldInterpreter();
  gestures.update("Pointing_Up", 0);
  assert.equal(gestures.update("Pointing_Up", 200).progress, 0.5);
  assert.equal(gestures.update("None", 300).progress, 0.75);
  assert.equal(gestures.update("Pointing_Up", 400).action, "orbit");
});

test("browser canned gestures map to the primary flight actions", () => {
  const expected = {
    Thumb_Down: "land",
    Pointing_Up: "orbit",
    ILoveYou: "return_home",
    Open_Palm: "halt",
    Closed_Fist: "rotate_heading",
  };
  for (const [gesture, action] of Object.entries(expected)) {
    const interpreter = new GestureHoldInterpreter();
    interpreter.update(gesture, 0);
    assert.equal(interpreter.update(gesture, 400).action, action);
  }
});

test("held motion gestures repeat and release into one explicit stop", () => {
  const repeats = new GestureCommandRepeater();
  repeats.start("rotate_heading", "Closed_Fist", 0);
  assert.deepEqual(repeats.update("Closed_Fist", 1_799), []);
  assert.deepEqual(repeats.update("Closed_Fist", 1_800), ["rotate_heading"]);
  assert.deepEqual(repeats.update(undefined, 2_000), []);
  assert.deepEqual(repeats.update(undefined, 2_220), ["gesture_stop"]);
  assert.deepEqual(repeats.update(undefined, 5_000), []);

  repeats.start("orbit", "Pointing_Up", 6_000);
  assert.deepEqual(repeats.update("Pointing_Up", 20_000), []);
  assert.deepEqual(repeats.update(undefined, 20_220), []);
  assert.deepEqual(repeats.update(undefined, 20_440), ["gesture_stop"]);
});

test("automatic camera keeps a restrained side-to-side drift", () => {
  assert.equal(idleCameraDriftRate(0), 0.012);
  assert(idleCameraDriftRate(Math.PI / 0.55) < 0);
  assert(Math.abs(idleCameraDriftRate(20)) <= 0.012);
});

function motionHand(pointDirection, fingers = [false, true, true, false, false], present = true) {
  return { present, fingers, pointDirection };
}

function feedMotion(interpreter, input, start, duration = 400) {
  const actions = [];
  let now = start;
  for (let elapsed = 0; elapsed < duration; elapsed += 50) {
    now += 50;
    actions.push(...interpreter.update(input, now).actions);
  }
  return { actions, now };
}

test("two-finger V-H-V-H wiper flies toward its final point", () => {
  for (const [horizontal, expected] of [[[0.98, 0.05], "fly_east"], [[-0.98, 0.05], "fly_west"]]) {
    const interpreter = new FingerMotionInterpreter();
    let now = 0;
    const actions = [];
    for (const direction of [[0.03, -0.99], horizontal, [0.03, -0.99], horizontal]) {
      const result = feedMotion(interpreter, motionHand(direction), now);
      now = result.now;
      actions.push(...result.actions);
    }
    assert.deepEqual(actions, [expected]);
  }
  assert.equal(resolvePointedDirection([0.1, -0.99]), "fly_north");
});

test("three-finger W pose fires one forward dash per deliberate hold", () => {
  const interpreter = new FingerMotionInterpreter();
  const three = motionHand([0, -1], [false, true, true, true, false]);
  let result = feedMotion(interpreter, three, 0, 800);
  assert.deepEqual(result.actions, ["fly_forward"]);
  const stillHeld = feedMotion(interpreter, three, result.now, 500);
  assert.deepEqual(stillHeld.actions, []);
  result = feedMotion(interpreter, motionHand([0, 0], [false, false, false, false, false]), stillHeld.now, 450);
  assert.deepEqual(feedMotion(interpreter, three, result.now, 800).actions, ["fly_forward"]);
});

test("browser landmarks recover the original three-finger geometry", () => {
  const landmarks = Array.from({ length: 21 }, () => ({ x: 0.5, y: 0.8 }));
  landmarks[0] = { x: 0.5, y: 0.9 };
  for (const [mcp, pip, tip, x] of [[5, 6, 8, 0.40], [9, 10, 12, 0.50], [13, 14, 16, 0.60]]) {
    landmarks[mcp] = { x, y: 0.68 };
    landmarks[pip] = { x, y: 0.46 };
    landmarks[tip] = { x, y: 0.20 };
  }
  landmarks[17] = { x: 0.70, y: 0.68 };
  landmarks[18] = { x: 0.70, y: 0.48 };
  landmarks[20] = { x: 0.70, y: 0.72 };
  const geometry = deriveFingerMotionInput(landmarks);
  assert.deepEqual(geometry.fingers.slice(1), [true, true, true, false]);
  assert(geometry.pointDirection[1] < -0.95);
});

test("command bar understands slash commands and common plain English", () => {
  assert.deepEqual(parseCommandInput("/deploy 4 survey"), { type: "deploy", count: 4, survey: true });
  assert.deepEqual(parseCommandInput("fly drone 2"), { type: "fly", droneNumber: 2 });
  assert.deepEqual(parseCommandInput("move forward 75 meters"), { type: "move", direction: "forward", meters: 75 });
  assert.deepEqual(parseCommandInput("/goto 38.8895, -77.0353, 120"), { type: "goto", latitude: 38.8895, longitude: -77.0353, altitude: 120, all: false });
  assert.deepEqual(parseCommandInput("return the whole fleet home"), { type: "return", all: true });
  assert.deepEqual(parseCommandInput("rotate right"), { type: "turn", direction: "right", degrees: 90 });
  assert.deepEqual(parseCommandInput("inspect the monuments with two drones"), { type: "ai", instruction: "inspect the monuments with two drones" });
});

test("named DC places resolve to safe real-world approach coordinates", () => {
  const monument = resolveLandmark("the Washington Monument");
  assert.equal(monument.name, "Washington Monument");
  assert(Math.abs(monument.latitude - 38.8895) < 0.001);
  assert.notEqual(monument.longitude, -77.0353, "approach point should not be inside the monument");
  assert.deepEqual(parseCommandInput("go to the Washington Monument"), {
    type: "landmark", name: "Washington Monument", latitude: monument.latitude, longitude: monument.longitude,
  });
  assert.deepEqual(parseCommandInput("go to the National Gallery of Art"), { type: "place", query: "the national gallery of art" });
});

test("plain text chains place, wait, relative movement, and return into one mission", () => {
  const intents = parseCommandSequence("go to the Washington Monument, wait 5 seconds, then go forward 40 meters and then return home");
  assert.deepEqual(intents.map(intent => intent.type), ["landmark", "hover", "move", "return"]);
  const start = { latitude: 38.889, longitude: -77.045, altitude: 85 };
  const steps = compileMissionSequence(intents, start, home, 0, 12);
  assert.deepEqual(steps.map(step => step.action), ["goto", "hover", "goto", "return_home"]);
  assert.equal(steps[1].duration_s, 5);
  assert.equal(steps[0].latitude, resolveLandmark("Washington Monument").latitude);
  assert(Cesium.Cartesian3.distance(
    Cesium.Cartesian3.fromDegrees(steps[0].longitude, steps[0].latitude, steps[0].altitude),
    Cesium.Cartesian3.fromDegrees(steps[2].longitude, steps[2].latitude, steps[2].altitude),
  ) > 39);
  assert.deepEqual(parseCommandSequence("go forward 10 and wait 5 seconds and go back 10").map(intent => intent.type), ["move", "hover", "move"]);
});

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

test("motion trace particles fade to transparent within a few seconds", () => {
  assert.equal(motionTraceAlpha(0), 0.9);
  assert(motionTraceAlpha(MOTION_TRACE_LIFETIME_MS / 2) < 0.3);
  assert.equal(motionTraceAlpha(MOTION_TRACE_LIFETIME_MS), 0);
});

test("planned flight accelerates smoothly and brakes for arrival", () => {
  let speed = 0;
  let remaining = 100;
  const speeds = [];
  for (let frame = 0; frame < 200 && remaining > 0; frame++) {
    const step = plannedFlightStep(speed, 40, remaining, 0.1);
    speed = step.speed;
    remaining -= step.distance;
    speeds.push(speed);
  }
  assert(speeds[1] > speeds[0]);
  assert(Math.max(...speeds) <= 40);
  assert(speeds.at(-1) === 0);
  assert(remaining <= 0.000001);
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

test("bundled aircraft model follows the controller during flight and playback", () => {
  const entities = new Cesium.EntityCollection();
  const fleet = new Fleet({ entities });
  const drone = fleet.deploy(home);
  const aircraft = entities.getById(drone.id);
  assert.equal(aircraft.model.uri.getValue(), "/models/uav.glb");
  assert.equal(aircraft.model.runAnimations.getValue(), true);
  assert.equal(aircraft.box, undefined);
  drone.setManualControl(true);
  for (let i = 0; i < 120; i++) drone.moveManually(1, 0, 0, 1 / 60, 1);
  const flown = Cesium.Cartesian3.clone(aircraft.position.getValue());
  drone.setManualControl(false);
  fleet.startReplay(0); fleet.updateReplay(0.5);
  const replay = entities.getById(`${drone.id}_replay`);
  assert(replay.model);
  assert(!Cesium.Cartesian3.equals(replay.position.getValue(), flown));
  drone.setColor("#ff6666");
  assert.equal(aircraft.model.silhouetteColor.getValue().toCssHexString(), "#ff6666");
  fleet.clear(); assert.equal(entities.values.length, 0);
});

test("bundled aircraft keeps a black canopy and a red accent material", () => {
  const bytes = fs.readFileSync(path.resolve(__dirname, "../public/models/uav.glb"));
  assert.equal(bytes.readUInt32LE(0), 0x46546c67);
  const jsonLength = bytes.readUInt32LE(12);
  const gltf = JSON.parse(bytes.subarray(20, 20 + jsonLength).toString("utf8").trim());
  const black = gltf.materials.find(material => material.name === "blackPanel");
  const red = gltf.materials.find(material => material.name === "accent");
  assert(black.pbrMetallicRoughness.baseColorFactor.slice(0, 3).every(channel => channel < 0.02));
  assert(red.pbrMetallicRoughness.baseColorFactor[0] > 0.9);
  assert(gltf.meshes[0].primitives.some(primitive => primitive.material === gltf.materials.indexOf(black)));
  assert.equal(gltf.animations[0].channels.length, 4);
  assert.equal(gltf.nodes.filter(node => node.name.startsWith("rotor_")).length, 4);
  assert.equal(gltf.meshes[1].name, "rotor_blades");
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
  for (let frame = 0; frame < 20 && !missionDrone.avoidanceActive; frame++) missionDrone.update(0.1);
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
  for (let frame = 0; frame < 20 && drone.snapshot().state !== "BLOCKED"; frame++) drone.update(0.1);
  assert.equal(drone.snapshot().state, "BLOCKED");
  assert(Cesium.Cartesian3.distance(
    Cesium.Cartesian3.fromDegrees(parked.longitude, parked.latitude, parked.altitude),
    entities.getById(drone.id).position.getValue(),
  ) < 0.6);
  const blockedAt = drone.snapshot();
  fleet.commandGroup([drone.id], destination, 30, 0);
  fleet.updateReplay(1000);
  assert.equal(fleet.blockedCount, 1);
  assert.equal(fleet.replay.arrived, 0);
  assert.equal(fleet.replay.running, false);
  assert.equal(drone.snapshot().longitude, blockedAt.longitude);
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
  assert(members.every(d => entities.getById(`${d.id}_release_1`).label));
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
  assert(members.every(d => entities.getById(`${d.id}_release_2`).label));
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
    const originColor = entities.getById(`${batch[i].id}_origin`).label.fillColor.getValue();
    assert.equal(originColor.toCssHexString(), batch[i].colorHex);
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
    assert(replay.model);
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
  assert.doesNotThrow(() => parseMission(JSON.stringify({ drone_id: "drone_1", mission: [{ action: "orbit", radius_m: 20, duration_s: 2, center: home }] })));
  assert.throws(() => parseMission(JSON.stringify({ drone_id: "drone_1", mission: [{ action: "orbit", radius_m: 20, duration_s: 2, center: { latitude: 999, longitude: 0, altitude: 1 } }] })));
  assert.throws(() => parseMission(JSON.stringify({ drone_id: "drone_1", mission: [{ action: "return_home", speed_mps: -2 }] })));
});

test("halt cancels an active mission and holds the current position", () => {
  const entities = new Cesium.EntityCollection();
  const fleet = new Fleet({ entities });
  const drone = fleet.deploy(home);
  drone.run({ drone_id: drone.id, mission: [{ action: "orbit", radius_m: 30, duration_s: 60 }] });
  drone.update(0.1);
  drone.stopCommand();
  const stopped = drone.snapshot();
  drone.update(1);
  assert.deepEqual(drone.snapshot(), stopped);
  assert.equal(stopped.state, "HOVERING");
});

test("held gesture motion accelerates continuously and stops on release", () => {
  const entities = new Cesium.EntityCollection();
  const drone = new Fleet({ entities }).deploy(home);
  drone.startGestureMotion(0, 1, 0, 38);
  const distances = [];
  let previous = Cesium.Cartesian3.clone(entities.getById(drone.id).position.getValue());
  for (let frame = 0; frame < 12; frame++) {
    drone.update(0.1);
    const current = Cesium.Cartesian3.clone(entities.getById(drone.id).position.getValue());
    distances.push(Cesium.Cartesian3.distance(previous, current));
    previous = current;
  }
  assert.equal(drone.snapshot().state, "GESTURE_CONTROL");
  assert(distances[5] > distances[0]);
  const released = drone.snapshot();
  drone.stopCommand();
  drone.update(1);
  assert.deepEqual(drone.snapshot(), { ...released, state: "HOVERING", currentStep: 0, totalSteps: 0 });
});

test("closed-fist heading command turns in place with eased motion", () => {
  const entities = new Cesium.EntityCollection();
  const drone = new Fleet({ entities }).deploy(home);
  const start = drone.snapshot();
  drone.rotateHeading(90);
  drone.update(0.1);
  assert.equal(drone.snapshot().state, "TURNING");
  assert(drone.heading > 0 && drone.heading < Math.PI / 2);
  for (let frame = 0; frame < 20; frame++) drone.update(0.1);
  assert.equal(drone.snapshot().state, "HOVERING");
  assert(Math.abs(drone.heading - Math.PI / 2) < 1e-9);
  assert.equal(drone.snapshot().latitude, start.latitude);
  assert.equal(drone.snapshot().longitude, start.longitude);
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
    assert.equal(leader.model.uri.getValue(), "/models/uav.glb");
    assert(Cesium.Color.equals(leader.model.silhouetteColor.getValue(), expected));
    assert.equal(entities.getById(`${drone.id}_trail`).show, false);
    assert.equal(leader.polyline, undefined);
    assert.equal(leader.model.minimumPixelSize.getValue(), 42);
    const trace = entities.getById(`${drone.id}_trace_0`);
    assert(trace.point);
    assert.equal(trace.polyline, undefined);
    assert.equal(entities.values.filter(entity => entity.id.startsWith(`${drone.id}_trace_`)).length, 72);
    assert(trace.point.pixelSize.getValue() <= 5);
  }
  assert.equal(fleet.drones.size, 9);
});

test("independent movement, label-only releases, preserved routes and full cleanup", () => {
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
  assert(release.label);
  assert.equal(release.label.text.getValue(), "RELEASE 1");
  assert.equal(release.box, undefined);
  assert.equal(release.ellipsoid, undefined);
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
