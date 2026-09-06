import "cesium/Build/Cesium/Widgets/widgets.css";
import "./style.css";
import * as Cesium from "cesium";
import { DroneController } from "./drone-controller";
import { Fleet, DRONE_COLORS } from "./fleet";
import { formationSlots } from "./formation";
import { collisionWarning } from "./collision";
import { fleetCameraFrame, screenRelativeMovement } from "./cinematic-camera";
import { parseMission, sampleMission } from "./mission";
import { startRuntimeMode } from "./runtime/index";
import { previewCoordinate, previewMission, type FlightPreview } from "./flight-preview";

const home = { latitude: 38.8895, longitude: -77.0353, altitude: 80 };
const token = import.meta.env.VITE_CESIUM_ION_ACCESS_TOKEN as string | undefined;
const status = document.querySelector<HTMLParagraphElement>("#world-status")!;
const commandStatus = document.querySelector<HTMLParagraphElement>("#command-status")!;
const missionInput = document.querySelector<HTMLTextAreaElement>("#mission-json")!;
const stateElement = document.querySelector<HTMLDListElement>("#drone-state")!;
const commandDrawer = document.querySelector<HTMLDetailsElement>("#command-drawer")!;
const runtimeMode = new URLSearchParams(window.location.search).get("mode") === "runtime";
document.body.classList.toggle("runtime-mode", runtimeMode);

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
if (runtimeMode) startRuntimeMode(viewer);

viewer.scene.globe.enableLighting = false;
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
const monumentTarget = Cesium.Cartesian3.fromDegrees(home.longitude, home.latitude, 20);
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

