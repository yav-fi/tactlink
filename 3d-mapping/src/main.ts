import "cesium/Build/Cesium/Widgets/widgets.css";
import "./style.css";
import * as Cesium from "cesium";
import { DroneController } from "./drone-controller";
import { Fleet, DRONE_COLORS } from "./fleet";
import { formationSlots } from "./formation";
import { parseMission, sampleMission } from "./mission";

const home = { latitude: 38.8895, longitude: -77.0353, altitude: 80 };
const token = import.meta.env.VITE_CESIUM_ION_ACCESS_TOKEN as string | undefined;
const status = document.querySelector<HTMLParagraphElement>("#world-status")!;
const commandStatus = document.querySelector<HTMLParagraphElement>("#command-status")!;
const missionInput = document.querySelector<HTMLTextAreaElement>("#mission-json")!;
const stateElement = document.querySelector<HTMLDListElement>("#drone-state")!;

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
  baseLayer: false,
});

viewer.scene.globe.enableLighting = true;
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
const pilotView = { heading: 0, pitch: Cesium.Math.toRadians(-18), range: 65 };
const pilotCameraSelect = document.querySelector<HTMLSelectElement>("#pilot-camera")!;
let pilotCameraMode = "third";
let freeOrbitHeading = 0;
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
}

function releaseDrone(): void {
  clearInput();
  if (controllingDrone) drone?.setManualControl(false);
  controllingDrone = false;
  activeCameraKeys.clear();
  controlDroneButton.textContent = "Control drone";
  controlDroneButton.setAttribute("aria-pressed", "false");
}
const monumentTarget = Cesium.Cartesian3.fromDegrees(home.longitude, home.latitude, 20);
const orbitCamera = { heading: Cesium.Math.toRadians(30), pitch: Cesium.Math.toRadians(-22), range: 450 };
let cameraMode: "orbit" | "free" = "orbit";
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
  cameraMode = "orbit";
  viewer.trackedEntity = undefined;
  viewer.scene.screenSpaceCameraController.enableInputs = false;
  applyOrbitCamera();
  commandStatus.textContent = "Orbit camera active. Drag the map to see every side of the monument; scroll to zoom.";
}

function flyToFreeCameraOverview(): void {
  releaseDrone();
  cameraMode = "free";
  isOrbitDragging = false;
  viewer.camera.cancelFlight();
  viewer.trackedEntity = undefined;
  viewer.camera.lookAtTransform(Cesium.Matrix4.IDENTITY);
  viewer.scene.screenSpaceCameraController.enableInputs = true;
  commandStatus.textContent = "Free camera active. W/A/S/D moves; R/F moves up/down. Ctrl + drag rotates the view.";
}

cameraHandler.setInputAction(() => { isOrbitDragging = cameraMode === "orbit"; steering = controllingDrone; }, Cesium.ScreenSpaceEventType.LEFT_DOWN);
cameraHandler.setInputAction(() => { isOrbitDragging = false; steering = false; }, Cesium.ScreenSpaceEventType.LEFT_UP);
window.addEventListener("pointerup", () => { steering = false; isOrbitDragging = false; });
cameraHandler.setInputAction((movement: { startPosition: Cesium.Cartesian2; endPosition: Cesium.Cartesian2 }) => {
  if (controllingDrone && steering) {
    if (pilotCameraMode === "free") {
      freeOrbitHeading += (movement.endPosition.x - movement.startPosition.x) * 0.005;
      pilotView.pitch = Cesium.Math.clamp(pilotView.pitch + (movement.endPosition.y - movement.startPosition.y) * 0.004, -1.2, -0.05);
    } else {
      pilotView.heading += (movement.endPosition.x - movement.startPosition.x) * 0.005;
    }
    return;
  }
  if (!isOrbitDragging || cameraMode !== "orbit") return;
  orbitCamera.heading -= (movement.endPosition.x - movement.startPosition.x) * 0.008;
  orbitCamera.pitch = Cesium.Math.clamp(orbitCamera.pitch + (movement.endPosition.y - movement.startPosition.y) * 0.006, Cesium.Math.toRadians(-85), Cesium.Math.toRadians(-5));
  applyOrbitCamera();
}, Cesium.ScreenSpaceEventType.MOUSE_MOVE);
cameraHandler.setInputAction((delta: number) => {
  if (controllingDrone) {
    if (pilotCameraMode === "free") pilotView.range = Cesium.Math.clamp(pilotView.range - delta * 0.05, 30, 180);
    return;
  }
  if (cameraMode !== "orbit") return;
  orbitCamera.range = Cesium.Math.clamp(orbitCamera.range + delta * 0.22, 80, 4_000);
  applyOrbitCamera();
}, Cesium.ScreenSpaceEventType.WHEEL);
viewer.camera.setView({ destination: Cesium.Cartesian3.fromDegrees(home.longitude, home.latitude - 0.008, 900), orientation: { heading: 0, pitch: Cesium.Math.toRadians(-35), roll: 0 } });
flyToFreeCameraOverview();
commandStatus.textContent = "No drones deployed. Choose Deploy a new drone to begin.";

