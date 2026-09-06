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
    <p id="phone-feed-warning" class="phone-help"></p>
    <label>Live room <select id="phone-room"></select></label>
    <div class="phone-view-controls"><button id="phone-closer" title="Zoom in">＋</button><button id="phone-wider" title="Zoom out">−</button><button id="phone-frame">Frame group</button></div>
    <p class="phone-scale">1 square = 1 m · people 1.75 m · drone 0.8 m rotor envelope</p>
    <p id="phone-height"></p>
    <p id="phone-action-status" class="phone-help" role="status"></p>
    <p id="phone-owner">Waiting for the group</p><div id="phone-members"></div>
    <button id="phone-stop" type="button">Stop drone</button>
    <details><summary>Group alignment</summary>
      <label>Stationary anchor <select id="phone-anchor"></select></label>
      <label>Rotate group <input id="phone-rotation" type="range" min="-180" max="180" value="0" step="1"></label>
      <label><input id="phone-mirror" type="checkbox"> Mirror group</label>
      <p>Metres are measured by UWB. The anchor stays 5 m north of the drone’s starting point. Keep it still and align the group to the map. Two-phone mode assumes a vertical map line, X = Z = 0, using real UWB distance. Three-phone mode sets Z = 0.<!-- Legacy: Five-phone mode preserves relative XYZ; its third axis is not gravity height. --></p>
    </details>
    <p class="phone-help">The gold person controls the drone. Anyone can hold a fist to follow, three fingers to orbit, or an open palm to hover above themselves. Drag the map to look around; Frame group restores the close view.</p>`;
  document.body.append(panel);
  const guide = document.createElement("section");
  guide.id = "phone-guide";
  guide.innerHTML = `<strong>GESTURES → DRONE</strong>
    <p>Use the phone’s <b>front camera</b>. Keep the whole hand in the selfie preview. Hold steady for <b>about one second</b> for recognition and the command hold.</p>
    <dl>
      <div data-gesture="One_Finger_Up"><dt>☝️ One finger</dt><dd>Index extended, other fingers curled. Any direction. Climb at 2 m/s while held. Lower your hand to stop.</dd></div>
      <div data-gesture="Two_Fingers_Down"><dt>✌️ Two fingers</dt><dd>Index + middle extended, ring + pinky curled. Any direction. Descend at 1 m/s to the hover floor while held. Lower your hand to stop.</dd></div>
      <div data-gesture="Three_Finger_Orbit"><dt>Three fingers · orbit</dt><dd>Index + middle + ring extended, pinky curled. Orbit your mapped position at a 2 m radius, 3.5 m above your base, while held. Release to stop.</dd></div>
      <div data-gesture="Open_Palm"><dt>✋ Open palm · hover above you</dt><dd>Open your hand with all fingers and thumb extended. Fly to 3.5 m above your mapped base and hover while held. Release to stop.</dd></div>
      <div data-gesture="Closed_Fist"><dt>✊ Closed fist</dt><dd>Four fingers curled; thumb position is ignored. Fly above you, then follow your mapped position at up to 2 m/s, about 3.5 m above your base. You can lower your hand. Another person’s held fist switches follow to them. Use Stop drone to cancel follow.</dd></div>
    </dl>
    <p><b>Release to stop vertical movement; fist-follow stays on.</b> One/two fingers leave follow and adjust altitude; three fingers/open palm switch to orbit/hover; release to hover at that height. The fist caller keeps control until another fist/three-finger/open-palm claim, Stop drone, or signal loss. Before a fist claim, the closest person controls. Stop drone pauses movement and releases control. Follow uses the mapped UWB position, so keep the stationary anchor still.</p>`;
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
      if (paused || document.hidden || mode === 5) controller.reset();
      const control = controller.update(paused || document.hidden || mode === 5 ? [] : tracked, actual, performance.now());
      if (control.stop || paused || document.hidden || mode === 5) halt();
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
        const health = phone.age > PHONE_TIMEOUT ? "signal lost" : !phone.pos ? "waiting for UWB" : phone.geometryAge > 8 ? "position expired" : phone.confidence < 0.55 && phone.gesture !== "None" ? "pose confidence too low; show the full hand"
          : !tracked.some(p => p.operator_id === phone.id) ? "check anchor and room mode"
          : owner && owner.operator_id !== phone.id && phone.gesture !== "None" ? `${owner.name} owns control; fist, three fingers or open palm can request it`
          : `${phone.geometryAge.toFixed(1)} s position age`;
        const claim = controller.claimProgress(phone.id);
        const gesture = phone.gesture.replaceAll("_", " ");
        const claiming = claim > 0 ? ` · calling drone ${Math.round(claim * 100)}%` : "";
        row.textContent = `${phone.name}${phone.id === owner?.operator_id ? " · controlling" : ""} — ${gesture}${claiming} · ${health}`;
        return row;
      }));
      const reportedMembers = Math.max(0, ...live.map(phone => phone.members));
      text("phone-feed-warning", reportedMembers > live.length
        ? "Your phones see each other, but this Mac is missing a feed. Check Visualizer host on the missing phone: it must be this Mac’s Wi-Fi address, port 9870."
        : "");
      text("phone-status", all.length === 0
        ? "No phone telemetry. Set Visualizer host on each phone to this Mac’s Wi-Fi IP:9870, connect to the same Wi-Fi, and keep the app open."
        : `${reportedMembers}/${mode} in phone room · ${live.length}/${mode} feeds reaching Mac · ${tracked.length} positioned · ${mode === 2 ? "2-phone test · axis assumed" : flat ? "3-phone flat (Z = 0)" : "unsupported legacy room"}`);
      text("phone-owner", paused ? "Phone control paused" : owner ? control.following ? `Following ${owner.name} · Stop drone to cancel` : `${owner.name} controls ${target.id.replace("_", " ")} · ${owner.gesture.replaceAll("_", " ")}` : "Waiting for fresh UWB positions");
      const hud = document.querySelector<HTMLElement>("#gesture-connection");
      if (hud) hud.textContent = live.length ? `PHONE CAMERAS · ${live.length} CONNECTED` : "WAITING FOR PHONES";
      document.querySelector<HTMLElement>("#gesture-name")!.textContent = control.following ? `FOLLOWING ${owner!.name}` : owner?.gesture.replaceAll("_", " ") ?? "NO CONTROLLER";
      document.querySelector<HTMLElement>("#gesture-confidence")!.textContent = owner ? `${owner.name} · ${owner.gesture === "None" ? "no stable pose" : "stable hand pose"}` : "Join one room and wait for its UWB map";
      document.querySelector<HTMLElement>("#gesture-progress-fill")!.style.width = `${control.progress * 100}%`;
      const action = control.action;
      const hold = Math.max(control.progress, ...tracked.map(p => controller.claimProgress(p.operator_id)));
      const atOverhead = owner && Math.hypot(owner.position.x - actual.x, owner.position.y - actual.y,
        Math.max(band.floor, Math.min(band.ceiling, owner.position.z + 3.5)) - actual.z) < 0.15;
      const reason = paused ? "Movement paused — press Resume phone control."
        : document.hidden ? "Movement paused while this browser tab is hidden."
        : mode === 5 && phones.length ? "This demo supports two or three phones. Create a new room on the updated iOS app."
        : !live.length ? "No live phone feed — check Wi-Fi, Visualizer host and that the phone app is open."
        : !tracked.length ? "No fresh mapped position — keep the anchor still and wait for a complete UWB range cycle."
        : control.reason ? control.reason
        : !owner ? "No controller — hold one gesture steadily."
        : target.collisionBlocked ? "Movement blocked by an obstacle. Change direction or climb if clear."
        : action === "takeoff" && actual.z >= band.ceiling ? "Ceiling reached — descent is still available."
        : action === "land" && actual.z <= band.floor ? "Hover floor reached — the drone stays above people."
        : (action === "follow" || action === "hover_overhead") && atOverhead ? `Holding above ${owner.name}; already at the requested position.`
        : action ? `${action.replaceAll("_", " ")} active · ${owner.name}`
        : hold > 0 && hold < 1 ? `Keep holding — confirming gesture ${Math.round(hold * 100)}%.`
        : owner.gesture === "None" ? "Holding position — no stable gesture. Show your whole hand for about one second."
        : "Waiting for a stable supported gesture; only the controlling operator can climb or descend.";
      text("phone-action-status", reason);
      if (!action || !owner || paused || document.hidden) return;
      applyPhoneAction(target, action, owner, home, actual, band);
      document.querySelector<HTMLElement>("#gesture-action")!.textContent = `${owner.name.toUpperCase()} · ${action.replaceAll("_", " ").toUpperCase()}`;
    } catch (error) {
      if (closed) return;
      reset(); layer.update({ operators: [] }); scene.update([]);
      text("phone-status", error instanceof Error ? error.message : "Phone receiver disconnected");
      text("phone-owner", "Drone holding — no live phone feed");
      text("phone-action-status", "Connection lost. Check the receiver, Wi-Fi and Visualizer host; movement has stopped.");
      text("phone-feed-warning", "");
    } finally { busy = false; }
  };
  const visibility = () => { if (document.hidden) reset(); };
  document.addEventListener("visibilitychange", visibility);
  const interval = window.setInterval(() => { void tick(); }, 100);
  void tick();
  return () => { closed = true; window.clearInterval(interval); reset(); layer.update({ operators: [] }); scene.update([]); panel.remove(); guide.remove(); scene.dispose(); document.removeEventListener("visibilitychange", visibility); };
}
