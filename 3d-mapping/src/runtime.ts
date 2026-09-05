import * as Cesium from "cesium";
import { localToFixed, type LocalVector } from "./coordinates";

type RuntimeDrone = {
  identity: { node_id: string; name: string; capabilities: string[] };
  state: string;
  role: string;
  estimated: { position: LocalVector; position_uncertainty: number; localization_mode: string; battery_estimate: number };
  truth: { position: LocalVector; heading: number; actual_battery: number; online: boolean };
  current_task_id: string | null;
  task_progress: number;
  peers: Record<string, { available: boolean; estimated_link_quality: number }>;
  current_plan: LocalVector[];
};
type RuntimeLink = {
  source_id: string; target_id: string; available: boolean; quality: number;
  distance_m: number; packet_loss: number; obstructed: boolean; partitioned: boolean;
};
type RuntimeTask = {
  id: string; type: string; status: string; priority: number; assigned_nodes: string[];
  target: { point: LocalVector | null; waypoints: LocalVector[] };
};
export type RuntimeSnapshot = {
  simulation_time: number; running: boolean; scenario: string; control_available: boolean;
  origin_lat: number; origin_lon: number; origin_alt: number;
  mission_capability: number;
  network: {
    network_health: number; connected_components: string[][]; largest_component_fraction: number;
    mean_link_quality: number; packet_loss_recent: number; active_nodes: number;
    degraded_nodes: number; relay_nodes: string[]; gps_degraded_count: number;
  };
  interference: Record<"gps_interference" | "network_interference" | "sensor_interference" | "node_failure_rate", number>;
  drones: RuntimeDrone[]; links: RuntimeLink[]; missions: RuntimeTask[];
};

const percent = (value: number): string => `${Math.round(value * 100)}%`;
const escapeHtml = (value: unknown): string => String(value).replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]!);

