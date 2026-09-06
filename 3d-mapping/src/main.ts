import { startBatteryPanel } from "./battery-panel";
import "cesium/Build/Cesium/Widgets/widgets.css";
import "./style.css";
import * as Cesium from "cesium";
import { DroneController } from "./drone-controller";
import { Fleet, DRONE_COLORS } from "./fleet";
import { formationSlots } from "./formation";
import { collisionWarning } from "./collision";
import { blendHeading, fleetCameraFrame, idleCameraDriftRate, screenRelativeMovement } from "./cinematic-camera";
import { parseMission, sampleMission, type MissionStep } from "./mission";
import { startRuntimeMode } from "./runtime/index";
import { previewCoordinate } from "./flight-preview";
import { COMMANDS, parseCommandSequence, type CommandIntent } from "./command-console";
import { compileMissionSequence, isFlightSequenceIntent } from "./mission-sequence";
import { startGestureCamera, type BrowserGestureState } from "./gesture-camera";
import { startPhoneDemo } from "./phone-demo";

const monument = { latitude: 38.8895, longitude: -77.0353, altitude: 80 };
// The south side of the monument plaza: visibly at the base, but outside the
// obelisk geometry so the collision layer can launch the aircraft safely.
const home = { latitude: 38.88928, longitude: -77.0353, altitude: -24 };
const token = import.meta.env.VITE_CESIUM_ION_ACCESS_TOKEN as string | undefined;
const status = document.querySelector<HTMLParagraphElement>("#world-status")!;
const commandStatus = document.querySelector<HTMLElement>("#command-status")!;
const missionInput = document.querySelector<HTMLTextAreaElement>("#mission-json")!;
const commandDrawer = document.querySelector<HTMLDetailsElement>("#command-drawer")!;
const commandForm = document.querySelector<HTMLFormElement>("#command-form")!;
const commandInput = document.querySelector<HTMLInputElement>("#command-input")!;
const commandSuggestions = document.querySelector<HTMLDivElement>("#command-suggestions")!;
const hud = {
  drone: document.querySelector<HTMLElement>("#hud-drone")!,
  latitude: document.querySelector<HTMLElement>("#hud-latitude")!,
  longitude: document.querySelector<HTMLElement>("#hud-longitude")!,
  altitude: document.querySelector<HTMLElement>("#hud-altitude")!,
  speed: document.querySelector<HTMLElement>("#hud-speed")!,
  state: document.querySelector<HTMLElement>("#hud-state")!,
  fleet: document.querySelector<HTMLElement>("#hud-fleet")!,
};
new MutationObserver(() => {
  if (commandStatus.textContent) commandInput.placeholder = commandStatus.textContent;
}).observe(commandStatus, { childList: true, characterData: true, subtree: true });
const runtimeApiBase = ((import.meta.env.VITE_RUNTIME_URL as string | undefined) ?? "http://127.0.0.1:8000").replace(/\/$/, "");
const runtimeMode = new URLSearchParams(window.location.search).get("mode") === "runtime";
const phoneMode = !runtimeMode && new URLSearchParams(window.location.search).get("input") !== "camera";
document.body.classList.toggle("phone-mode", phoneMode);
if (phoneMode) hud.altitude.previousElementSibling!.textContent = "Height";
document.body.classList.toggle("runtime-mode", runtimeMode);
const gestureHud = {
  root: document.querySelector<HTMLElement>("#gesture-hud")!,
  connection: document.querySelector<HTMLElement>("#gesture-connection")!,
  name: document.querySelector<HTMLElement>("#gesture-name")!,
  confidence: document.querySelector<HTMLElement>("#gesture-confidence")!,
  progress: document.querySelector<HTMLElement>("#gesture-progress-fill")!,
  action: document.querySelector<HTMLElement>("#gesture-action")!,
};

const panelTabs = [...document.querySelectorAll<HTMLButtonElement>('[role="tab"]')];
function showControlTab(name: "drone" | "batches"): void {
  for (const tab of panelTabs) {
    const selected = tab.id === `tab-${name}`;
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
    document.getElementById(tab.getAttribute("aria-controls")!)!.hidden = !selected;
  }
}
panelTabs.forEach((tab, index) => {
  tab.addEventListener("click", () => { clearInput(); showControlTab(index === 0 ? "drone" : "batches"); });
  tab.addEventListener("keydown", event => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === "Home" ? 0 : event.key === "End" ? 1 : 1 - index;
    panelTabs[next].click(); panelTabs[next].focus();
  });
});

function showPlacementControls(batch: boolean): void {
  document.getElementById("drone-type-fields")!.hidden = placementMode === "command";
  const name = batch ? "batches" : "drone";
  showControlTab(name);
  document.getElementById(`panel-${name}`)!.append(deploymentPanel);
  commandDrawer.open = true;
  for (const id of ["batch-count", "formation-spacing"]) {
    document.getElementById(id)!.hidden = !batch;
    document.querySelector<HTMLLabelElement>(`label[for="${id}"]`)!.hidden = !batch;
  }
}

missionInput.value = JSON.stringify(sampleMission, null, 2);
if (token) Cesium.Ion.defaultAccessToken = token;

const viewer = new Cesium.Viewer("cesiumContainer", {
  animation: false,
  shouldAnimate: true,
  baseLayerPicker: false,
  geocoder: false,
  homeButton: false,
  infoBox: false,
  navigationHelpButton: false,
  sceneModePicker: false,
  selectionIndicator: false,
  timeline: false,
  baseLayer: new Cesium.ImageryLayer(new Cesium.GridImageryProvider({
    cells: 16,
    color: Cesium.Color.fromCssColorString("#6b9c88"),
    backgroundColor: Cesium.Color.fromCssColorString("#233d34"),
  })),
});
const placeGeocoder = token ? new Cesium.IonGeocoderService({
  scene: viewer.scene,
  accessToken: token,
  geocodeProviderType: Cesium.IonGeocodeProviderType.GOOGLE,
}) : undefined;
if (runtimeMode) startRuntimeMode(viewer);

viewer.scene.globe.enableLighting = false;
viewer.scene.globe.maximumScreenSpaceError = 3;
viewer.scene.postProcessStages.fxaa.enabled = true;
viewer.scene.msaaSamples = 1;
const fleet = new Fleet(viewer);
const replayButton = document.querySelector<HTMLButtonElement>("#run-all-paths")!;
const stopReplayButton = document.querySelector<HTMLButtonElement>("#stop-paths")!;
const replayTime = document.querySelector<HTMLParagraphElement>("#playback-time")!;
const replayStatus = document.querySelector<HTMLParagraphElement>("#playback-status")!;
let drone: DroneController | undefined;
const droneSelect = document.querySelector<HTMLSelectElement>("#selected-drone")!;
const speedInput = document.querySelector<HTMLInputElement>("#drone-speed")!;
const speedStatus = document.querySelector<HTMLParagraphElement>("#speed-status")!;
speedInput.addEventListener("input", () => {
  if (!drone) return;
  const value = speedInput.valueAsNumber;
  if (!Number.isFinite(value) || value <= 0) {
    speedInput.setAttribute("aria-invalid", "true");
    speedStatus.textContent = "Enter a speed greater than zero in mph. Previous speed remains active.";
    return;
  }
  speedInput.removeAttribute("aria-invalid");
  if (drone.speedMph !== value) fleet.checkpoint("speed change");
  drone.speedMph = value;
  speedStatus.textContent = `${value} mph · ${(value * 0.44704).toFixed(2)} m/s`;
});
const deploymentPanel = document.querySelector<HTMLDivElement>("#deployment")!;
const deploymentStatus = document.querySelector<HTMLParagraphElement>("#deployment-status")!;
const heightInput = document.querySelector<HTMLInputElement>("#deployment-height")!;
const confirmDeployment = document.querySelector<HTMLButtonElement>("#confirm-deployment")!;
let deploying = false;
let pickedSurface: Cesium.Cartographic | undefined;
let previews: Cesium.Entity[] = [];
let placementMode: "single" | "bulk" | "command" = "single";
let quickPlacement = false;
const selectedIds = new Set<string>();
const batchControlButton = document.querySelector<HTMLButtonElement>("#control-batch")!;
const batchSpeedInput = document.querySelector<HTMLInputElement>("#batch-speed")!;
const batchStatus = document.querySelector<HTMLParagraphElement>("#batch-status")!;
const memberList = document.querySelector<HTMLDivElement>("#group-members")!;
const savedGroups = document.querySelector<HTMLSelectElement>("#saved-groups")!;
const groupStatus = document.querySelector<HTMLParagraphElement>("#group-status")!;
const countInput = document.querySelector<HTMLInputElement>("#batch-count")!;
const spacingInput = document.querySelector<HTMLInputElement>("#formation-spacing")!;
let pendingIds: string[] = [];
let pendingGroup = "";
const activeCameraKeys = new Set<string>();
const controlDroneButton = document.querySelector<HTMLButtonElement>("#control-drone")!;
const rotationControls = document.querySelector<HTMLDivElement>("#rotation-controls")!;
let controllingDrone = false;
const pilotView = { heading: 0 };
const pilotCameraSelect = document.querySelector<HTMLSelectElement>("#pilot-camera")!;
let pilotCameraMode = "third";
pilotCameraSelect.addEventListener("change", () => {
  pilotCameraMode = pilotCameraSelect.value;
  clearInput();
  if (controllingDrone) viewer.canvas.focus();
});
let steering = false;