function refreshFleet(): void {
  droneSelect.replaceChildren();
  if (!fleet.drones.size) droneSelect.add(new Option("No drones deployed", ""));
  let index = 0;
  for (const item of fleet.drones.values()) {
    index++;
    const color = DRONE_COLORS.find(color => color.hex === item.colorHex);
    droneSelect.add(new Option(`${item.id.replace("_", " ")} · ${color?.name ?? item.colorHex}`, item.id));
  }
  droneSelect.value = drone?.id ?? "";
  droneSelect.disabled = deploying || !drone;
  speedInput.disabled = deploying || !drone || fleet.replay.running;
  speedInput.value = String(drone?.speedMph ?? 60);
  speedInput.removeAttribute("aria-invalid");
  speedStatus.textContent = `${drone?.speedMph ?? 60} mph · ${((drone?.speedMph ?? 60) * 0.44704).toFixed(2)} m/s`;
  for (const id of ["run-mission", "reset-mission", "control-drone"]) {
    document.querySelector<HTMLButtonElement>(`#${id}`)!.disabled = deploying || !drone || fleet.replay.running;
  }
  document.querySelector<HTMLButtonElement>("#deploy-drone")!.disabled = fleet.replay.running;
  replayButton.disabled = deploying || fleet.replay.running || !fleet.drones.size;
  stopReplayButton.disabled = !fleet.replay.running;
  document.querySelector<HTMLButtonElement>("#bulk-deploy")!.disabled = fleet.replay.running;
  refreshGroups();
}

