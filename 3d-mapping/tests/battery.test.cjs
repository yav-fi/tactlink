const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
function load(name) {
 const source = fs.readFileSync(path.resolve(__dirname, '../src', name + '.ts'), 'utf8');
 const code = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
 const m = { exports: {} };
 new Function('require', 'module', 'exports', code)(id => id.startsWith('.') ? load(path.join(path.dirname(name), id)) : require(id), m, m.exports);
 return m.exports;
}
const { Battery } = load('battery');
test('disabled and paused batteries do not consume energy', () => {
 const b = new Battery(); b.step(100, 20, 10, 5, true); assert.equal(b.remainingWh, 60);
 b.enabled = true; b.step(0, 20, 10, 5, true); assert.equal(b.remainingWh, 60);
});
test('hover consumption integrates watts into watt hours independently of frame size', () => {
 const a = new Battery(), b = new Battery(); a.enabled = b.enabled = true;
 a.step(60, 0, 0, 0, true);
 for (let i = 0; i < 600; i++) b.step(0.1, 0, 0, 0, true);
 assert(Math.abs(a.remainingWh - (60 - 155 / 60)) < 1e-8);
 assert(Math.abs(a.remainingWh - b.remainingWh) < 1e-8);
});
test('speed, climb, descent, acceleration and pack weight affect power', () => {
 const b = new Battery(), hover = b.estimatePower(0, 0);
 assert(b.estimatePower(30, 0) > b.estimatePower(8, 0));
 assert(b.estimatePower(0, 5) > hover); assert(b.estimatePower(0, -5) > 0);
 assert(b.estimatePower(10, 0, 5) > b.estimatePower(10, 0, 0));
 b.massKg = 0.62; assert(b.estimatePower(0, 0) > hover);
});
test('health limits usable energy and depletion clamps to zero', () => {
 const b = new Battery(); b.replace(35, .22, .5); assert.equal(b.remainingWh, 17.5);
 b.enabled = true; b.step(100000, 0, 0, 0, true); assert.equal(b.remainingWh, 0); assert(b.depleted);
 b.enabled = false; assert(!b.depleted);
 assert.throws(() => b.replace(NaN, .2, 1)); assert.throws(() => b.replace(20, -.2, 1));
});
test('controller blocks depleted flight and preserves battery through snapshots', () => {
 const Cesium = require('cesium'); const { DroneController } = load('drone-controller');
 const viewer = { entities: new Cesium.EntityCollection() };
 const d = new DroneController(viewer, {latitude:38.88928,longitude:-77.0353,altitude:20}, Cesium.Color.CYAN, 'test');
 d.battery.enabled = true; d.battery.remainingWh = 0;
 const before = d.snapshot();
 d.startGestureMotion(1,0,0,10); d.update(.1);
 assert.equal(d.snapshot().longitude, before.longitude);
 assert.equal(d.beginReplay(), null);
 const saved = d.capture(); d.battery.replace(95,.62,1); d.restore(saved);
 assert.equal(d.battery.remainingWh, 0); assert(d.battery.depleted);
});