function clearInput(): void {
  activeCameraKeys.clear();
  steering = false;
  drone?.stopManualMotion();
  for (const member of fleet.manualBatch) member.stopManualMotion();
}

for (const [id, code] of [["rotate-left", "KeyQ"], ["rotate-right", "KeyE"]] as const) {
  const button = document.querySelector<HTMLButtonElement>(`#${id}`)!;
  button.addEventListener("pointerdown", event => {
    event.preventDefault();
    if (controllingDrone) activeCameraKeys.add(code);
  });
  for (const eventName of ["pointerup", "pointercancel", "pointerleave"]) {
    button.addEventListener(eventName, () => activeCameraKeys.delete(code));
  }
}

function releaseDrone(): void {
  clearInput();
  if (fleet.manualBatch.length) {
    fleet.releaseBatch();
    batchStatus.textContent = "Batch released. Click its PICK UP BATCH marker or Control selected batch to continue the same paths.";
  } else if (controllingDrone) drone?.setManualControl(false);
  controllingDrone = false;
  activeCameraKeys.clear();
  controlDroneButton.textContent = "Fly";
  controlDroneButton.setAttribute("aria-pressed", "false");
  batchControlButton.textContent = "Control selected batch";
  batchControlButton.setAttribute("aria-pressed", "false");
}
const monumentTarget = Cesium.Cartesian3.fromDegrees(monument.longitude, monument.latitude, 20);
const orbitCamera = { heading: Cesium.Math.toRadians(30), pitch: Cesium.Math.toRadians(-22), range: 450 };
let cameraMode: "auto" | "placement" = "auto";
let isOrbitDragging = false;
const cameraHandler = new Cesium.ScreenSpaceEventHandler(viewer.canvas);

function applyOrbitCamera(): void {
  viewer.camera.lookAt(monumentTarget, new Cesium.HeadingPitchRange(orbitCamera.heading, orbitCamera.pitch, orbitCamera.range));
}

function activateOrbitCamera(): void {
  cancelDeployment();
  releaseDrone();
  activeCameraKeys.clear();
  viewer.camera.cancelFlight();
  cameraMode = "auto";
  viewer.trackedEntity = undefined;
  viewer.scene.screenSpaceCameraController.enableInputs = false;
  applyOrbitCamera();
  refreshFleet();
  commandStatus.textContent = "Automatic camera active.";
}

function flyToFreeCameraOverview(): void {
  releaseDrone();
  cameraMode = "auto";
  isOrbitDragging = false;
  viewer.camera.cancelFlight();
  viewer.trackedEntity = undefined;
  viewer.camera.lookAtTransform(Cesium.Matrix4.IDENTITY);
  viewer.scene.screenSpaceCameraController.enableInputs = false;
  commandStatus.textContent = fleet.drones.size ? "Camera is framing the fleet." : "Camera is orbiting the Washington Monument.";
  refreshFleet();
}

cameraHandler.setInputAction(() => { if (phoneMode) return; isOrbitDragging = cameraMode === "auto"; steering = controllingDrone; }, Cesium.ScreenSpaceEventType.LEFT_DOWN);
cameraHandler.setInputAction(() => { isOrbitDragging = false; steering = false; }, Cesium.ScreenSpaceEventType.LEFT_UP);
window.addEventListener("pointerup", () => { steering = false; isOrbitDragging = false; });
cameraHandler.setInputAction((movement: { startPosition: Cesium.Cartesian2; endPosition: Cesium.Cartesian2 }) => {
  if (phoneMode) return;
  if (controllingDrone && steering) {
    pilotView.heading += (movement.endPosition.x - movement.startPosition.x) * 0.005;
    return;
  }
  if (!isOrbitDragging || cameraMode !== "auto") return;
  orbitCamera.heading -= (movement.endPosition.x - movement.startPosition.x) * 0.008;
  orbitCamera.pitch = Cesium.Math.clamp(orbitCamera.pitch + (movement.endPosition.y - movement.startPosition.y) * 0.006, Cesium.Math.toRadians(-85), Cesium.Math.toRadians(-5));
  applyOrbitCamera();
}, Cesium.ScreenSpaceEventType.MOUSE_MOVE);
cameraHandler.setInputAction((delta: number) => {
  if (phoneMode) return;
  if (controllingDrone) return;
  if (cameraMode !== "auto" || fleet.drones.size > 0) return;
  orbitCamera.range = Cesium.Math.clamp(orbitCamera.range + delta * 0.22, 80, 4_000);
  applyOrbitCamera();
}, Cesium.ScreenSpaceEventType.WHEEL);
viewer.scene.screenSpaceCameraController.enableInputs = false;
applyOrbitCamera();
commandStatus.textContent = "Camera is orbiting the Washington Monument. Deploy when ready.";

function refreshFleet(): void {
  droneSelect.replaceChildren();
  if (!fleet.drones.size) droneSelect.add(new Option("No drones deployed", ""));
  let index = 0;
  for (const item of fleet.drones.values()) {
    index++;
    const color = DRONE_COLORS.find(color => color.hex === item.colorHex);
    droneSelect.add(new Option(`${item.id.replace("_", " ")} · ${item.droneType === "survey" ? "Survey" : "Normal"} · ${color?.name ?? item.colorHex}`, item.id));
  }
  droneSelect.value = drone?.id ?? "";
  droneSelect.disabled = deploying || !drone;
  speedInput.disabled = deploying || !drone || fleet.replay.running || fleet.manualBatch.length > 0;
  speedInput.value = String(drone?.speedMph ?? 60);
  speedInput.removeAttribute("aria-invalid");
  speedStatus.textContent = `${drone?.speedMph ?? 60} mph · ${((drone?.speedMph ?? 60) * 0.44704).toFixed(2)} m/s`;
  for (const id of ["run-mission", "reset-mission", "control-drone"]) {
    document.querySelector<HTMLButtonElement>(`#${id}`)!.disabled = deploying || !drone || fleet.replay.running;
  }
  document.querySelector<HTMLButtonElement>("#deploy-drone")!.disabled = fleet.replay.running;
  replayButton.disabled = deploying || fleet.replay.running || !fleet.drones.size;
  stopReplayButton.disabled = !fleet.replay.running;
  controlDroneButton.disabled ||= fleet.manualBatch.length > 0;
  rotationControls.hidden = !controllingDrone;
  document.querySelector<HTMLButtonElement>("#bulk-deploy")!.disabled = fleet.replay.running;
  refreshGroups();
  updateTelemetry();
}