cameraHandler.setInputAction(() => { isOrbitDragging = cameraMode === "auto"; steering = controllingDrone; }, Cesium.ScreenSpaceEventType.LEFT_DOWN);
cameraHandler.setInputAction(() => { isOrbitDragging = false; steering = false; }, Cesium.ScreenSpaceEventType.LEFT_UP);
window.addEventListener("pointerup", () => { steering = false; isOrbitDragging = false; });
cameraHandler.setInputAction((movement: { startPosition: Cesium.Cartesian2; endPosition: Cesium.Cartesian2 }) => {
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
  document.querySelector<HTMLButtonElement>("#bulk-deploy")!.disabled = fleet.replay.running;
  refreshGroups();
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
void loadWorld();

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

window.addEventListener("keydown", (event) => {
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
  if (runtimeMode || cameraMode !== "auto" || deploying || (controllingDrone && pilotCameraMode === "first")) return;
  const subjects = [...fleet.drones.values()].map(member => ({ id: member.id, position: member.cameraPosition }));
  if (!subjects.length) {
    orbitCamera.heading += deltaSeconds * 0.08;
    applyOrbitCamera();
    automaticCenter = undefined;
    previousSubjects.clear();
    return;
  }

  let movement = 0;
  for (const subject of subjects) {
    const previous = previousSubjects.get(subject.id);
    if (previous) movement = Math.max(movement, Cesium.Cartesian3.distance(previous, subject.position));
    previousSubjects.set(subject.id, Cesium.Cartesian3.clone(subject.position));
  }
  for (const id of previousSubjects.keys()) {
    if (!subjects.some(subject => subject.id === id)) previousSubjects.delete(id);
  }
  stillSeconds = movement < 0.04 ? stillSeconds + deltaSeconds : 0;
  if (stillSeconds > 0.65) orbitCamera.heading += deltaSeconds * 0.065;

  const frame = fleetCameraFrame(subjects.map(subject => subject.position))!;
  const blend = 1 - Math.exp(-2.8 * deltaSeconds);
  automaticCenter = automaticCenter
    ? Cesium.Cartesian3.lerp(automaticCenter, frame.center, blend, automaticCenter)
    : Cesium.Cartesian3.clone(frame.center);
  automaticRange = Cesium.Math.lerp(automaticRange, frame.range, blend);
  viewer.camera.lookAt(automaticCenter, new Cesium.HeadingPitchRange(orbitCamera.heading, Cesium.Math.toRadians(-24), automaticRange));
}

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
let overviewTime = 0;
let surveyUpdateIndex = 0;
function updateOverview(now: number): void {
  undoButton.disabled = !fleet.undoLabel;
  undoButton.textContent = fleet.undoLabel ? `Undo ${fleet.undoLabel}` : "Undo last action";
  pauseButton.disabled = !fleet.replay.running;
  pauseButton.textContent = fleet.paused ? "Resume playback" : "Pause playback";
  if (now - overviewTime < 250) return;
  overviewTime = now;
  const container = document.querySelector<HTMLDivElement>("#fleet-overview")!;
  container.replaceChildren();
  for (const member of fleet.drones.values()) {
    const card = document.createElement("div"); card.className = "fleet-card"; card.style.borderLeftColor = member.colorHex;
    const title = document.createElement("strong"); title.textContent = `${member.id.replace("_", " ")} · ${member.droneType}`;
    const detail = document.createElement("p");
    const route = member.routeLength;
    detail.textContent = `${member.collisionBlocked ? "No safe route" : member.avoidanceActive ? "Auto-avoiding obstacle" : member.snapshot().state} · ${member.speedMph} mph · ${route.toFixed(0)} m route · ${(route / (member.speedMph * 0.44704)).toFixed(1)} s estimated${member.droneType === "survey" ? ` · ${member.coverageCount} coverage patches` : ""}`;
    card.append(title, detail); container.append(card);
  }
}
let previousCameraTime = performance.now();
viewer.clock.onTick.addEventListener((clock) => {
  const deltaSeconds = Math.max(0, Math.min(0.1, Cesium.JulianDate.secondsDifference(clock.currentTime, previousTime)));
  previousTime = Cesium.JulianDate.clone(clock.currentTime, previousTime);
  if (!runtimeMode) for (const item of fleet.drones.values()) item.update(deltaSeconds);
  const cameraTime = performance.now();
  const wasPlaying = fleet.replay.running;
  fleet.updateReplay(cameraTime / 1000);
  replayTime.textContent = `Elapsed: ${fleet.replay.elapsed.toFixed(2)} s`;
  if (fleet.replay.total) {
    replayStatus.textContent = `${fleet.replay.arrived} / ${fleet.replay.total} arrived${fleet.blockedCount ? ` · ${fleet.blockedCount} blocked by obstacles` : ""}${fleet.paused ? " · Paused" : fleet.replay.running ? " · Playing at assigned speeds" : fleet.blockedCount ? " · Playback stopped" : " · All paths complete"}`;
  }
  if (wasPlaying && !fleet.replay.running) refreshFleet();
  const cameraDelta = Math.min(0.1, Math.max(0, (cameraTime - previousCameraTime) / 1000));
  updateFreeCamera(cameraDelta);
  updateAutomaticCamera(cameraDelta);
  previousCameraTime = cameraTime;
  updateOverview(cameraTime);
  const surveys = [...fleet.drones.values()].filter(member => member.droneType === "survey");
  if (surveys.length) surveys[surveyUpdateIndex++ % surveys.length].updateSurvey(cameraTime);
  if (runtimeMode) return;
  const snapshot = drone?.snapshot();
  if (controllingDrone) {
    commandStatus.textContent = drone?.collisionBlocked
      ? "No safe path found. Steer away or climb to continue."
      : drone?.avoidanceActive
        ? "Obstacle detected: autopilot is routing around it."
      : collisionWarning(viewer) ?? "Pilot control active. Esc releases; WASD moves; R/F changes height.";
  }
  if (!snapshot) { stateElement.innerHTML = "<dt>Fleet</dt><dd>No drones deployed</dd>"; return; }
  stateElement.innerHTML = [
    ["State", snapshot.state], ["Position", `${snapshot.latitude.toFixed(5)}, ${snapshot.longitude.toFixed(5)}`], ["Altitude", `${snapshot.altitude.toFixed(0)} m`], ["Step", snapshot.totalSteps ? `${snapshot.currentStep} / ${snapshot.totalSteps}` : "—"],
  ].map(([label, value]) => `<dt>${label}</dt><dd>${value}</dd>`).join("");
});
