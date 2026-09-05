const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const Cesium = require('cesium');

function loadSource(name) {
  const code = ts.transpileModule(fs.readFileSync(path.resolve(__dirname, '../src', `${name}.ts`), 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;
  const module = { exports: {} };
  new Function('require', 'module', 'exports', code)(
    id => id.startsWith('./') ? loadSource(id.slice(2)) : require(id), module, module.exports);
  return module.exports;
}
const { previewMission, previewCoordinate } = loadSource('flight-preview');
const { DroneController } = loadSource('drone-controller');
const { parseMission } = loadSource('mission');
const home = { latitude: 38.8895, longitude: -77.0353, altitude: 80 };
const preview = {
  schema_version: '1.0', type: 'flight_preview', frame: 'ENU', mission_id: 'test',
  segments: [
    { type: 'vertical', points: [{x:0,y:0,z:0}, {x:0,y:0,z:10}] },
    { type: 'arc', points: [{x:0,y:0,z:10}, {x:10,y:10,z:10}, {x:20,y:0,z:10}, {x:10,y:-10,z:10}, {x:0,y:0,z:10}] },
    { type: 'hold', points: [{x:0,y:0,z:10}], duration_s: 2 },
    { type: 'vertical', points: [{x:0,y:0,z:10}, {x:0,y:0,z:0}] },
  ],
};

test('ENU uses deployed home ellipsoid height and correct east/north axes', () => {
  const point = previewCoordinate({x:20,y:30,z:10}, home);
  assert(point.latitude > home.latitude);
  assert(point.longitude > home.longitude);
  assert.equal(point.altitude, 90);
  const mission = previewMission(preview, 'drone_1', home, 5);
  assert.deepEqual(parseMission(JSON.stringify(mission)), mission);
  assert.equal(mission.mission.filter(s => s.action === 'hover')[0].duration_s, 2);
  assert(!mission.mission.some(s => s.action === 'orbit')); // Preserve backend arc geometry.
});

test('converted path runs through the actual teammate controller and lands at preview zero', () => {
  const drone = new DroneController({entities: new Cesium.EntityCollection()}, home);
  drone.run(previewMission(preview, drone.id, home, 5));
  for (let i = 0; i < 2000; i++) drone.update(0.1);
  const result = drone.snapshot();
  assert.equal(result.state, 'IDLE');
  assert(Math.abs(result.latitude - home.latitude) < 1e-8);
  assert(Math.abs(result.longitude - home.longitude) < 1e-8);
  assert.equal(result.altitude, home.altitude);
});

test('unsupported frames and invalid speed are rejected', () => {
  assert.throws(() => previewMission({...preview, frame:'NED'}, 'drone_1', home, 5));
  assert.throws(() => previewMission(preview, 'drone_1', home, NaN));
});