let lastPhoneTelemetry: { point: Cesium.Cartesian3; at: number } | undefined;
function updateTelemetry(): void {
  const snapshot = drone?.snapshot();
  hud.drone.textContent = drone?.id.replace("_", " ") ?? "None";
  hud.latitude.textContent = snapshot ? snapshot.latitude.toFixed(5) : "—";
  hud.longitude.textContent = snapshot ? snapshot.longitude.toFixed(5) : "—";
  hud.altitude.textContent = snapshot ? `${snapshot.altitude.toFixed(1)} m` : "—";
  hud.speed.textContent = drone ? `${drone.speedMph.toFixed(1)} mph` : "—";
  if (phoneMode && snapshot) {
    hud.altitude.textContent = `${Math.max(0, snapshot.altitude - home.altitude).toFixed(1)} m`;
    const point = Cesium.Cartesian3.fromDegrees(snapshot.longitude, snapshot.latitude, snapshot.altitude);
    const at = performance.now();
    const speed = lastPhoneTelemetry && at > lastPhoneTelemetry.at
      ? Cesium.Cartesian3.distance(point, lastPhoneTelemetry.point) / ((at - lastPhoneTelemetry.at) / 1000) : 0;
    hud.speed.textContent = `${speed.toFixed(1)} m/s`;
    lastPhoneTelemetry = { point, at };
  }
  hud.state.textContent = snapshot?.state.replaceAll("_", " ") ?? "Idle";
  hud.fleet.textContent = String(fleet.drones.size);
}

function refreshGroups(): void {
  const locked = deploying || fleet.replay.running || fleet.manualBatch.length > 0;
  memberList.replaceChildren();
  for (const item of fleet.drones.values()) {
    const row = document.createElement("label"); row.className = "member-row";
    const checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.checked = selectedIds.has(item.id); checkbox.disabled = locked;
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) selectedIds.add(item.id); else selectedIds.delete(item.id);
      savedGroups.value = ""; refreshGroups();
    });
    const swatch = document.createElement("span"); swatch.className = "member-swatch"; swatch.style.background = item.colorHex;
    row.append(checkbox, swatch, document.createTextNode(`${item.id.replace("_", " ")} · ${item.droneType}`)); memberList.append(row);
  }
  const previousGroup = savedGroups.value;
  savedGroups.replaceChildren(new Option("Custom selection", ""));
  for (const [name, group] of fleet.groups) savedGroups.add(new Option(`${name} (${group.ids.length})`, name));
  savedGroups.value = previousGroup;
  savedGroups.disabled = locked;
  for (const id of ["select-all", "select-none", "save-group", "choose-destination"]) {
    document.querySelector<HTMLButtonElement>(`#${id}`)!.disabled = locked || (id === "choose-destination" || id === "save-group" ? !selectedIds.size : !fleet.drones.size);
  }
  groupStatus.textContent = `${selectedIds.size} drone${selectedIds.size === 1 ? "" : "s"} selected. Click Choose destination to assign arrival slots.`;
  batchControlButton.disabled = deploying || fleet.replay.running || (!fleet.manualBatch.length && !selectedIds.size);
  document.querySelector<HTMLButtonElement>("#apply-batch-speed")!.disabled = deploying || fleet.replay.running || !selectedIds.size;
  batchSpeedInput.disabled = deploying || fleet.replay.running;
}

function beginBatchControl(ids: string[]): void {
  showControlTab("batches");
  const speed = batchSpeedInput.valueAsNumber;
  if (!Number.isFinite(speed) || speed <= 0) { batchStatus.textContent = "Enter a positive batch speed in mph."; return; }
  flyToFreeCameraOverview();
  fleet.checkpoint("batch flight");
  fleet.takeBatch(ids, speed);
  drone = fleet.manualBatch[0];
  selectedIds.clear(); for (const id of ids) selectedIds.add(id);
  controllingDrone = true;
  pilotView.heading = drone.heading;
  viewer.scene.screenSpaceCameraController.enableInputs = false;
  batchControlButton.textContent = "Release batch";
  batchControlButton.setAttribute("aria-pressed", "true");
  refreshFleet();
  batchStatus.textContent = `Controlling ${ids.length} drones at ${speed} mph. WASD moves together; Q/E steers; Space/Shift or R/F changes altitude. Esc releases.`;
  viewer.canvas.focus();
}
batchControlButton.addEventListener("click", () => {
  if (fleet.manualBatch.length) flyToFreeCameraOverview(); else beginBatchControl([...selectedIds]);
});
document.querySelector<HTMLButtonElement>("#apply-batch-speed")!.addEventListener("click", () => {
  try {
    if (!Number.isFinite(batchSpeedInput.valueAsNumber) || batchSpeedInput.valueAsNumber <= 0) throw new Error("Enter a positive speed.");
    fleet.checkpoint("batch speed change");
    fleet.setBatchSpeed(fleet.manualBatch.length ? fleet.manualBatch.map(d => d.id) : [...selectedIds], batchSpeedInput.valueAsNumber);
    refreshFleet(); batchStatus.textContent = `Batch speed set to ${batchSpeedInput.value} mph.`;
  } catch (error) { batchStatus.textContent = error instanceof Error ? error.message : "Invalid speed."; }
});
document.querySelector<HTMLButtonElement>("#select-all")!.addEventListener("click", () => { for (const id of fleet.drones.keys()) selectedIds.add(id); savedGroups.value = ""; refreshGroups(); });
document.querySelector<HTMLButtonElement>("#select-none")!.addEventListener("click", () => { selectedIds.clear(); savedGroups.value = ""; refreshGroups(); });
savedGroups.addEventListener("change", () => { selectedIds.clear(); for (const id of fleet.groups.get(savedGroups.value)?.ids ?? []) selectedIds.add(id); refreshGroups(); });
document.querySelector<HTMLButtonElement>("#save-group")!.addEventListener("click", () => {
  try {
    const name = document.querySelector<HTMLInputElement>("#group-name")!.value.trim();
    if (!name || !selectedIds.size) throw new Error("Select drones and enter a group name.");
    fleet.checkpoint("save group");
    fleet.saveGroup(name, [...selectedIds]); refreshFleet(); savedGroups.value = name;
    groupStatus.textContent = `${name} saved. Its drones now share a color.`;
  } catch (error) { groupStatus.textContent = error instanceof Error ? error.message : "Unable to save group."; }
});

replayButton.addEventListener("click", () => {
  flyToFreeCameraOverview();
  fleet.startReplay(performance.now() / 1000);
  replayStatus.textContent = fleet.replay.total
    ? `All ${fleet.replay.total} routed drones launched together. Drones without a path stay parked.`
    : "No recorded paths yet. Pilot a drone to record its route first.";
  refreshFleet();
});
stopReplayButton.addEventListener("click", () => {
  fleet.stopReplay();
  replayStatus.textContent = "Playback stopped. Recorded paths are preserved.";
  refreshFleet();
});

function cancelDeployment(): void {
  const wasDeploying = deploying;
  deploying = false;
  quickPlacement = false;
  pickedSurface = undefined;
  for (const preview of previews) viewer.entities.remove(preview);
  previews = [];
  deploymentPanel.hidden = true;
  confirmDeployment.disabled = true;
  if (wasDeploying) {
    cameraMode = "auto";
    viewer.scene.screenSpaceCameraController.enableInputs = false;
  }
  refreshFleet();
}