function refreshGroups(): void {
  const locked = deploying || fleet.replay.running;
  memberList.replaceChildren();
  for (const item of fleet.drones.values()) {
    const row = document.createElement("label"); row.className = "member-row";
    const checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.checked = selectedIds.has(item.id); checkbox.disabled = locked;
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) selectedIds.add(item.id); else selectedIds.delete(item.id);
      savedGroups.value = ""; refreshGroups();
    });
    const swatch = document.createElement("span"); swatch.className = "member-swatch"; swatch.style.background = item.colorHex;
    row.append(checkbox, swatch, document.createTextNode(item.id.replace("_", " "))); memberList.append(row);
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
}
document.querySelector<HTMLButtonElement>("#select-all")!.addEventListener("click", () => { for (const id of fleet.drones.keys()) selectedIds.add(id); savedGroups.value = ""; refreshGroups(); });
document.querySelector<HTMLButtonElement>("#select-none")!.addEventListener("click", () => { selectedIds.clear(); savedGroups.value = ""; refreshGroups(); });
savedGroups.addEventListener("change", () => { selectedIds.clear(); for (const id of fleet.groups.get(savedGroups.value)?.ids ?? []) selectedIds.add(id); refreshGroups(); });
document.querySelector<HTMLButtonElement>("#save-group")!.addEventListener("click", () => {
  try {
    const name = document.querySelector<HTMLInputElement>("#group-name")!.value.trim();
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
  deploying = false;
  pickedSurface = undefined;
  for (const preview of previews) viewer.entities.remove(preview);
  previews = [];
  deploymentPanel.hidden = true;
  confirmDeployment.disabled = true;
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
  placementMode = "single";
  countInput.disabled = true;
  countInput.value = "1";
  confirmDeployment.textContent = "Deploy here";
  deploymentPanel.hidden = false;
  deploymentStatus.textContent = `Next drone: ${fleet.nextColor.name}. Fly the camera, then click a starting point on the map. No route is recorded during placement.`;
  commandStatus.textContent = "Choose a starting point, then confirm deployment.";
  refreshFleet();
});
document.querySelector<HTMLButtonElement>("#bulk-deploy")!.addEventListener("click", () => {
  cancelDeployment(); flyToFreeCameraOverview(); deploying = true; placementMode = "bulk";
  countInput.disabled = false; countInput.value = "10"; deploymentPanel.hidden = false; confirmDeployment.textContent = "Deploy batch here";
  deploymentStatus.textContent = "Choose a map location for the batch, then adjust number, height and spacing.";
  refreshFleet();
});
document.querySelector<HTMLButtonElement>("#choose-destination")!.addEventListener("click", () => {
  if (!selectedIds.size) return;
  pendingIds = [...selectedIds]; pendingGroup = savedGroups.value;
  cancelDeployment(); flyToFreeCameraOverview(); deploying = true; placementMode = "command";
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
    return;
  }
  pickedSurface = Cesium.Cartographic.fromCartesian(picked);
  updatePreview();
}, Cesium.ScreenSpaceEventType.LEFT_CLICK);
confirmDeployment.addEventListener("click", () => {
  if (!deploying || !pickedSurface || confirmDeployment.disabled) return;
  const center = { latitude: Cesium.Math.toDegrees(pickedSurface.latitude), longitude: Cesium.Math.toDegrees(pickedSurface.longitude), altitude: pickedSurface.height + heightInput.valueAsNumber };
  if (placementMode === "command") {
    fleet.commandGroup(pendingIds, center, spacingInput.valueAsNumber, performance.now() / 1000, pendingGroup);
    cancelDeployment(); refreshFleet(); commandStatus.textContent = `${pendingIds.length} drones launched together; routes replaced. Shared color assigned.`;
    return;
  }
  const added = placementMode === "bulk" ? fleet.deployBulk(center, countInput.valueAsNumber, spacingInput.valueAsNumber) : [fleet.deploy(center)];
  drone = added[0]; selectedIds.clear(); for (const item of added) selectedIds.add(item.id);
  cancelDeployment(); selectDrone(drone.id);
  commandStatus.textContent = `${added.length} drone(s) deployed. Select Control drone for individual flight or Choose destination for the selection.`;
});

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
    status.textContent = "Google tiles could not load; showing the fallback globe.";
  }
} else {
  status.textContent = "Fallback globe active — add a Cesium ion token for the 3D city.";
}
}
void loadWorld();

document.querySelector<HTMLButtonElement>("#run-mission")!.addEventListener("click", () => {
  try {
    const mission = parseMission(missionInput.value);
    const target = fleet.drones.get(mission.drone_id);
    if (!target) throw new Error(`Deploy ${mission.drone_id} before sending it a mission.`);
    if (controllingDrone) flyToFreeCameraOverview();
    target.run(mission);
    commandStatus.textContent = "Mission accepted.";
  } catch (error) {
    commandStatus.textContent = error instanceof Error ? error.message : "Mission could not be parsed.";
  }
});
document.querySelector<HTMLButtonElement>("#reset-mission")!.addEventListener("click", () => {
  if (controllingDrone) flyToFreeCameraOverview();
  drone?.reset();
  commandStatus.textContent = "Drone reset to its home coordinate.";
});
document.querySelector<HTMLButtonElement>("#orbit-monument")!.addEventListener("click", activateOrbitCamera);
controlDroneButton.addEventListener("click", () => {
  if (!drone || deploying) return;
  if (controllingDrone) {
    flyToFreeCameraOverview();
    commandStatus.textContent = "Drone hovering. Free camera active.";
    return;
  }
  flyToFreeCameraOverview();
  controllingDrone = true;
  drone.setManualControl(true);
  viewer.scene.screenSpaceCameraController.enableInputs = false;
  controlDroneButton.textContent = "Release drone";
  controlDroneButton.setAttribute("aria-pressed", "true");
  commandStatus.textContent = "PILOT MODE · WASD relative to drone heading · Q/E turns · Space/Shift up/down (R/F also works) · Esc releases. Mission canceled; trail preserved.";
  viewer.canvas.focus();
});

