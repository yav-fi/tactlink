import "cesium/Build/Cesium/Widgets/widgets.css";
import "./style.css";
import * as Cesium from "cesium";
import { DroneController } from "./drone-controller";
import { Fleet, DRONE_COLORS } from "./fleet";
import { parseMission, sampleMission } from "./mission";
import { previewCoordinate, previewMission, type FlightPreview } from "./flight-preview";

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
let drone: DroneController | undefined;
const previewButton = document.querySelector<HTMLButtonElement>("#preview-flight")!;
const previewStatus = document.querySelector<HTMLParagraphElement>("#preview-status")!;
let flightRoute: Cesium.Entity | undefined;
const bridgeUrl = (import.meta.env.VITE_FLIGHT_BRIDGE_URL ?? "http://127.0.0.1:8765").replace(/\/$/, "");
previewButton.addEventListener("click", async () => {
  const selected = drone;
  if (!selected) { previewStatus.textContent = "Deploy and select a drone first."; return; }
  previewButton.disabled = true;
  previewStatus.textContent = "Validating flight instruction...";
  try {
    const response = await fetch(`${bridgeUrl}/api/preview`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: document.querySelector<HTMLTextAreaElement>("#flight-text")!.value }),
      signal: AbortSignal.timeout(10000),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "Flight instruction was rejected.");
    if (drone !== selected || !fleet.drones.has(selected.id)) throw new Error("Selected drone changed; generate the preview again.");
    const path = result as FlightPreview;
    const mission = previewMission(path, selected.id, selected.homeCoordinates, selected.speedMph * 0.44704);
    const positions = path.segments.flatMap(segment => segment.points.map(point => {
      const geo = previewCoordinate(point, selected.homeCoordinates);
      return Cesium.Cartesian3.fromDegrees(geo.longitude, geo.latitude, geo.altitude);
    }));
    if (flightRoute) viewer.entities.remove(flightRoute);
    flightRoute = viewer.entities.add({ name: "Validated flight preview", polyline: {
      positions, width: 3, material: Cesium.Color.CYAN, arcType: Cesium.ArcType.NONE, clampToGround: false,
    } });
    missionInput.value = JSON.stringify(mission, null, 2);
    previewStatus.textContent = `Path ready for ${selected.id}. Run mission animates the simulated drone. Landing returns to deployment height.`;
  } catch (error) {
    previewStatus.textContent = error instanceof Error ? error.message : "Cannot reach the flight bridge on port 8765.";
  } finally {
    previewButton.disabled = false;
  }
});
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
let preview: Cesium.Entity | undefined;
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
    const color = DRONE_COLORS[index++ % DRONE_COLORS.length];
    droneSelect.add(new Option(`${item.id.replace("_", " ")} · ${color.name}`, item.id));
  }
  droneSelect.value = drone?.id ?? "";
  droneSelect.disabled = deploying || !drone;
  speedInput.disabled = deploying || !drone;
  speedInput.value = String(drone?.speedMph ?? 60);
  speedInput.removeAttribute("aria-invalid");
  speedStatus.textContent = `${drone?.speedMph ?? 60} mph · ${((drone?.speedMph ?? 60) * 0.44704).toFixed(2)} m/s`;
  for (const id of ["run-mission", "reset-mission", "control-drone"]) {
    document.querySelector<HTMLButtonElement>(`#${id}`)!.disabled = deploying || !drone;
  }
}

function cancelDeployment(): void {
  deploying = false;
  pickedSurface = undefined;
  if (preview) viewer.entities.remove(preview);
  preview = undefined;
  deploymentPanel.hidden = true;
  confirmDeployment.disabled = true;
  refreshFleet();
}

function updatePreview(): void {
  const height = heightInput.valueAsNumber;
  confirmDeployment.disabled = !pickedSurface || !heightInput.checkValidity() || !Number.isFinite(height);
  if (confirmDeployment.disabled || !pickedSurface) return;
  const position = Cesium.Cartesian3.fromRadians(pickedSurface.longitude, pickedSurface.latitude, pickedSurface.height + height);
  if (!preview) {
    preview = viewer.entities.add({ id: "deployment-preview", position, point: { pixelSize: 12, color: Cesium.Color.fromCssColorString(fleet.nextColor.hex) }, label: { text: "START HERE", pixelOffset: new Cesium.Cartesian2(0, -22), font: "14px system-ui", showBackground: true } });
  } else preview.position = new Cesium.ConstantPositionProperty(position);
  deploymentStatus.textContent = `Starting point: ${Cesium.Math.toDegrees(pickedSurface.latitude).toFixed(5)}, ${Cesium.Math.toDegrees(pickedSurface.longitude).toFixed(5)}. Click elsewhere to adjust, then Deploy here.`;
}

document.querySelector<HTMLButtonElement>("#deploy-drone")!.addEventListener("click", () => {
  cancelDeployment();
  flyToFreeCameraOverview();
  deploying = true;
  deploymentPanel.hidden = false;
  deploymentStatus.textContent = `Next drone: ${fleet.nextColor.name}. Fly the camera, then click a starting point on the map. No route is recorded during placement.`;
  commandStatus.textContent = "Choose a starting point, then confirm deployment.";
  refreshFleet();
});
heightInput.addEventListener("input", updatePreview);
document.querySelector<HTMLButtonElement>("#cancel-deployment")!.addEventListener("click", cancelDeployment);
cameraHandler.setInputAction((event: { position: Cesium.Cartesian2 }) => {
  if (!deploying) return;
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
  if (!deploying || !pickedSurface || !heightInput.checkValidity()) return;
  const selected = fleet.deploy({ latitude: Cesium.Math.toDegrees(pickedSurface.latitude), longitude: Cesium.Math.toDegrees(pickedSurface.longitude), altitude: pickedSurface.height + heightInput.valueAsNumber });
  drone = selected;
  cancelDeployment();
  selectDrone(selected.id);
  commandStatus.textContent = `${selected.id.replace("_", " ")} deployed. Choose Control drone to fly it, or deploy another.`;
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
  if (flightRoute) viewer.entities.remove(flightRoute);
  flightRoute = undefined;
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
document.querySelector<HTMLButtonElement>("#free-camera")!.addEventListener("click", flyToFreeCameraOverview);
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
  updateFreeCamera(Math.min(0.1, Math.max(0, (cameraTime - previousCameraTime) / 1000)));
  previousCameraTime = cameraTime;
  const snapshot = drone?.snapshot();
  if (!snapshot) { stateElement.innerHTML = "<dt>Fleet</dt><dd>No drones deployed</dd>"; return; }
  stateElement.innerHTML = [
    ["State", snapshot.state], ["Position", `${snapshot.latitude.toFixed(5)}, ${snapshot.longitude.toFixed(5)}`], ["Altitude", `${snapshot.altitude.toFixed(0)} m`], ["Step", snapshot.totalSteps ? `${snapshot.currentStep} / ${snapshot.totalSteps}` : "—"],
  ].map(([label, value]) => `<dt>${label}</dt><dd>${value}</dd>`).join("");
});