function updatePreview(): void {
  for (const preview of previews) viewer.entities.remove(preview);
  previews = [];
  const height = heightInput.valueAsNumber;
  confirmDeployment.disabled = !pickedSurface || !heightInput.checkValidity() || !Number.isFinite(height) || !spacingInput.checkValidity() || !Number.isFinite(spacingInput.valueAsNumber) || (placementMode === "bulk" && (!countInput.checkValidity() || !Number.isInteger(countInput.valueAsNumber))) || (placementMode === "command" && pendingIds.length > 100);
  if (confirmDeployment.disabled || !pickedSurface) return;
  const center = { longitude: Cesium.Math.toDegrees(pickedSurface.longitude), latitude: Cesium.Math.toDegrees(pickedSurface.latitude), altitude: pickedSurface.height + height };
  const count = placementMode === "command" ? pendingIds.length : placementMode === "bulk" ? countInput.valueAsNumber : 1;
  const slots = formationSlots(center, count, spacingInput.valueAsNumber);
  slots.forEach((slot, index) => {
    const position = Cesium.Cartesian3.fromDegrees(slot.longitude, slot.latitude, slot.altitude);
    const member = placementMode === "command" ? fleet.drones.get(pendingIds[index]) : undefined;
    const sharedColor = fleet.groups.get(pendingGroup)?.color ?? fleet.drones.get(pendingIds[0])?.colorHex;
    const color = Cesium.Color.fromCssColorString(placementMode === "command" ? sharedColor! : fleet.nextColor.hex);
    previews.push(viewer.entities.add({ position, point: { pixelSize: 10, color }, label: { text: member?.id ?? `${index + 1}`, pixelOffset: new Cesium.Cartesian2(0, -20), font: "12px system-ui", showBackground: true } }));
    if (member) {
      const start = member.snapshot();
      previews.push(viewer.entities.add({ polyline: { positions: [Cesium.Cartesian3.fromDegrees(start.longitude, start.latitude, start.altitude), position], width: 2, material: new Cesium.PolylineDashMaterialProperty({ color }), arcType: Cesium.ArcType.NONE } }));
    }
  });
  deploymentStatus.textContent = `${count} ${placementMode === "command" ? "arrival" : "starting"} slot(s), ${spacingInput.value} m apart. Click elsewhere to adjust, then confirm.`;
}

document.querySelector<HTMLButtonElement>("#deploy-drone")!.addEventListener("click", () => {
  cancelDeployment();
  flyToFreeCameraOverview();
  deploying = true;
  cameraMode = "placement";
  viewer.scene.screenSpaceCameraController.enableInputs = true;
  placementMode = "single";
  countInput.disabled = true;
  countInput.value = "1";
  heightInput.value = "20";
  document.querySelector<HTMLSelectElement>("#drone-type")!.value = "normal";
  commandDrawer.open = false;
  commandStatus.textContent = `Click the map to deploy ${fleet.nextColor.name} immediately.`;
  refreshFleet();
});
document.querySelector<HTMLButtonElement>("#bulk-deploy")!.addEventListener("click", () => {
  cancelDeployment(); flyToFreeCameraOverview(); deploying = true; placementMode = "bulk";
  cameraMode = "placement"; viewer.scene.screenSpaceCameraController.enableInputs = true;
  showPlacementControls(true);
  countInput.disabled = false; countInput.value = "10"; deploymentPanel.hidden = false; confirmDeployment.textContent = "Deploy batch here";
  deploymentStatus.textContent = "Choose a map location for the batch, then adjust number, height and spacing.";
  refreshFleet();
});
document.querySelector<HTMLButtonElement>("#choose-destination")!.addEventListener("click", () => {
  if (!selectedIds.size) return;
  pendingIds = [...selectedIds]; pendingGroup = savedGroups.value;
  cancelDeployment(); flyToFreeCameraOverview(); deploying = true; placementMode = "command";
  cameraMode = "placement"; viewer.scene.screenSpaceCameraController.enableInputs = true;
  showPlacementControls(true);
  countInput.disabled = true; countInput.value = String(pendingIds.length); deploymentPanel.hidden = false;
  confirmDeployment.textContent = "Send selected drones";
  deploymentStatus.textContent = "Click the destination, adjust height and spacing, and review the dashed routes before sending.";
  deploymentPanel.scrollIntoView({ block: "nearest" }); refreshFleet();
});
heightInput.addEventListener("input", updatePreview);
countInput.addEventListener("input", updatePreview);
spacingInput.addEventListener("input", updatePreview);
document.querySelector<HTMLButtonElement>("#cancel-deployment")!.addEventListener("click", cancelDeployment);
cameraHandler.setInputAction((event: { position: Cesium.Cartesian2 }) => {
  if (!deploying) {
    if (controllingDrone || fleet.replay.running) return;
    const picked = viewer.scene.pick(event.position);
    const batch = fleet.batchMarkers.get(picked?.id?.id);
    if (batch) {
      batchSpeedInput.value = String(fleet.drones.get(batch[0])!.speedMph);
      beginBatchControl(batch); return;
    }
    const id = typeof picked?.id?.id === "string" ? picked.id.id.match(/^drone_\d+/)?.[0] : undefined;
    if (id && fleet.drones.has(id)) { if (selectedIds.has(id)) selectedIds.delete(id); else selectedIds.add(id); savedGroups.value = ""; refreshGroups(); }
    return;
  }
  let picked: Cesium.Cartesian3 | undefined;
  if (viewer.scene.pickPositionSupported) picked = viewer.scene.pickPosition(event.position);
  if (!picked) {
    const ray = viewer.camera.getPickRay(event.position);
    if (ray) picked = viewer.scene.globe.pick(ray, viewer.scene);
  }
  if (!picked) {
    deploymentStatus.textContent = "Click a visible map surface; sky cannot be used as a starting point.";
    commandStatus.textContent = "Click a visible map surface; sky cannot be used for deployment.";
    return;
  }
  pickedSurface = Cesium.Cartographic.fromCartesian(picked);
  if (placementMode === "single") {
    completeDeployment();
    return;
  }
  updatePreview();
  if (quickPlacement && placementMode === "bulk") completeDeployment();
}, Cesium.ScreenSpaceEventType.LEFT_CLICK);

function completeDeployment(): void {
  if (!deploying || !pickedSurface || (placementMode !== "single" && confirmDeployment.disabled)) return;
  fleet.checkpoint(placementMode === "command" ? "destination command" : "deployment");
  const center = { latitude: Cesium.Math.toDegrees(pickedSurface.latitude), longitude: Cesium.Math.toDegrees(pickedSurface.longitude), altitude: pickedSurface.height + heightInput.valueAsNumber };
  if (placementMode === "command") {
    fleet.commandGroup(pendingIds, center, spacingInput.valueAsNumber, performance.now() / 1000, pendingGroup);
    cancelDeployment(); refreshFleet(); commandStatus.textContent = `${pendingIds.length} drones launched together; routes replaced. Shared color assigned.`;
    return;
  }
  const type = document.querySelector<HTMLSelectElement>("#drone-type")!.value === "survey" ? "survey" : "normal";
  const added = placementMode === "bulk" ? fleet.deployBulk(center, countInput.valueAsNumber, spacingInput.valueAsNumber, type) : [fleet.deploy(center, type)];
  drone = added[0]; selectedIds.clear(); for (const item of added) selectedIds.add(item.id);
  cancelDeployment(); selectDrone(drone.id);
  commandStatus.textContent = `${added.length} drone(s) deployed. Select Fly to pilot; the camera will keep the fleet framed.`;
}
confirmDeployment.addEventListener("click", completeDeployment);