window.addEventListener("keydown", (event) => {
  if (event.target instanceof HTMLElement && event.target.closest('textarea, input, select, [contenteditable="true"]')) return;
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  if (deploying && event.code === "Escape") { cancelDeployment(); return; }
  if (controllingDrone && event.code === "Escape") { flyToFreeCameraOverview(); return; }
  if (["KeyW", "KeyA", "KeyS", "KeyD", "KeyR", "KeyF"].includes(event.code) || (controllingDrone && ["Space", "ShiftLeft", "ShiftRight", "KeyQ", "KeyE"].includes(event.code))) {
    if (cameraMode !== "free") flyToFreeCameraOverview();
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
    drone.moveManually(forward * Math.sin(pilotView.heading) + right * Math.cos(pilotView.heading), forward * Math.cos(pilotView.heading) - right * Math.sin(pilotView.heading), up, deltaSeconds, pilotView.heading);
    const position = drone.snapshot();
    const center = Cesium.Cartesian3.fromDegrees(position.longitude, position.latitude, position.altitude);
    if (pilotCameraMode === "first") {
      // Fixed mount just above the prism, looking forward along its heading.
      const mount = Cesium.Matrix4.multiplyByPoint(Cesium.Transforms.eastNorthUpToFixedFrame(center), new Cesium.Cartesian3(0, 0, 3), new Cesium.Cartesian3());
      viewer.camera.lookAtTransform(Cesium.Matrix4.IDENTITY);
      viewer.camera.setView({ destination: mount, orientation: { heading: pilotView.heading, pitch: 0, roll: 0 } });
    } else {
      const free = pilotCameraMode === "free";
      viewer.camera.lookAt(center, new Cesium.HeadingPitchRange(pilotView.heading + (free ? freeOrbitHeading : 0), free ? pilotView.pitch : Cesium.Math.toRadians(-18), free ? pilotView.range : 65));
    }
    return;
  }
  if (cameraMode !== "free" || activeCameraKeys.size === 0) return;
  const distance = Math.max(viewer.camera.positionCartographic.height * 0.35, 20) * deltaSeconds;
  if (activeCameraKeys.has("KeyW")) viewer.camera.moveForward(distance * 3);
  if (activeCameraKeys.has("KeyS")) viewer.camera.moveBackward(distance * 3);
  if (activeCameraKeys.has("KeyA")) viewer.camera.moveLeft(distance * 3);
  if (activeCameraKeys.has("KeyD")) viewer.camera.moveRight(distance * 3);
  if (activeCameraKeys.has("KeyR")) viewer.camera.moveUp(distance * 3);
  if (activeCameraKeys.has("KeyF")) viewer.camera.moveDown(distance * 3);
}

let previousTime = Cesium.JulianDate.clone(viewer.clock.currentTime);
let previousCameraTime = performance.now();
viewer.clock.onTick.addEventListener((clock) => {
  const deltaSeconds = Math.max(0, Math.min(0.1, Cesium.JulianDate.secondsDifference(clock.currentTime, previousTime)));
  previousTime = Cesium.JulianDate.clone(clock.currentTime, previousTime);
  for (const item of fleet.drones.values()) item.update(deltaSeconds);
  const cameraTime = performance.now();
  const wasPlaying = fleet.replay.running;
  fleet.updateReplay(cameraTime / 1000);
  replayTime.textContent = `Elapsed: ${fleet.replay.elapsed.toFixed(2)} s`;
  if (fleet.replay.total) {
    replayStatus.textContent = `${fleet.replay.arrived} / ${fleet.replay.total} arrived${fleet.replay.running ? " · Playing at assigned speeds" : " · All paths complete"}`;
  }
  if (wasPlaying && !fleet.replay.running) refreshFleet();
  updateFreeCamera(Math.min(0.1, Math.max(0, (cameraTime - previousCameraTime) / 1000)));
  previousCameraTime = cameraTime;
  const snapshot = drone?.snapshot();
  if (!snapshot) { stateElement.innerHTML = "<dt>Fleet</dt><dd>No drones deployed</dd>"; return; }
  stateElement.innerHTML = [
    ["State", snapshot.state], ["Position", `${snapshot.latitude.toFixed(5)}, ${snapshot.longitude.toFixed(5)}`], ["Altitude", `${snapshot.altitude.toFixed(0)} m`], ["Step", snapshot.totalSteps ? `${snapshot.currentStep} / ${snapshot.totalSteps}` : "—"],
  ].map(([label, value]) => `<dt>${label}</dt><dd>${value}</dd>`).join("");
});
