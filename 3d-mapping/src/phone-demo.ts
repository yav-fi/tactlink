import * as Cesium from "cesium";
import type { DroneController } from "./drone-controller";
import type { Coordinates } from "./mission";
import { applyPhoneAction } from "./phone-flight";
import { GeoFrame } from "./runtime/frame";
import { OperatorLayer } from "./runtime/layers/operators";
import { PhoneControl, placePhones, PHONE_TIMEOUT, type PhoneSample } from "./phone-control";

/** Phone input for the existing flight controller. The browser owns the only demo drone. */
export function startPhoneDemo(viewer: Cesium.Viewer, target: DroneController, home: Coordinates, api: string): () => void {
  const frame = new GeoFrame();
  frame.update(home.latitude, home.longitude, home.altitude);
  const controller = new PhoneControl();
  const alignment = { anchor: "", rotation: 0, mirror: false };
  let room = "", closed = false, busy = false, lastMode = 0;
  const panel = document.createElement("section");
  panel.id = "phone-panel";
  panel.innerHTML = `<strong>PHONE CONTROL · ONE DRONE</strong>
    <p id="phone-status" role="status">Connecting to the phone receiver…</p>
    <p id="phone-owner">Waiting for the group</p><div id="phone-members"></div>
    <button id="phone-stop" type="button">Stop drone</button>
    <details><summary>Group alignment</summary>
      <label>Room <select id="phone-room"></select></label>
      <label>Stationary anchor <select id="phone-anchor"></select></label>
      <label>Rotate group <input id="phone-rotation" type="range" min="-180" max="180" value="0" step="1"></label>
      <label><input id="phone-mirror" type="checkbox"> Mirror group</label>
      <p>Metres are measured by UWB. The anchor stays 5 m north of the drone’s starting point. Keep it still and align the group to the map. Two-phone mode assumes a vertical map line, X = Z = 0, using real UWB distance. Three-phone mode sets Z = 0. Five-phone mode preserves relative XYZ; its third axis is not gravity height.</p>
    </details>
    <p class="phone-help">2-phone assumed-axis, 3-phone flat and 5-phone rooms supported. Hold a gesture for 0.4 s. Release to stop motion. ↑ climb · ↓ descend · palm stop · point orbit · three fingers forward · fist turn · 🤟 home.</p>`;
  document.body.append(panel);
  const text = (id: string, value: string) => { panel.querySelector<HTMLElement>(`#${id}`)!.textContent = value; };
  const roomSelect = panel.querySelector<HTMLSelectElement>("#phone-room")!;
  const anchorSelect = panel.querySelector<HTMLSelectElement>("#phone-anchor")!;
  const fixedPosition = () => { const p = target.snapshot(); return Cesium.Cartesian3.fromDegrees(p.longitude, p.latitude, p.altitude); };
  const layer = new OperatorLayer(viewer, frame, id => id === target.id ? fixedPosition() : undefined);
  const halt = () => { target.stopCommand(); };
  const reset = () => { halt(); controller.reset(); };
  let paused = false;
  const stopButton = panel.querySelector<HTMLButtonElement>("#phone-stop")!;
  stopButton.onclick = () => { paused = !paused; reset(); stopButton.textContent = paused ? "Resume phone control" : "Stop drone"; };
  roomSelect.onchange = () => { room = roomSelect.value; alignment.anchor = ""; reset(); };
  anchorSelect.onchange = () => { alignment.anchor = anchorSelect.value; reset(); };
  panel.querySelector<HTMLInputElement>("#phone-rotation")!.oninput = event => { alignment.rotation = Number((event.target as HTMLInputElement).value); reset(); };
  panel.querySelector<HTMLInputElement>("#phone-mirror")!.onchange = event => { alignment.mirror = (event.target as HTMLInputElement).checked; reset(); };
  const updateOptions = (select: HTMLSelectElement, entries: [string, string][], value: string) => {
    if (JSON.stringify(entries) !== select.dataset.entries) {
      select.replaceChildren(...entries.map(([id, name]) => new Option(name, id)));
      select.dataset.entries = JSON.stringify(entries);
    }
    select.value = value;
  };

  const tick = async () => {
    if (closed || busy) return;
    busy = true;
    try {
      const response = await fetch(`${api}/api/phones`, { signal: AbortSignal.timeout(1000), cache: "no-store" });
      if (!response.ok) throw new Error(`Receiver returned ${response.status}`);
      const data = await response.json() as { phones: PhoneSample[]; listening: boolean; phone_demo: boolean; host: string; port: number };
      if (closed) return;
      if (!data.listening) throw new Error("Phone receiver is offline — restart ./start");
      if (!data.phone_demo || data.host === "127.0.0.1") throw new Error("Old server is running — stop it and restart ./start");
      const all = data.phones;
      const rooms = [...new Set(all.map(phone => phone.room))];
      if (!room && rooms.length) room = rooms[0];
      updateOptions(roomSelect, rooms.map(id => [id, `${id.slice(0, 8)}${id === "mock" ? " (synthetic)" : ""}`]), room);
      const phones = all.filter(phone => phone.room === room);
      if (!alignment.anchor) alignment.anchor = phones.find(phone => phone.pos && phone.age <= PHONE_TIMEOUT)?.id ?? "";
      updateOptions(anchorSelect, phones.map(phone => [phone.id, phone.name]), alignment.anchor);
      const flat = phones[0]?.flat;
      const mode = phones[0]?.twoPhone ? 2 : flat ? 3 : 5;
      if (mode !== lastMode) { reset(); lastMode = mode; }
      const operators = placePhones(phones, alignment);
      const live = phones.filter(phone => phone.age <= PHONE_TIMEOUT);
      const tracked = operators.filter(operator => operator.age <= PHONE_TIMEOUT);
      const actual = frame.toLocal(fixedPosition());
      const control = controller.update(tracked, actual, performance.now());
      if (control.stop || paused || document.hidden) halt();
      const owner = control.operator;
      if (owner && !paused && !document.hidden) owner.controls = [target.id];
      layer.update({ operators });
      const members = panel.querySelector<HTMLElement>("#phone-members")!;
      members.replaceChildren(...phones.map(phone => {
        const row = document.createElement("div");
        const health = phone.age > PHONE_TIMEOUT ? "signal lost" : !phone.pos ? "waiting for UWB" : phone.geometryAge > 8 ? "position expired" : `${phone.geometryAge.toFixed(1)} s position age`;
        row.textContent = `${phone.name}${phone.id === owner?.operator_id ? " · controlling" : ""} — ${health}`;
        return row;
      }));
      text("phone-status", `${live.length}/${mode} phones connected · ${tracked.length} positioned · ${mode === 2 ? "2-phone test · axis assumed" : flat ? "3-phone flat (Z = 0)" : "5-phone"}`);
      text("phone-owner", paused ? "Phone control paused" : owner ? `${owner.name} controls ${target.id.replace("_", " ")} · ${owner.gesture.replaceAll("_", " ")}` : "Waiting for fresh UWB positions");
      const hud = document.querySelector<HTMLElement>("#gesture-connection");
      if (hud) hud.textContent = live.length ? `PHONE CAMERAS · ${live.length} CONNECTED` : "WAITING FOR PHONES";
      document.querySelector<HTMLElement>("#gesture-name")!.textContent = owner?.gesture.replaceAll("_", " ") ?? "NO CONTROLLER";
      document.querySelector<HTMLElement>("#gesture-confidence")!.textContent = owner ? `${owner.name} · ${Math.round(owner.gesture_confidence * 100)}%` : "Join one room and wait for its UWB map";
      document.querySelector<HTMLElement>("#gesture-progress-fill")!.style.width = `${control.progress * 100}%`;
      const action = control.action;
      if (!action || !owner || paused || document.hidden) return;
      applyPhoneAction(target, action, owner, home, actual);
      document.querySelector<HTMLElement>("#gesture-action")!.textContent = `${owner.name.toUpperCase()} · ${action.replaceAll("_", " ").toUpperCase()}`;
    } catch (error) {
      if (closed) return;
      reset(); layer.update({ operators: [] });
      text("phone-status", error instanceof Error ? error.message : "Phone receiver disconnected");
      text("phone-owner", "Drone holding — no live phone feed");
    } finally { busy = false; }
  };
  const visibility = () => { if (document.hidden) reset(); };
  document.addEventListener("visibilitychange", visibility);
  const interval = window.setInterval(() => { void tick(); }, 100);
  void tick();
  return () => { closed = true; window.clearInterval(interval); reset(); layer.update({ operators: [] }); panel.remove(); document.removeEventListener("visibilitychange", visibility); };
}
