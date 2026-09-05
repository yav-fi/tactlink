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
viewer.camera.flyTo({ destination: Cesium.Cartesian3.fromDegrees(home.longitude, home.latitude - 0.008, 900), orientation: { heading: 0, pitch: Cesium.Math.toRadians(-35), roll: 0 } });
const drone = new DroneController(viewer, home);

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

document.querySelector<HTMLButtonElement>("#run-mission")!.addEventListener("click", () => {
  try {
    drone.run(parseMission(missionInput.value));
    commandStatus.textContent = "Mission accepted.";
  } catch (error) {
    commandStatus.textContent = error instanceof Error ? error.message : "Mission could not be parsed.";
  }
});
document.querySelector<HTMLButtonElement>("#reset-mission")!.addEventListener("click", () => {
  drone.reset();
  commandStatus.textContent = "Drone reset to its home coordinate.";
});

let previousTime = Cesium.JulianDate.clone(viewer.clock.currentTime);
viewer.clock.onTick.addEventListener((clock) => {
  const deltaSeconds = Math.max(0, Math.min(0.1, Cesium.JulianDate.secondsDifference(clock.currentTime, previousTime)));
  previousTime = Cesium.JulianDate.clone(clock.currentTime, previousTime);
  drone.update(deltaSeconds);
  const snapshot = drone.snapshot();
  stateElement.innerHTML = [
    ["State", snapshot.state], ["Position", `${snapshot.latitude.toFixed(5)}, ${snapshot.longitude.toFixed(5)}`], ["Altitude", `${snapshot.altitude.toFixed(0)} m`], ["Step", snapshot.totalSteps ? `${snapshot.currentStep} / ${snapshot.totalSteps}` : "—"],
  ].map(([label, value]) => `<dt>${label}</dt><dd>${value}</dd>`).join("");
});