function selectDrone(id: string): void {
  flyToFreeCameraOverview();
  drone = fleet.drones.get(id);
  if (drone) {
    pilotView.heading = drone.heading;
    // Keep the mission editor's target consistent when changing selection.
    try {
      const edited = JSON.parse(missionInput.value);
      if (edited && typeof edited === "object" && !Array.isArray(edited)) {
        edited.drone_id = drone.id;
        missionInput.value = JSON.stringify(edited, null, 2);
      }
    } catch { /* Preserve unfinished edits. */ }
  }
  refreshFleet();
}
droneSelect.addEventListener("change", () => selectDrone(droneSelect.value));
document.querySelector<HTMLButtonElement>("#reset-all")!.addEventListener("click", () => {
  fleet.checkpoint("reset all");
  flyToFreeCameraOverview();
  cancelDeployment();
  fleet.clear();
  selectedIds.clear();
  replayStatus.textContent = "Record paths by piloting drones, then run them together.";
  drone = undefined;
  refreshFleet();
  commandStatus.textContent = "All drones and routes cleared. Deploy a new drone to begin.";
});

async function loadWorld(): Promise<void> {
if (token) {
  try {
    const tileset = await Cesium.createGooglePhotorealistic3DTileset();
    tileset.maximumScreenSpaceError = 24;
    tileset.dynamicScreenSpaceError = true;
    viewer.scene.primitives.add(tileset);
    viewer.scene.globe.show = false;
    status.textContent = "Google Photorealistic 3D Tiles connected.";
  } catch (error) {
    console.error(error);
    status.textContent = "Google tiles could not load; showing the offline grid globe.";
  }
} else {
  status.textContent = "Offline grid globe active. No token needed to simulate; an ion token enables the 3D city.";
}
}
const worldReady = loadWorld();

async function groundStartingHome(): Promise<void> {
  const location = Cesium.Cartographic.fromDegrees(home.longitude, home.latitude, home.altitude);
  let height: number | undefined;
  if (token) {
    try {
      const [sampled] = await viewer.scene.sampleHeightMostDetailed([location], viewer.entities.values, 0.5);
      height = sampled?.height;
    } catch { /* Keep the surveyed DC ellipsoid-height fallback below. */ }
  } else {
    try { height = viewer.scene.globe.getHeight(location); } catch { /* Use fallback. */ }
  }
  // The fallback globe is the zero-height ellipsoid, including before its tiles load.
  if (!Number.isFinite(height) && viewer.scene.globe.show) height = 0;
  if (Number.isFinite(height)) home.altitude = height! + (phoneMode ? 0 : 0.62);
}

document.querySelector<HTMLButtonElement>("#run-mission")!.addEventListener("click", () => {
  try {
    const mission = parseMission(missionInput.value);
    const target = fleet.drones.get(mission.drone_id);
    if (!target) throw new Error(`Deploy ${mission.drone_id} before sending it a mission.`);
    if (controllingDrone) flyToFreeCameraOverview();
    fleet.checkpoint("mission");
    target.run(mission);
    commandStatus.textContent = "Mission accepted.";
  } catch (error) {
    commandStatus.textContent = error instanceof Error ? error.message : "Mission could not be parsed.";
  }
});
document.querySelector<HTMLButtonElement>("#reset-mission")!.addEventListener("click", () => {
  fleet.checkpoint("reset drone");
  if (controllingDrone) flyToFreeCameraOverview();
  drone?.reset();
  commandStatus.textContent = "Drone reset to its home coordinate.";
});
document.querySelector<HTMLButtonElement>("#orbit-monument")!.addEventListener("click", activateOrbitCamera);
document.querySelector<HTMLButtonElement>("#free-camera")!.addEventListener("click", () => {
  cancelDeployment();
  flyToFreeCameraOverview();
  viewer.canvas.focus();
});
controlDroneButton.addEventListener("click", () => {
  if (!drone || deploying) return;
  if (controllingDrone) {
    flyToFreeCameraOverview();
    commandStatus.textContent = "Drone hovering. Automatic fleet camera active.";
    return;
  }
  flyToFreeCameraOverview();
  controllingDrone = true;
  fleet.checkpoint("manual flight");
  drone.setManualControl(true);
  viewer.scene.screenSpaceCameraController.enableInputs = false;
  controlDroneButton.textContent = "Release drone";
  controlDroneButton.setAttribute("aria-pressed", "true");
  commandStatus.textContent = "PILOT MODE · WASD follows the screen · Q/E turns · Space/Shift moves up/down · Esc releases.";
  viewer.canvas.focus();
});

function requireDrone(number?: number): DroneController {
  const target = number ? fleet.drones.get(`drone_${number}`) : drone;
  if (!target) throw new Error(number ? `Drone ${number} is not deployed.` : "Deploy or select a drone first.");
  return target;
}

function setCommandMessage(message: string): void {
  if (commandStatus.textContent !== message) commandStatus.textContent = message;
  commandInput.placeholder = message;
}

function localOffset(target: DroneController, east: number, north: number): { latitude: number; longitude: number; altitude: number } {
  return previewCoordinate({ x: east, y: north, z: 0 }, target.snapshot());
}

function runLocalMission(target: DroneController, mission: Parameters<DroneController["run"]>[0], label: string): void {
  if (controllingDrone) flyToFreeCameraOverview();
  fleet.checkpoint(label);
  target.run(mission);
  drone = target;
  refreshFleet();
}

function gestureOffset(action: string, meters = 65): { east: number; north: number } | undefined {
  if (action === "fly_north") return { east: 0, north: meters };
  if (action === "fly_south") return { east: 0, north: -meters };
  if (action === "fly_east") return { east: meters, north: 0 };
  if (action === "fly_west") return { east: -meters, north: 0 };
  if (action === "fly_forward") {
    const heading = drone?.horizontalFlightHeading ?? drone?.heading ?? 0;
    return { east: Math.sin(heading) * meters, north: Math.cos(heading) * meters };
  }
  if (action.startsWith("fly_bearing:")) {
    const bearing = Number(action.slice("fly_bearing:".length));
    if (Number.isFinite(bearing)) return { east: Math.sin(bearing) * meters, north: Math.cos(bearing) * meters };
  }
  return undefined;
}

function executeGestureAction(action: string): void {
  const target = drone ?? fleet.drones.values().next().value;
  if (!target) return;
  drone = target;
  if (action === "gesture_stop") {
    target.stopCommand();
    refreshFleet();
    gestureHud.action.textContent = "GESTURE RELEASED · HOLDING";
    setCommandMessage("Gesture released · drone holding position");
    return;
  }
  const offset = gestureOffset(action);
  if (offset) {
    target.startGestureMotion(offset.east, offset.north, 0, Math.max(38, target.speedMph * 0.44704));
    refreshFleet();
  } else if (action === "takeoff") {
    target.startGestureMotion(0, 0, 1, 18);
    refreshFleet();
  } else if (action === "descend") {
    target.startGestureMotion(0, 0, -1, 18);
    refreshFleet();
  } else if (action === "land" || action === "return_home") {
    runLocalMission(target, { drone_id: target.id, mission: [{ action: "return_home", speed_mps: 12 }] }, "gesture return");
  } else if (action === "orbit") {
    runLocalMission(target, { drone_id: target.id, mission: [{ action: "orbit", radius_m: 45, duration_s: 3600 }] }, "gesture orbit");
  } else if (action === "halt" || action === "estop") {
    if (controllingDrone) flyToFreeCameraOverview();
    target.stopCommand();
    refreshFleet();
  } else if (action === "rotate_heading") {
    if (controllingDrone) flyToFreeCameraOverview();
    fleet.checkpoint("gesture heading turn");
    target.rotateHeading(90);
    refreshFleet();
  } else if (action.startsWith("speed:")) {
    const speed = action.endsWith("slow") ? 25 : action.endsWith("sport") ? 90 : 60;
    target.speedMph = speed;
    refreshFleet();
  } else if (action === "spin360") {
    runLocalMission(target, { drone_id: target.id, mission: [{ action: "orbit", radius_m: 5, duration_s: 3 }] }, "gesture spin");
  }
  gestureHud.action.textContent = `COMMAND · ${action.replaceAll("_", " ").toUpperCase()}`;
  setCommandMessage(`Gesture received · ${action.replaceAll("_", " ")}`);
}

