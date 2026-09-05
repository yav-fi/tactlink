import "cesium/Build/Cesium/Widgets/widgets.css";
import "./style.css";
import * as Cesium from "cesium";
import { DroneController } from "./drone-controller";
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
  baseLayerPicker: false,
  geocoder: false,
  homeButton: false,
  infoBox: false,
  navigationHelpButton: false,
  sceneModePicker: false,
  selectionIndicator: false,
  timeline: false,
  terrainProvider: token ? await Cesium.createWorldTerrainAsync() : undefined,
});

viewer.scene.globe.enableLighting = true;
const drone = new DroneController(viewer, home);
const activeCameraKeys = new Set<string>();
const controlDroneButton = document.querySelector<HTMLButtonElement>("#control-drone")!;
let controllingDrone = false;

function releaseDrone(): void {
  if (controllingDrone) drone.setManualControl(false);
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

cameraHandler.setInputAction(() => { isOrbitDragging = cameraMode === "orbit"; }, Cesium.ScreenSpaceEventType.LEFT_DOWN);
cameraHandler.setInputAction(() => { isOrbitDragging = false; }, Cesium.ScreenSpaceEventType.LEFT_UP);
cameraHandler.setInputAction((movement: { startPosition: Cesium.Cartesian2; endPosition: Cesium.Cartesian2 }) => {
  if (!isOrbitDragging || cameraMode !== "orbit") return;
  orbitCamera.heading -= (movement.endPosition.x - movement.startPosition.x) * 0.008;
  orbitCamera.pitch = Cesium.Math.clamp(orbitCamera.pitch + (movement.endPosition.y - movement.startPosition.y) * 0.006, Cesium.Math.toRadians(-85), Cesium.Math.toRadians(-5));
  applyOrbitCamera();
}, Cesium.ScreenSpaceEventType.MOUSE_MOVE);
cameraHandler.setInputAction((delta: number) => {
  if (cameraMode !== "orbit") return;
  orbitCamera.range = Cesium.Math.clamp(orbitCamera.range + delta * 0.22, 80, 4_000);
  applyOrbitCamera();
}, Cesium.ScreenSpaceEventType.WHEEL);
activateOrbitCamera();

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
    if (controllingDrone) flyToFreeCameraOverview();
    drone.run(mission);
    commandStatus.textContent = "Mission accepted.";
  } catch (error) {
    commandStatus.textContent = error instanceof Error ? error.message : "Mission could not be parsed.";
  }
});
document.querySelector<HTMLButtonElement>("#reset-mission")!.addEventListener("click", () => {
  if (controllingDrone) flyToFreeCameraOverview();
  drone.reset();
  commandStatus.textContent = "Drone reset to its home coordinate.";
});
document.querySelector<HTMLButtonElement>("#orbit-monument")!.addEventListener("click", activateOrbitCamera);
document.querySelector<HTMLButtonElement>("#free-camera")!.addEventListener("click", flyToFreeCameraOverview);
controlDroneButton.addEventListener("click", () => {
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
  commandStatus.textContent = "You control the drone. W/S north/south, A/D west/east, R/F up/down. Previous mission canceled. Release keys to hover.";
  viewer.canvas.focus();
});

window.addEventListener("keydown", (event) => {
  if (event.target instanceof HTMLElement && event.target.closest('textarea, input, select, [contenteditable="true"]')) return;
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  if (["KeyW", "KeyA", "KeyS", "KeyD", "KeyR", "KeyF"].includes(event.code)) {
    if (cameraMode !== "free") flyToFreeCameraOverview();
    viewer.camera.cancelFlight();
    activeCameraKeys.add(event.code);
    event.preventDefault();
  }
});
window.addEventListener("keyup", (event) => activeCameraKeys.delete(event.code));
window.addEventListener("blur", () => activeCameraKeys.clear());
document.addEventListener("visibilitychange", () => activeCameraKeys.clear());
document.addEventListener("focusin", () => activeCameraKeys.clear());
viewer.canvas.tabIndex = 0;
viewer.canvas.addEventListener("pointerdown", () => viewer.canvas.focus());

function updateFreeCamera(deltaSeconds: number): void {
  if (controllingDrone) {
    const axis = (positive: string, negative: string) => Number(activeCameraKeys.has(positive)) - Number(activeCameraKeys.has(negative));
    drone.moveManually(axis("KeyD", "KeyA"), axis("KeyW", "KeyS"), axis("KeyR", "KeyF"), deltaSeconds);
    const position = drone.snapshot();
    viewer.camera.lookAt(Cesium.Cartesian3.fromDegrees(position.longitude, position.latitude, position.altitude), new Cesium.HeadingPitchRange(0, Cesium.Math.toRadians(-25), 100));
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
  drone.update(deltaSeconds);
  const cameraTime = performance.now();
  updateFreeCamera(Math.min(0.1, Math.max(0, (cameraTime - previousCameraTime) / 1000)));
  previousCameraTime = cameraTime;
  const snapshot = drone.snapshot();
  stateElement.innerHTML = [
    ["State", snapshot.state], ["Position", `${snapshot.latitude.toFixed(5)}, ${snapshot.longitude.toFixed(5)}`], ["Altitude", `${snapshot.altitude.toFixed(0)} m`], ["Step", snapshot.totalSteps ? `${snapshot.currentStep} / ${snapshot.totalSteps}` : "—"],
  ].map(([label, value]) => `<dt>${label}</dt><dd>${value}</dd>`).join("");
});
