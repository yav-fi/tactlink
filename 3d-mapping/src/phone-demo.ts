import * as Cesium from "cesium";
import type { DroneController } from "./drone-controller";
import type { Coordinates } from "./mission";
import { startPhoneScene } from "./phone-scene";
import { applyPhoneAction, phoneFlightBand } from "./phone-flight";
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
    <label>Live room <select id="phone-room"></select></label>
    <div class="phone-view-controls"><button id="phone-closer" title="Zoom in">＋</button><button id="phone-wider" title="Zoom out">−</button><button id="phone-frame">Frame group</button></div>
    <p class="phone-scale">1 square = 1 m · people 1.75 m · drone 0.8 m rotor envelope</p>
    <p id="phone-height"></p>
    <p id="phone-owner">Waiting for the group</p><div id="phone-members"></div>
    <button id="phone-stop" type="button">Stop drone</button>
    <details><summary>Group alignment</summary>
      <label>Stationary anchor <select id="phone-anchor"></select></label>
      <label>Rotate group <input id="phone-rotation" type="range" min="-180" max="180" value="0" step="1"></label>
      <label><input id="phone-mirror" type="checkbox"> Mirror group</label>
      <p>Metres are measured by UWB. The anchor stays 5 m north of the drone’s starting point. Keep it still and align the group to the map. Two-phone mode assumes a vertical map line, X = Z = 0, using real UWB distance. Three-phone mode sets Z = 0. Five-phone mode preserves relative XYZ; its third axis is not gravity height.</p>
    </details>
    <p class="phone-help">The gold person controls the drone. Other people observe. Drag the map to look around; Frame group restores the close view.</p>`;
  document.body.append(panel);
  const guide = document.createElement("section");
  guide.id = "phone-guide";
  guide.innerHTML = `<strong>GESTURES → DRONE</strong>
    <p>Use the phone’s <b>front camera</b>. Keep the whole hand in the selfie preview. Hold a recognized pose for <b>0.4 seconds</b>.</p>
    <dl>
      <div data-gesture="Thumb_Up"><dt>👍 Thumb up</dt><dd>Fingers curled, thumb pointing up. Climb at 2 m/s while held.</dd></div>
      <div data-gesture="Thumb_Down"><dt>👎 Thumb down</dt><dd>Fingers curled, thumb pointing down. Descend at 1 m/s to the hover floor; this demo does not land among people.</dd></div>
      <div data-gesture="Open_Palm"><dt>✋ Open palm</dt><dd>Four fingers extended. Stop and hover.</dd></div>
      <div data-gesture="Three_Finger_Forward"><dt>Three fingers</dt><dd>Index + middle + ring extended; pinky and thumb folded. Fly forward at 2 m/s in the phone’s compass direction.</dd></div>
      <div data-gesture="Pointing_Up"><dt>☝️ Index finger only</dt><dd>Other fingers curled. Circle the controlling person counterclockwise from above, aiming for a 2 m radius, while held.</dd></div>
      <div data-gesture="Closed_Fist"><dt>✊ Closed fist</dt><dd>All fingers and thumb curled. Turn the drone 90° clockwise once. Release and repeat for another turn.</dd></div>
      <div data-gesture="ILoveYou"><dt>🤟 Thumb + index + pinky</dt><dd>Middle and ring curled. Fly to the starting location at 2 m/s, holding altitude. Keep the pose until arrival; release stops the trip.</dd></div>
      <div data-gesture="Dash_Left Dash_Right"><dt>✌️ Two-finger swing</dt><dd>Index + middle extended. Turn them vertical → horizontal → vertical → horizontal within 4 seconds. Finish pointing left or right in the selfie preview and hold briefly: short movement at 2 m/s to that side of the phone’s compass direction. A still V sign does nothing.</dd></div>
    </dl>
    <p><b>Release your hand to stop.</b> The closest person to the drone in the horizontal plane takes control, with a fresh 0.4 s hold after handoff. Keep the phone aimed in the direction you mean by forward.</p>`;
  document.body.append(guide);
  const text = (id: string, value: string) => { panel.querySelector<HTMLElement>(`#${id}`)!.textContent = value; };
  const roomSelect = panel.querySelector<HTMLSelectElement>("#phone-room")!;
  const anchorSelect = panel.querySelector<HTMLSelectElement>("#phone-anchor")!;
  const fixedPosition = () => { const p = target.snapshot(); return Cesium.Cartesian3.fromDegrees(p.longitude, p.latitude, p.altitude); };
  const layer = new OperatorLayer(viewer, frame, id => id === target.id ? fixedPosition() : undefined);
  layer.setOptions({ humanModels: true });
  const scene = startPhoneScene(viewer, target, frame);
  panel.querySelector<HTMLButtonElement>("#phone-closer")!.onclick = () => scene.zoom(0.8);
  panel.querySelector<HTMLButtonElement>("#phone-wider")!.onclick = () => scene.zoom(1.25);
  panel.querySelector<HTMLButtonElement>("#phone-frame")!.onclick = () => scene.focus();
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
      if (!rooms.includes(room)) {
        room = rooms[0] ?? "";
        alignment.anchor = "";
        reset();
      }
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
      scene.update(tracked);
      const band = phoneFlightBand(tracked);
      text("phone-height", `Drone ${actual.z.toFixed(1)} m · hover floor ${band.floor.toFixed(1)} m · ceiling ${band.ceiling.toFixed(1)} m`);
      for (const row of guide.querySelectorAll<HTMLElement>("[data-gesture]")) row.classList.toggle("active", !!owner && row.dataset.gesture!.split(" ").includes(owner.gesture));
      const members = panel.querySelector<HTMLElement>("#phone-members")!;
      members.replaceChildren(...phones.map(phone => {
        const row = document.createElement("div");
        const health = phone.age > PHONE_TIMEOUT ? "signal lost" : !phone.pos ? "waiting for UWB" : phone.geometryAge > 8 ? "position expired" : `${phone.geometryAge.toFixed(1)} s position age`;
        row.textContent = `${phone.name}${phone.id === owner?.operator_id ? " · controlling" : ""} — ${health}`;
        return row;
      }));
      text("phone-status", all.length === 0
        ? "No phone telemetry. Set Visualizer host on each phone to this Mac’s Wi-Fi IP:9870, connect to the same Wi-Fi, and keep the app open."
        : `${live.length}/${mode} phones connected · ${tracked.length} positioned · ${mode === 2 ? "2-phone test · axis assumed" : flat ? "3-phone flat (Z = 0)" : "5-phone"}`);
      text("phone-owner", paused ? "Phone control paused" : owner ? `${owner.name} controls ${target.id.replace("_", " ")} · ${owner.gesture.replaceAll("_", " ")}` : "Waiting for fresh UWB positions");
      const hud = document.querySelector<HTMLElement>("#gesture-connection");
      if (hud) hud.textContent = live.length ? `PHONE CAMERAS · ${live.length} CONNECTED` : "WAITING FOR PHONES";
      document.querySelector<HTMLElement>("#gesture-name")!.textContent = owner?.gesture.replaceAll("_", " ") ?? "NO CONTROLLER";
      document.querySelector<HTMLElement>("#gesture-confidence")!.textContent = owner ? `${owner.name} · ${Math.round(owner.gesture_confidence * 100)}%` : "Join one room and wait for its UWB map";
      document.querySelector<HTMLElement>("#gesture-progress-fill")!.style.width = `${control.progress * 100}%`;
      const action = control.action;
      if (!action || !owner || paused || document.hidden) return;
      applyPhoneAction(target, action, owner, home, actual, band);
      document.querySelector<HTMLElement>("#gesture-action")!.textContent = `${owner.name.toUpperCase()} · ${action.replaceAll("_", " ").toUpperCase()}`;
    } catch (error) {
      if (closed) return;
      reset(); layer.update({ operators: [] }); scene.update([]);
      text("phone-status", error instanceof Error ? error.message : "Phone receiver disconnected");
      text("phone-owner", "Drone holding — no live phone feed");
    } finally { busy = false; }
  };
  const visibility = () => { if (document.hidden) reset(); };
  document.addEventListener("visibilitychange", visibility);
  const interval = window.setInterval(() => { void tick(); }, 100);
  void tick();
  return () => { closed = true; window.clearInterval(interval); reset(); layer.update({ operators: [] }); scene.update([]); panel.remove(); guide.remove(); scene.dispose(); document.removeEventListener("visibilitychange", visibility); };
}