function updateGestureHud(state: BrowserGestureState): void {
  const active = state.status === "active";
  gestureHud.root.classList.toggle("connected", active);
  gestureHud.connection.textContent = active ? "CAMERA ACTIVE · ON-DEVICE" : state.status === "loading" ? "LOADING GESTURE MODEL" : "CAMERA OFFLINE";
  gestureHud.name.textContent = state.present
    ? state.gesture === "None" ? "HAND DETECTED" : state.gesture.replaceAll("_", " ").toUpperCase()
    : "NO HAND";
  gestureHud.confidence.textContent = state.message ?? (state.present && state.gesture !== "None"
    ? `${Math.round(state.score * 100)}% confidence · hold to command`
    : active ? "Show a gesture to control drone 1" : "Waiting for browser camera permission");
  gestureHud.progress.style.width = `${Math.round(state.holdProgress * 100)}%`;
}

let stopGestureCamera: (() => void) | undefined;
if (!runtimeMode && !phoneMode) {
  void startGestureCamera(updateGestureHud, executeGestureAction)
    .then(stop => { stopGestureCamera = stop; })
    .catch(error => updateGestureHud({
      status: "error",
      present: false,
      gesture: "None",
      score: 0,
      holdProgress: 0,
      message: error instanceof DOMException && error.name === "NotAllowedError"
        ? "Camera permission denied · allow it in browser settings, then reload"
        : `Gesture camera failed · ${error instanceof Error ? error.message : "reload to retry"}`,
    }));
}
window.addEventListener("beforeunload", () => stopGestureCamera?.());

type AiObjective = {
  type: string;
  desired_units?: number;
  target?: { point?: { x: number; y: number; z: number }; region_id?: string };
};

async function executeAiInstruction(instruction: string): Promise<void> {
  commandInput.disabled = true;
  setCommandMessage("AI is compiling and validating the mission…");
  try {
    const response = await fetch(`${runtimeApiBase}/api/mission-plans/compile`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ utterance: instruction, selected_drone_id: drone?.id ?? null, start: false }),
    });
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    const payload = await response.json() as { result?: { status?: string; plan?: { objectives?: AiObjective[] }; clarification_question?: string; rejection_reason?: string; interpretation_summary?: string } };
    const result = payload.result;
    if (result?.status !== "READY" || !result.plan?.objectives?.length) {
      throw new Error(result?.clarification_question ?? result?.rejection_reason ?? "The AI could not form a safe mission.");
    }
    let world: { regions?: { id: string; center: { x: number; y: number; z: number } }[] } | undefined;
    const objectives = result.plan.objectives;
    const members = [...fleet.drones.values()];
    if (!members.length) throw new Error("Deploy a drone before assigning an AI mission.");
    fleet.checkpoint("AI mission");
    if (controllingDrone) flyToFreeCameraOverview();
    let assigned = 0;
    for (const objective of objectives) {
      const count = Math.max(1, Math.min(members.length, objective.desired_units ?? 1));
      const targets = [drone, ...members].filter((item, index, all): item is DroneController => Boolean(item) && all.indexOf(item) === index).slice(0, count);
      let point = objective.target?.point;
      if (!point && objective.target?.region_id) {
        if (!world) {
          const worldResponse = await fetch(`${runtimeApiBase}/api/world`);
          if (!worldResponse.ok) throw new Error("Could not load the AI mission region.");
          world = await worldResponse.json() as { regions?: { id: string; center: { x: number; y: number; z: number } }[] };
        }
        point = world.regions?.find(region => region.id.toLowerCase() === objective.target!.region_id!.toLowerCase())?.center;
      }
      for (const target of targets) {
        const type = objective.type.toUpperCase();
        if (type === "RETURN") {
          target.run({ drone_id: target.id, mission: [{ action: "return_home", speed_mps: target.speedMph * 0.44704 }] });
        } else if (type === "HOLD") {
          target.stopCommand();
        } else if (point) {
          const destination = previewCoordinate(point, home);
          const mission: MissionStep[] = [{ action: "goto", ...destination, speed_mps: target.speedMph * 0.44704 }];
          if (["WATCH", "SEARCH"].includes(type)) mission.push({ action: "orbit", radius_m: 30, duration_s: 24 });
          target.run({ drone_id: target.id, mission });
        } else {
          continue;
        }
        assigned++;
      }
    }
    if (!assigned) throw new Error("The AI plan did not contain a location this map can fly to.");
    refreshFleet();
    setCommandMessage(`AI mission accepted · ${result.interpretation_summary || `${assigned} drone${assigned === 1 ? "" : "s"} assigned`}`);
  } catch (error) {
    const detail = error instanceof Error ? error.message : "AI command failed.";
    setCommandMessage(`${detail} · Common commands still work; type / to see them.`);
  } finally {
    commandInput.disabled = false;
    commandInput.focus();
  }
}

