import * as Cesium from 'cesium';
import { Battery, BATTERY_PRESETS } from './battery';
import type { DroneController } from './drone-controller';
import type { Fleet } from './fleet';

export function startBatteryPanel(fleet: Fleet, selected: () => DroneController | undefined): (dt: number, paused: boolean) => void {
  const panel = document.createElement('details');
  panel.id = 'battery-panel';
  panel.innerHTML = `<summary>Battery simulation · <span id="battery-summary">off</span></summary>
    <label><input id="battery-enabled" type="checkbox"> Enable for PC fleet</label>
    <div id="battery-settings" hidden>
      <p>Applies to every PC drone. Select a drone to inspect or replace its battery.</p>
      <label>Battery profile <select id="battery-profile"><option value="standard">Standard</option><option value="lightweight">Lightweight</option><option value="endurance">Endurance</option><option value="custom">Custom</option></select></label>
      <label>Capacity (Wh) <input id="battery-capacity" type="number" min="1" max="500" value="60" step="any"></label>
      <label>Battery weight (kg) <input id="battery-mass" type="number" min="0.05" max="3" value="0.38" step="0.01"></label>
      <label>Battery health (%) <input id="battery-health" type="number" min="10" max="100" value="100"></label>
      <button id="battery-replace" type="button">Replace selected battery</button>
      <p id="battery-message" role="status"></p>
      <div id="battery-readout" aria-live="off"></div>
      <p>20% reserve warning. Empty battery freezes simulated flight; it does not model a physical landing. Estimates are uncalibrated. Pause freezes consumption; turning this mode off preserves charge.</p>
    </div>`;
  document.body.append(panel);
  const input = (id: string) => panel.querySelector<HTMLInputElement>(`#battery-${id}`)!;
  const enabled = input('enabled');
  const summary = panel.querySelector<HTMLElement>('#battery-summary')!;
  const message = panel.querySelector<HTMLElement>('#battery-message')!;
  const readout = panel.querySelector<HTMLElement>('#battery-readout')!;
  const profile = panel.querySelector<HTMLSelectElement>('#battery-profile')!;
  const samples = new WeakMap<DroneController, { position: Cesium.Cartesian3; speed: number }>();
  let renderTime = 0;
  enabled.onchange = () => {
    panel.querySelector<HTMLElement>('#battery-settings')!.hidden = !enabled.checked;
    for (const d of fleet.drones.values()) { d.battery.enabled = enabled.checked; samples.delete(d); }
    summary.textContent = enabled.checked ? 'on' : 'off';
  };
  profile.onchange = () => {
    const preset = BATTERY_PRESETS[profile.value as keyof typeof BATTERY_PRESETS];
    if (preset) { input('capacity').value = String(preset.capacityWh); input('mass').value = String(preset.massKg); }
  };
  for (const field of ['capacity', 'mass', 'health']) input(field).oninput = () => { profile.value = 'custom'; };
  panel.querySelector<HTMLButtonElement>('#battery-replace')!.onclick = () => {
    const d = selected();
    if (!d) { message.textContent = 'Select or deploy a drone first.'; return; }
    const capacity = Number(input('capacity').value), mass = Number(input('mass').value), health = Number(input('health').value) / 100;
    try {
      // Validate before creating an undo entry or stopping motion.
      const trial = new Battery(); trial.replace(capacity, mass, health);
      fleet.checkpoint('replace battery');
      fleet.stopReplay(); d.stopCommand(); d.battery.replace(capacity, mass, health); samples.delete(d);
      message.textContent = `Full battery fitted to ${d.id}. Flight stopped.`;
    } catch (e) { message.textContent = (e as Error).message; }
  };
  return (dt, paused) => {
    for (const d of fleet.drones.values()) {
      d.battery.enabled = enabled.checked;
      const position = d.cameraPosition;
      const old = samples.get(d);
      const elapsed = !paused && enabled.checked ? dt : 0;
      let horizontal = 0, vertical = 0, acceleration = 0;
      if (old && elapsed > 0) {
        const local = Cesium.Matrix4.multiplyByPoint(Cesium.Matrix4.inverseTransformation(Cesium.Transforms.eastNorthUpToFixedFrame(old.position), new Cesium.Matrix4()), position, new Cesium.Cartesian3());
        horizontal = Math.hypot(local.x, local.y) / elapsed;
        vertical = local.z / elapsed;
        // Replays/reset/undo can reposition a drone instantly: do not bill teleports.
        if (Math.hypot(horizontal, vertical) > 250) { horizontal = 0; vertical = 0; }
        acceleration = Math.min(20, Math.abs(horizontal - old.speed) / elapsed);
      }
      const altitude = Cesium.Cartographic.fromCartesian(position).height;
      d.battery.step(elapsed, horizontal, vertical, acceleration, altitude > d.homeCoordinates.altitude + 0.8 || horizontal > 0.1 || Math.abs(vertical) > 0.1);
      samples.set(d, { position, speed: horizontal });
      if (d.battery.depleted) { d.stopCommand(); if (fleet.replay.running) d.replayBlocked = true; }
    }
    if (performance.now() - renderTime < 250) return;
    renderTime = performance.now();
    if (!enabled.checked) return;
    const d = selected();
    if (!d) { readout.textContent = 'No drone selected.'; summary.textContent = 'on'; return; }
    const b = d.battery;
    const minutes = b.powerW > 0 ? b.remainingWh / b.powerW * 60 : 0;
    const home = Cesium.Cartesian3.fromDegrees(d.homeCoordinates.longitude, d.homeCoordinates.latitude, d.homeCoordinates.altitude);
    const distance = Cesium.Cartesian3.distance(d.cameraPosition, home);
    const speed = Math.max(1, d.speedMph * 0.44704);
    const returnWh = b.estimatePower(speed, 0) * distance / speed / 3600;
    const warning = b.depleted ? 'EMPTY · flight stopped' : b.remainingWh - returnWh < b.usableWh * b.reserve ? 'Return reserve low' : 'Charge OK';
    summary.textContent = `${Math.round(b.fraction * 100)}% · ${warning}`;
    readout.textContent = `${d.id}: ${b.remainingWh.toFixed(1)} / ${b.usableWh.toFixed(1)} Wh · ${b.powerW.toFixed(0)} W · ${minutes.toFixed(1)} min at current draw. Fitted pack: ${b.capacityWh} Wh, ${b.massKg} kg, ${Math.round(b.health * 100)}% health. Direct return estimate: ${returnWh.toFixed(1)} Wh (excludes detours).`;
  };
}