export function startRuntimeMode(viewer: Cesium.Viewer): void {
  const apiBase = (import.meta.env.VITE_RUNTIME_URL as string | undefined) ?? "http://127.0.0.1:8000";
  const wsBase = apiBase.replace(/^http/, "ws");
  const runtimeStatus = document.querySelector<HTMLElement>("#runtime-status")!;
  const metrics = document.querySelector<HTMLDListElement>("#runtime-metrics")!;
  const detail = document.querySelector<HTMLDListElement>("#drone-state")!;
  const drones = new Map<string, RuntimeDrone>();
  let latest: RuntimeSnapshot | undefined;
  let stopped = false;

  const position = (point: LocalVector) => {
    const snapshot = latest!;
    return localToFixed({ latitude: snapshot.origin_lat, longitude: snapshot.origin_lon, altitude: snapshot.origin_alt }, point);
  };

  function render(snapshot: RuntimeSnapshot): void {
    latest = snapshot;
    drones.clear();
    for (const drone of snapshot.drones) {
      const id = drone.identity.node_id;
      drones.set(id, drone);
      const color = !drone.truth.online ? Cesium.Color.GRAY : drone.role === "RELAY" ? Cesium.Color.CYAN : drone.state === "DEGRADED" ? Cesium.Color.ORANGE : Cesium.Color.LIME;
      const entity = viewer.entities.getById(`runtime-${id}`) ?? viewer.entities.add({
        id: `runtime-${id}`,
        box: { dimensions: new Cesium.Cartesian3(8, 5, 2) },
        label: { font: "13px system-ui", pixelOffset: new Cesium.Cartesian2(0, -24), showBackground: true },
      });
      entity.position = new Cesium.ConstantPositionProperty(position(drone.truth.position));
      entity.orientation = new Cesium.ConstantProperty(Cesium.Transforms.headingPitchRollQuaternion(position(drone.truth.position), new Cesium.HeadingPitchRoll(drone.truth.heading)));
      entity.show = true;
      entity.name = id;
      if (entity.box) entity.box.material = new Cesium.ColorMaterialProperty(color.withAlpha(drone.truth.online ? 0.9 : 0.35));
      if (entity.label) entity.label.text = new Cesium.ConstantProperty(`${id} · ${drone.role}${drone.current_task_id ? `\n${drone.current_task_id}` : ""}`);

      const estimated = viewer.entities.getById(`runtime-estimated-${id}`) ?? viewer.entities.add({ id: `runtime-estimated-${id}`, point: { pixelSize: 7, color: Cesium.Color.YELLOW, outlineWidth: 1, outlineColor: Cesium.Color.BLACK } });
      estimated.position = new Cesium.ConstantPositionProperty(position(drone.estimated.position));
      estimated.show = drone.truth.online;
      const uncertainty = viewer.entities.getById(`runtime-uncertainty-${id}`) ?? viewer.entities.add({ id: `runtime-uncertainty-${id}`, ellipse: { material: Cesium.Color.YELLOW.withAlpha(0.12), outline: true, outlineColor: Cesium.Color.YELLOW.withAlpha(0.65) } });
      uncertainty.position = new Cesium.ConstantPositionProperty(position({ ...drone.estimated.position, z: Math.max(0, drone.estimated.position.z - 1) }));
      if (uncertainty.ellipse) {
        uncertainty.ellipse.semiMajorAxis = new Cesium.ConstantProperty(Math.max(1, drone.estimated.position_uncertainty));
        uncertainty.ellipse.semiMinorAxis = new Cesium.ConstantProperty(Math.max(1, drone.estimated.position_uncertainty));
      }
      uncertainty.show = drone.truth.online;
      const route = viewer.entities.getById(`runtime-route-${id}`) ?? viewer.entities.add({ id: `runtime-route-${id}`, polyline: { width: 2, material: color.withAlpha(0.7) } });
      if (route.polyline) route.polyline.positions = new Cesium.ConstantProperty([position(drone.truth.position), ...drone.current_plan.map(position)]);
      route.show = drone.truth.online && drone.current_plan.length > 0;
    }
    for (const entity of viewer.entities.values.filter((item) => item.id.startsWith("runtime-drone-") || item.id.startsWith("runtime-estimated-drone-") || item.id.startsWith("runtime-uncertainty-drone-") || item.id.startsWith("runtime-route-drone-"))) {
      const id = entity.id.replace(/^runtime-(estimated-|uncertainty-|route-)?/, "");
      if (!drones.has(id)) entity.show = false;
    }
    renderLinks(snapshot.links);
    renderTasks(snapshot.missions);
    metrics.innerHTML = [
      ["Mission", percent(snapshot.mission_capability)], ["Network", percent(snapshot.network.network_health)],
      ["Control", snapshot.control_available ? "ONLINE" : "OFFLINE"], ["Cloud", "NOT REQUIRED"],
      ["Nodes", `${snapshot.network.active_nodes} / ${snapshot.drones.length}`],
      ["Components", String(snapshot.network.connected_components.length)], ["Mean link", percent(snapshot.network.mean_link_quality)],
      ["Packet loss", percent(snapshot.network.packet_loss_recent)], ["GPS degraded", String(snapshot.network.gps_degraded_count)],
      ["Relay", snapshot.network.relay_nodes.join(", ") || "—"], ["Clock", `${snapshot.simulation_time.toFixed(1)} s ${snapshot.running ? "RUNNING" : "PAUSED"}`],
    ].map(([key, value]) => `<dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd>`).join("");
    runtimeStatus.textContent = `CONNECTED · ${snapshot.scenario} · backend simulation authoritative`;
    runtimeStatus.className = "status connected";
    syncControls(snapshot);
  }

  function renderLinks(links: RuntimeLink[]): void {
    const active = new Set<string>();
    for (const link of links) {
      const id = `runtime-link-${link.source_id}-${link.target_id}`;
      active.add(id);
      const entity = viewer.entities.getById(id) ?? viewer.entities.add({ id, polyline: { clampToGround: false } });
      const first = drones.get(link.source_id), second = drones.get(link.target_id);
      entity.show = Boolean(first && second);
      if (!first || !second || !entity.polyline) continue;
      entity.polyline.positions = new Cesium.ConstantProperty([position(first.truth.position), position(second.truth.position)]);
      entity.polyline.width = new Cesium.ConstantProperty(link.available ? 1.5 + 3 * link.quality : 1);
      const color = link.available ? Cesium.Color.fromHsl(0.33 * link.quality, 0.9, 0.55, 0.25 + 0.7 * link.quality) : Cesium.Color.RED.withAlpha(0.3);
      entity.polyline.material = link.available ? new Cesium.ColorMaterialProperty(color) : new Cesium.PolylineDashMaterialProperty({ color });
    }
    for (const entity of viewer.entities.values.filter((item) => item.id.startsWith("runtime-link-"))) if (!active.has(entity.id)) entity.show = false;
  }

  function renderTasks(tasks: RuntimeTask[]): void {
    const active = new Set<string>();
    for (const task of tasks) {
      if (!task.target.point || task.status === "COMPLETED" || task.status === "CANCELLED") continue;
      const id = `runtime-task-${task.id}`;
      active.add(id);
      const entity = viewer.entities.getById(id) ?? viewer.entities.add({ id, point: { pixelSize: 9, color: Cesium.Color.AQUA }, label: { font: "12px system-ui", pixelOffset: new Cesium.Cartesian2(0, -18), showBackground: true } });
      entity.position = new Cesium.ConstantPositionProperty(position(task.target.point));
      if (entity.label) entity.label.text = new Cesium.ConstantProperty(`${task.type} · P${task.priority}\n${task.assigned_nodes.join(", ") || "AUCTION"}`);
      entity.show = true;
    }
    for (const entity of viewer.entities.values.filter((item) => item.id.startsWith("runtime-task-"))) if (!active.has(entity.id)) entity.show = false;
  }

  function showDrone(id: string): void {
    const drone = drones.get(id);
    if (!drone) return;
    const reachable = Object.values(drone.peers).filter((peer) => peer.available).length;
    detail.innerHTML = [
      ["ID", id], ["State", drone.truth.online ? drone.state : "OFFLINE"], ["Role", drone.role],
      ["Task", drone.current_task_id ?? "—"], ["Battery", percent(drone.truth.actual_battery)],
      ["GPS", drone.estimated.localization_mode], ["Uncertainty", `${drone.estimated.position_uncertainty.toFixed(1)} m`],
      ["Peers", `${reachable} / ${Object.keys(drone.peers).length}`], ["Capabilities", drone.identity.capabilities.join(", ")],
      ["Local xyz", `${drone.truth.position.x.toFixed(1)}, ${drone.truth.position.y.toFixed(1)}, ${drone.truth.position.z.toFixed(1)}`],
    ].map(([key, value]) => `<dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd>`).join("");
  }

  viewer.selectedEntityChanged.addEventListener((entity) => {
    if (entity?.id.startsWith("runtime-drone-")) showDrone(entity.id.replace("runtime-", ""));
  });

  function connect(): void {
    if (stopped) return;
    runtimeStatus.textContent = "CONNECTING · ws://127.0.0.1:8000/ws";
    runtimeStatus.className = "status";
    const socket = new WebSocket(`${wsBase}/ws`);
    socket.onmessage = (event) => render(JSON.parse(String(event.data)) as RuntimeSnapshot);
    socket.onerror = () => socket.close();
    socket.onclose = () => {
      runtimeStatus.textContent = "DISCONNECTED · retrying backend…";
      runtimeStatus.className = "status disconnected";
      window.setTimeout(connect, 1000);
    };
  }

  async function post(path: string, body?: unknown): Promise<void> {
    const response = await fetch(`${apiBase}${path}`, { method: "POST", headers: body ? { "Content-Type": "application/json" } : undefined, body: body ? JSON.stringify(body) : undefined });
    if (!response.ok) throw new Error(await response.text());
  }

  function syncControls(snapshot: RuntimeSnapshot): void {
    for (const key of Object.keys(snapshot.interference) as (keyof RuntimeSnapshot["interference"])[]) {
      const input = document.querySelector<HTMLInputElement>(`#${key.replaceAll("_", "-")}`);
      if (input && document.activeElement !== input) input.value = String(snapshot.interference[key]);
    }
  }

  for (const key of ["gps_interference", "network_interference", "sensor_interference", "node_failure_rate"] as const) {
    document.querySelector<HTMLInputElement>(`#${key.replaceAll("_", "-")}`)!.addEventListener("change", async () => {
      if (!latest) return;
      const body = { ...latest.interference };
      for (const item of Object.keys(body) as (keyof typeof body)[]) body[item] = Number(document.querySelector<HTMLInputElement>(`#${item.replaceAll("_", "-")}`)!.value);
      await post("/api/interference", body);
    });
  }
  document.querySelector<HTMLSelectElement>("#runtime-preset")!.addEventListener("change", (event) => post(`/api/simulation/scenario/${(event.target as HTMLSelectElement).value}`));
  document.querySelector<HTMLButtonElement>("#runtime-fail-drone")!.addEventListener("click", () => post("/api/drones/fail-random"));
  document.querySelector<HTMLButtonElement>("#runtime-fail-control")!.addEventListener("click", () => post("/api/control/fail"));
  document.querySelector<HTMLButtonElement>("#runtime-reset")!.addEventListener("click", () => post("/api/simulation/reset"));
  document.querySelector<HTMLButtonElement>("#runtime-pause")!.addEventListener("click", () => post(latest?.running ? "/api/simulation/pause" : "/api/simulation/resume"));
  connect();
  window.addEventListener("beforeunload", () => { stopped = true; });
}