async function executeCommand(intent: CommandIntent): Promise<void> {
  if (intent.type === "help") {
    commandInput.value = "/";
    renderCommandSuggestions();
    setCommandMessage("Try “go to the Washington Monument, wait 5 seconds, then go back 50 meters”.");
    return;
  }
  if (intent.type === "deploy") {
    if (intent.count === 1) {
      document.querySelector<HTMLButtonElement>("#deploy-drone")!.click();
    } else {
      document.querySelector<HTMLButtonElement>("#bulk-deploy")!.click();
      countInput.value = String(intent.count);
      quickPlacement = true;
    }
    document.querySelector<HTMLSelectElement>("#drone-type")!.value = intent.survey ? "survey" : "normal";
    setCommandMessage(`Click the map to deploy ${intent.count === 1 ? "the drone" : `${intent.count} drones`} immediately.`);
    return;
  }
  if (intent.type === "fly") {
    const target = requireDrone(intent.droneNumber);
    if (target !== drone) selectDrone(target.id);
    if (!controllingDrone) controlDroneButton.click();
    return;
  }
  if (intent.type === "release") {
    if (controllingDrone) flyToFreeCameraOverview();
    setCommandMessage("Manual control released · drone hovering.");
    return;
  }
  if (intent.type === "select") {
    const target = requireDrone(intent.droneNumber);
    selectDrone(target.id);
    setCommandMessage(`${target.id.replace("_", " ")} selected.`);
    return;
  }
  if (intent.type === "speed") {
    const target = requireDrone();
    fleet.checkpoint("speed change");
    target.speedMph = intent.mph;
    refreshFleet();
    setCommandMessage(`${target.id.replace("_", " ")} speed set to ${intent.mph} mph.`);
    return;
  }
  if (intent.type === "return") {
    const targets = intent.all ? [...fleet.drones.values()] : [requireDrone()];
    if (!targets.length) throw new Error("Deploy a drone first.");
    fleet.checkpoint("return command");
    if (controllingDrone) flyToFreeCameraOverview();
    for (const target of targets) target.run({ drone_id: target.id, mission: [{ action: "return_home", speed_mps: target.speedMph * 0.44704 }] });
    setCommandMessage(`${targets.length === 1 ? targets[0].id.replace("_", " ") : "Fleet"} returning home.`);
    return;
  }
  if (intent.type === "reset") {
    if (intent.all) document.querySelector<HTMLButtonElement>("#reset-all")!.click();
    else {
      const target = requireDrone(); fleet.checkpoint("reset drone"); target.reset(); refreshFleet();
      setCommandMessage(`${target.id.replace("_", " ")} reset to its home coordinate.`);
    }
    return;
  }
  if (intent.type === "goto") {
    if (Math.abs(intent.latitude) > 90 || Math.abs(intent.longitude) > 180) throw new Error("Latitude or longitude is outside the valid range.");
    const targets = intent.all ? [...fleet.drones.values()] : [requireDrone()];
    if (!targets.length) throw new Error("Deploy a drone first.");
    fleet.checkpoint("coordinate command");
    if (controllingDrone) flyToFreeCameraOverview();
    for (const target of targets) target.run({ drone_id: target.id, mission: [{ action: "goto", latitude: intent.latitude, longitude: intent.longitude, altitude: intent.altitude ?? target.snapshot().altitude, speed_mps: target.speedMph * 0.44704 }] });
    setCommandMessage(`${targets.length === 1 ? targets[0].id.replace("_", " ") : "Fleet"} flying to ${intent.latitude.toFixed(5)}, ${intent.longitude.toFixed(5)}.`);
    return;
  }
  if (intent.type === "move") {
    const target = requireDrone();
    const heading = target.horizontalFlightHeading ?? target.heading;
    const forward = intent.direction === "forward" ? intent.meters : intent.direction === "back" ? -intent.meters : 0;
    const right = intent.direction === "right" ? intent.meters : intent.direction === "left" ? -intent.meters : 0;
    const destination = localOffset(target, Math.sin(heading) * forward + Math.cos(heading) * right, Math.cos(heading) * forward - Math.sin(heading) * right);
    runLocalMission(target, { drone_id: target.id, mission: [{ action: "goto", ...destination, speed_mps: target.speedMph * 0.44704 }] }, "text movement");
    setCommandMessage(`${target.id.replace("_", " ")} moving ${intent.direction} ${intent.meters} m.`);
    return;
  }
  if (intent.type === "turn") {
    const target = requireDrone();
    if (controllingDrone) flyToFreeCameraOverview();
    fleet.checkpoint("heading turn");
    target.rotateHeading(intent.direction === "right" ? intent.degrees : -intent.degrees);
    refreshFleet();
    setCommandMessage(`${target.id.replace("_", " ")} rotating ${intent.direction} ${intent.degrees}°.`);
    return;
  }
  if (intent.type === "hover") {
    const target = requireDrone();
    runLocalMission(target, { drone_id: target.id, mission: [{ action: "hover", duration_s: intent.seconds }] }, "hover command");
    setCommandMessage(`${target.id.replace("_", " ")} hovering for ${intent.seconds} seconds.`);
    return;
  }
  if (intent.type === "orbit") {
    const target = requireDrone();
    runLocalMission(target, { drone_id: target.id, mission: [{ action: "orbit", radius_m: intent.radius, duration_s: intent.seconds }] }, "orbit command");
    setCommandMessage(`${target.id.replace("_", " ")} orbiting at ${intent.radius} m for ${intent.seconds} seconds.`);
    return;
  }
  if (intent.type === "landmark" || intent.type === "place") {
    await executeCommandSequence([intent], `go to ${intent.type === "landmark" ? intent.name : intent.query}`);
    return;
  }
  await executeAiInstruction(intent.instruction);
}

async function executeCommandSequence(intents: CommandIntent[], original: string): Promise<void> {
  const resolvedIntents = await Promise.all(intents.map(async intent => {
    if (intent.type !== "place") return intent;
    if (!placeGeocoder) throw new Error(`“${intent.query}” is not in the built-in DC landmarks; add a Cesium ion token to enable place search.`);
    setCommandMessage(`Finding ${intent.query}…`);
    const results = await placeGeocoder.geocode(`${intent.query}, Washington, DC`, Cesium.GeocodeType.SEARCH);
    const result = results[0];
    if (!result) throw new Error(`Could not find “${intent.query}”.`);
    const center = result.destination instanceof Cesium.Rectangle
      ? Cesium.Rectangle.center(result.destination)
      : Cesium.Cartographic.fromCartesian(result.destination);
    return {
      type: "landmark" as const,
      name: result.displayName,
      latitude: Cesium.Math.toDegrees(center.latitude),
      // Aim beside the returned feature center so collision avoidance does not
      // try to enter the landmark's building geometry.
      longitude: Cesium.Math.toDegrees(center.longitude) + 0.00065,
    };
  }));
  if (resolvedIntents.length === 1 && !isFlightSequenceIntent(resolvedIntents[0])) {
    await executeCommand(resolvedIntents[0]);
    return;
  }
  if (resolvedIntents.some(intent => intent.type === "ai")) {
    await executeAiInstruction(original);
    return;
  }
  if (!resolvedIntents.every(isFlightSequenceIntent)) throw new Error("Deployment and pilot-mode commands cannot be mixed into an automatic flight sequence.");
  const target = requireDrone();
  const steps = compileMissionSequence(
    resolvedIntents,
    target.snapshot(),
    target.homeCoordinates,
    target.horizontalFlightHeading ?? target.heading,
    target.speedMph * 0.44704,
  );
  runLocalMission(target, { drone_id: target.id, mission: steps }, "text flight sequence");
  const landmark = resolvedIntents.find((intent): intent is Extract<CommandIntent, { type: "landmark" }> => intent.type === "landmark");
  setCommandMessage(`${target.id.replace("_", " ")} running ${steps.length}-step mission${landmark ? ` via ${landmark.name}` : ""}.`);
}

function renderCommandSuggestions(): void {
  const query = commandInput.value.trim().toLowerCase();
  if (!query.startsWith("/")) { commandSuggestions.hidden = true; return; }
  const matches = COMMANDS.filter(item => item.command.startsWith(query.split(" ")[0]));
  commandSuggestions.replaceChildren(...matches.map(item => {
    const button = document.createElement("button");
    button.type = "button"; button.className = "command-suggestion"; button.setAttribute("role", "option");
    const command = document.createElement("code"); command.textContent = item.command;
    const hint = document.createElement("small"); hint.textContent = item.hint;
    button.append(command, hint);
    button.addEventListener("click", () => { commandInput.value = `${item.command} `; commandSuggestions.hidden = true; commandInput.focus(); });
    return button;
  }));
  commandSuggestions.hidden = matches.length === 0;
}

commandInput.addEventListener("input", renderCommandSuggestions);
commandInput.addEventListener("keydown", event => {
  if (event.key === "Escape") { commandSuggestions.hidden = true; commandInput.blur(); }
});
commandForm.addEventListener("submit", event => {
  event.preventDefault();
  if (phoneMode) return;
  const value = commandInput.value;
  commandInput.value = "";
  commandSuggestions.hidden = true;
  try { void executeCommandSequence(parseCommandSequence(value), value).catch(error => setCommandMessage(error instanceof Error ? error.message : "Command failed.")); }
  catch (error) { setCommandMessage(error instanceof Error ? error.message : "Command failed."); }
});

window.addEventListener("keydown", (event) => {
  if (event.key === "/" && !(event.target instanceof HTMLElement && event.target.closest('textarea, input, select, [contenteditable="true"]'))) {
    event.preventDefault(); commandInput.value = "/"; commandInput.focus(); renderCommandSuggestions(); return;
  }
  if (event.target instanceof HTMLElement && event.target.closest('textarea, input, select, [contenteditable="true"]')) return;
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  if (deploying && event.code === "Escape") { cancelDeployment(); return; }
  if (controllingDrone && event.code === "Escape") { flyToFreeCameraOverview(); return; }
  if (controllingDrone && ["KeyW", "KeyA", "KeyS", "KeyD", "KeyR", "KeyF", "Space", "ShiftLeft", "ShiftRight", "KeyQ", "KeyE"].includes(event.code)) {
    viewer.camera.cancelFlight();
    activeCameraKeys.add(event.code);
    event.preventDefault();
  }
});
window.addEventListener("keyup", (event) => activeCameraKeys.delete(event.code));
window.addEventListener("blur", clearInput);
document.addEventListener("visibilitychange", clearInput);
document.addEventListener("focusin", clearInput);
viewer.canvas.tabIndex = 0;
viewer.canvas.addEventListener("pointerdown", () => viewer.canvas.focus());

if (!runtimeMode) void worldReady.then(async () => {
  await groundStartingHome();
  drone = fleet.deploy(phoneMode ? { ...home, altitude: home.altitude + 3.5 } : home);
  selectedIds.add(drone.id);
  refreshFleet();
  if (phoneMode) {
    stopGestureCamera = startPhoneDemo(viewer, drone, home, runtimeApiBase);
    commandStatus.textContent = "One drone · waiting for phone positions and gestures";
  } else commandStatus.textContent = "Drone 1 grounded beside the Washington Monument · show a gesture to fly.";
});

function updateFreeCamera(deltaSeconds: number): void {
  if (controllingDrone && drone) {
    const axis = (positive: string, negative: string) => Number(activeCameraKeys.has(positive)) - Number(activeCameraKeys.has(negative));
    pilotView.heading += axis("KeyE", "KeyQ") * 1.8 * deltaSeconds;
    const forward = axis("KeyW", "KeyS");
    const right = axis("KeyD", "KeyA");
    const up = Number(activeCameraKeys.has("Space") || activeCameraKeys.has("KeyR")) - Number(activeCameraKeys.has("ShiftLeft") || activeCameraKeys.has("ShiftRight") || activeCameraKeys.has("KeyF"));
    const movement = screenRelativeMovement(drone.cameraPosition, viewer.camera.directionWC, viewer.camera.rightWC, forward, right);
    const { east, north } = movement;
    if (fleet.manualBatch.length) fleet.moveBatch(east, north, up, deltaSeconds, pilotView.heading);
    else drone.moveManually(east, north, up, deltaSeconds, pilotView.heading);
    const position = drone.snapshot();
    const center = Cesium.Cartesian3.fromDegrees(position.longitude, position.latitude, position.altitude);
    if (pilotCameraMode === "first") {
      // Fixed mount just above the prism, looking forward along its heading.
      const mount = Cesium.Matrix4.multiplyByPoint(Cesium.Transforms.eastNorthUpToFixedFrame(center), new Cesium.Cartesian3(0, 0, 3), new Cesium.Cartesian3());
      viewer.camera.lookAtTransform(Cesium.Matrix4.IDENTITY);
      viewer.camera.setView({ destination: mount, orientation: { heading: pilotView.heading, pitch: 0, roll: 0 } });
    }
    return;
  }
}

let automaticCenter: Cesium.Cartesian3 | undefined;
let automaticRange = orbitCamera.range;
let stillSeconds = 0;
const previousSubjects = new Map<string, Cesium.Cartesian3>();

function updateAutomaticCamera(deltaSeconds: number): void {
  if (runtimeMode || phoneMode || cameraMode !== "auto" || deploying || (controllingDrone && pilotCameraMode === "first")) return;
  const subjects = [...fleet.drones.values()].map(member => ({ id: member.id, position: member.cameraPosition }));
  if (!subjects.length) {
    orbitCamera.heading += deltaSeconds * 0.08;
    applyOrbitCamera();
    automaticCenter = undefined;
    previousSubjects.clear();
    return;
  }

  let movement = 0;
  let movingId: string | undefined;
  for (const subject of subjects) {
    const previous = previousSubjects.get(subject.id);
    if (previous) {
      const distance = Cesium.Cartesian3.distance(previous, subject.position);
      if (distance > movement) { movement = distance; movingId = subject.id; }
    }
    previousSubjects.set(subject.id, Cesium.Cartesian3.clone(subject.position));
  }
  for (const id of previousSubjects.keys()) {
    if (!subjects.some(subject => subject.id === id)) previousSubjects.delete(id);
  }
  stillSeconds = movement < 0.04 ? stillSeconds + deltaSeconds : 0;
  const headingDrone = controllingDrone ? drone : movingId ? fleet.drones.get(movingId) : undefined;
  const flightHeading = controllingDrone ? headingDrone?.heading : headingDrone?.horizontalFlightHeading;
  if (flightHeading !== undefined && (controllingDrone || movement >= 0.04)) {
    orbitCamera.heading = blendHeading(orbitCamera.heading, flightHeading, 1 - Math.exp(-4.5 * deltaSeconds));
  } else {
    orbitCamera.heading += deltaSeconds * idleCameraDriftRate(stillSeconds);
  }

  const frame = fleetCameraFrame(subjects.map(subject => subject.position))!;
  const blend = 1 - Math.exp(-2.8 * deltaSeconds);
  automaticCenter = automaticCenter
    ? Cesium.Cartesian3.lerp(automaticCenter, frame.center, blend, automaticCenter)
    : Cesium.Cartesian3.clone(frame.center);
  automaticRange = Cesium.Math.lerp(automaticRange, frame.range, blend);
  viewer.camera.lookAt(automaticCenter, new Cesium.HeadingPitchRange(orbitCamera.heading, Cesium.Math.toRadians(-24), automaticRange));
}

const updateBattery = !runtimeMode && !phoneMode ? startBatteryPanel(fleet, () => drone) : undefined;
let previousTime = Cesium.JulianDate.clone(viewer.clock.currentTime);
const undoButton = document.querySelector<HTMLButtonElement>("#undo-action")!;
const pauseButton = document.querySelector<HTMLButtonElement>("#pause-paths")!;
undoButton.addEventListener("click", () => {
  const id = drone?.id;
  flyToFreeCameraOverview(); cancelDeployment();
  const label = fleet.undo();
  drone = fleet.drones.get(id ?? "") ?? fleet.drones.values().next().value;
  for (const selected of selectedIds) if (!fleet.drones.has(selected)) selectedIds.delete(selected);
  refreshFleet(); commandStatus.textContent = label ? `Undid ${label}. Drones restored at rest.` : "Nothing to undo.";
});
pauseButton.addEventListener("click", () => { fleet.togglePause(performance.now() / 1000); refreshFleet(); });
let surveyUpdateIndex = 0;
let previousCameraTime = performance.now();
let pilotStatusTime = 0;
let telemetryTime = 0;
viewer.clock.onTick.addEventListener((clock) => {
  const deltaSeconds = Math.max(0, Math.min(0.1, Cesium.JulianDate.secondsDifference(clock.currentTime, previousTime)));
  previousTime = Cesium.JulianDate.clone(clock.currentTime, previousTime);
  if (!runtimeMode) for (const item of fleet.drones.values()) item.update(deltaSeconds);
  const cameraTime = performance.now();
  const wasPlaying = fleet.replay.running;
  fleet.updateReplay(cameraTime / 1000);
  if (wasPlaying && !fleet.replay.running) refreshFleet();
  const cameraDelta = Math.min(0.1, Math.max(0, (cameraTime - previousCameraTime) / 1000));
  updateFreeCamera(cameraDelta);
  updateAutomaticCamera(cameraDelta);
  updateBattery?.(cameraDelta, fleet.paused || !clock.shouldAnimate);
  previousCameraTime = cameraTime;
  if (!runtimeMode && cameraTime - telemetryTime >= 100) {
    telemetryTime = cameraTime;
    updateTelemetry();
  }
  if (!runtimeMode && controllingDrone && cameraTime - pilotStatusTime >= 250) {
    pilotStatusTime = cameraTime;
    setCommandMessage(drone?.collisionBlocked
      ? "No safe path found. Steer away or climb to continue."
      : drone?.avoidanceActive
        ? "Obstacle detected: autopilot is routing around it."
        : collisionWarning(viewer) ?? "Pilot active · WASD move · Q/E turn · R/F altitude · Esc release");
  }
  const surveys = [...fleet.drones.values()].filter(member => member.droneType === "survey");
  if (surveys.length) surveys[surveyUpdateIndex++ % surveys.length].updateSurvey(cameraTime);
});
