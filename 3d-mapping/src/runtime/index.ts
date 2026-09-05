import "../runtime.css";
import * as Cesium from "cesium";
import { CameraDirector, type CameraMode } from "./camera";
import { focusTargets, styleFor } from "./events";
import { GeoFrame } from "./frame";
import { PresentationBuffer } from "./interpolator";
import { CommTerrainLayer } from "./layers/comms";
import { CoverageLayer, type CoverageMode } from "./layers/coverage";
import { DroneLayer } from "./layers/drones";
import { NetworkLayer } from "./layers/network";
import { ObjectiveLayer } from "./layers/objectives";
import { WorldLayer } from "./layers/world";
import { DisturbancePanel } from "./panels/disturbance";
import { Inspector } from "./panels/inspector";
import { LayersPanel, type LayerState } from "./panels/layers";
import { missionState, StatusPanels } from "./panels/status";
import { Timeline } from "./panels/timeline";
import { buildShell, escapeHtml, type Shell } from "./shell";
import type {
  LocalVector,
  RuntimeEvent,
  RuntimeSnapshot,
  RuntimeTask,
  WorldDefinition,
} from "./types";

type CompileResult = {
  result: {
    status: "READY" | "NEEDS_CLARIFICATION" | "REJECTED";
    plan: Record<string, unknown> | null;
    clarification_question: string | null;
    rejection_reason: string | null;
    warnings: string[];
    interpretation_summary: string;
  };
};

const finiteVector = (value: LocalVector): boolean =>
  Number.isFinite(value.x) && Number.isFinite(value.y) && Number.isFinite(value.z);

const isSnapshot = (value: unknown): value is RuntimeSnapshot => {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<RuntimeSnapshot>;
  return Number.isFinite(candidate.simulation_time)
    && Number.isFinite(candidate.origin_lat)
    && Number.isFinite(candidate.origin_lon)
    && Number.isFinite(candidate.origin_alt)
    && Array.isArray(candidate.drones)
    && candidate.drones.every((drone) => finiteVector(drone.truth.position))
    && Array.isArray(candidate.missions)
    && Array.isArray(candidate.links)
    && Boolean(candidate.adaptive);
};

const selectedRegionFor = (task: RuntimeTask | undefined): string | null =>
  task?.target.region_id ?? null;

/** Starts the backend-authoritative mission-control experience. */
export function startRuntimeMode(viewer: Cesium.Viewer): void {
  const apiBase = ((import.meta.env.VITE_RUNTIME_URL as string | undefined) ?? "http://127.0.0.1:8000").replace(/\/$/, "");
  const wsBase = apiBase.replace(/^http/, "ws");
  const shell = buildShell();
  const frame = new GeoFrame();
  const buffer = new PresentationBuffer();
  const drones = new DroneLayer(viewer, frame);
  const objectives = new ObjectiveLayer(viewer, frame);
  const network = new NetworkLayer(viewer, (id) => drones.positionOf(id));
  const coverage = new CoverageLayer(viewer, frame);
  const comms = new CommTerrainLayer(viewer, frame);
  const world = new WorldLayer(viewer, frame);
  let snapshot: RuntimeSnapshot | undefined;
  let pendingWorld: WorldDefinition | undefined;
  let taskById = new Map<string, RuntimeTask>();
  let selectedDrone: string | null = null;
  let selectedTask: string | null = null;
  let mapCursor: LocalVector | null = null;
  let compiledPlan: Record<string, unknown> | null = null;
  let stopped = false;
  let reconnectAttempt = 0;
  let demoMode = false;

  const banner = shell.query("#mc-banner");
  const connection = shell.query("#mc-connection");
  const commandStatus = shell.query("#mc-command-status");
  const textStatus = shell.query("#mc-command-text-status");
  const commandContext = shell.query("#mc-command-context");
  const preview = shell.query("#mc-plan-preview");
  const confirmPlan = shell.query<HTMLButtonElement>("#mc-command-confirm");
  const commandText = shell.query<HTMLTextAreaElement>("#mc-command-text");
  const targetSelect = shell.query<HTMLSelectElement>("#mc-command-target");
  const toasts = shell.query("#mc-toasts");

  const regionBounds = (regionId: string): Cesium.Rectangle | undefined => {
    const region = world.world?.regions.find((item) => item.id === regionId);
    if (!region) return undefined;
    const points = [
      { x: region.center.x - region.radius, y: region.center.y - region.radius, z: 0 },
      { x: region.center.x + region.radius, y: region.center.y + region.radius, z: 0 },
    ].map((point) => Cesium.Cartographic.fromCartesian(frame.toFixed(point)));
    return Cesium.Rectangle.fromCartographicArray(points);
  };

  const camera = new CameraDirector(viewer, {
    dronePosition: (id) => drones.positionOf(id),
    droneForward: (id) => drones.forwardOf(id),
    droneEntity: (id) => viewer.entities.getById(drones.entityIdFor(id)),
    allPositions: () => [
      ...((snapshot?.drones ?? []).map((item) => drones.positionOf(item.identity.node_id)).filter(Boolean) as Cesium.Cartesian3[]),
      ...((snapshot?.missions ?? []).map((item) => objectives.positionOf(item.id)).filter(Boolean) as Cesium.Cartesian3[]),
    ],
    regionBounds,
  });

  const selectDrone = (id: string | null): void => {
    selectedDrone = id;
    if (id) selectedTask = snapshot?.drones.find((item) => item.identity.node_id === id)?.current_task_id ?? selectedTask;
    drones.setSelected(selectedDrone);
    objectives.setSelected(selectedTask);
    network.setFocus(selectedDrone);
    camera.setTarget(selectedDrone);
    renderPanels();
  };

  const selectTask = (id: string | null): void => {
    selectedTask = id;
    objectives.setSelected(id);
    const task = id ? taskById.get(id) : undefined;
    camera.setFocusRegion(selectedRegionFor(task));
    renderPanels();
  };

  const statusPanels = new StatusPanels(shell, (id) => selectDrone(id), (id) => selectTask(id));
  const inspector = new Inspector(
    shell,
    (id) => { selectDrone(id); camera.setMode("follow"); },
    (id) => { selectTask(id); camera.setMode("focus"); },
  );
  const timeline = new Timeline(shell, (droneIds, taskIds) => {
    if (droneIds[0]) selectDrone(droneIds[0]);
    if (taskIds[0]) selectTask(taskIds[0]);
    camera.setMode(taskIds[0] ? "focus" : "follow");
  });

  const applyLayers = (state: LayerState): void => {
    drones.setOptions({
      showTrails: state.trails,
      showPlans: state.plans,
      showAltitudeCues: state.altitude,
      showEstimates: state.estimates,
    });
    network.setOptions({
      showLinks: state.links,
      showDownLinks: state.downLinks,
      showLinkLabels: state.linkLabels,
      showRelayRange: state.relayRange,
    });
    coverage.setOptions({ show: state.coverage });
    objectives.setOptions({ show: true, showPatterns: state.patterns });
    comms.setOptions({ show: state.comms });
    world.setOptions({ showRegions: state.regions, showObstacles: state.obstacles });
  };
  const layers = new LayersPanel(
    shell,
    applyLayers,
    (mode: CoverageMode) => coverage.setOptions({ mode, nodeId: selectedDrone }),
  );
  applyLayers(layers.state);

  async function request<T>(path: string, method = "POST", body?: unknown): Promise<T> {
    const response = await fetch(`${apiBase}${path}`, {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!response.ok) {
      const message = await response.text();
      throw new Error(message || `${response.status} ${response.statusText}`);
    }
    return response.json() as Promise<T>;
  }

  const disturbances = new DisturbancePanel(shell, {
    setInterference: (config) => request("/api/interference", "POST", config),
    setPreset: (preset) => request(`/api/simulation/scenario/${encodeURIComponent(preset)}`),
    failDrone: () => request("/api/drones/fail-random"),
    failControl: () => request("/api/control/fail"),
    recoverDrone: (id) => request(`/api/drones/${encodeURIComponent(id)}/recover`),
    recoverControl: () => request("/api/control/recover"),
    togglePause: (running) => request(running ? "/api/simulation/pause" : "/api/simulation/resume"),
    reset: async () => { await request("/api/simulation/reset"); timeline.clear(); buffer.reset(); },
  });

  function renderPanels(): void {
    if (!snapshot) return;
    statusPanels.render(snapshot, taskById, selectedDrone, selectedTask);
    layers.render(snapshot, selectedDrone);
    disturbances.sync(snapshot);
    coverage.setOptions({ mode: layers.mode, nodeId: selectedDrone });
    const task = selectedTask ? taskById.get(selectedTask) : undefined;
    const drone = selectedDrone
      ? snapshot.drones.find((item) => item.identity.node_id === selectedDrone)
      : undefined;
    if (drone) inspector.renderDrone(drone, snapshot, taskById);
    else if (task) inspector.renderTask(task, snapshot);
    else inspector.renderEmpty();
    commandContext.textContent = [
      selectedDrone ? `drone ${selectedDrone}` : null,
      selectedRegionFor(task) ? `region ${selectedRegionFor(task)}` : null,
      mapCursor ? `map ${mapCursor.x.toFixed(0)}, ${mapCursor.y.toFixed(0)} m` : null,
    ].filter(Boolean).join(" · ") || "No contextual target selected.";
  }

  function toast(event: RuntimeEvent): void {
    const style = styleFor(event.event_type);
    const node = document.createElement("div");
    node.className = "mc-toast";
    node.dataset.tone = style.tone;
    node.innerHTML = `<span class="mc-toast-mark"></span><span><b>${escapeHtml(style.title)}</b><small>${escapeHtml(event.human_readable_summary)}</small></span>`;
    toasts.append(node);
    window.setTimeout(() => node.classList.add("is-leaving"), 4200);
    window.setTimeout(() => node.remove(), 4550);
  }

  function accept(next: RuntimeSnapshot): void {
    snapshot = next;
    reconnectAttempt = 0;
    frame.update(next.origin_lat, next.origin_lon, next.origin_alt);
    if (pendingWorld) {
      world.build(pendingWorld);
      comms.setWorldBounds(pendingWorld.minimum, pendingWorld.maximum);
      fillTargets(pendingWorld);
      pendingWorld = undefined;
    }
    buffer.ingest(next, performance.now());
    taskById = new Map(next.missions.map((task) => [task.id, task]));
    objectives.update(next);
    coverage.update(next);
    comms.update(next, performance.now());
    const fresh = timeline.ingest(next.events);
    renderPanels();

    const state = missionState(next);
    shell.query("#mc-mission-state").textContent = state.label;
    shell.query("#mc-mission-state").setAttribute("data-tone", state.tone);
    shell.query("#mc-scenario").textContent = `SCENARIO ${next.scenario}`;
    shell.query("#mc-clock").textContent = `T+${next.simulation_time.toFixed(1)}`;
    connection.textContent = "LIVE";
    connection.setAttribute("data-tone", "ok");
    banner.className = "";
    banner.replaceChildren();

    if (demoMode) {
      const headline = [...fresh].reverse().find((event: RuntimeEvent) => styleFor(event.event_type).tier === "headline");
      if (headline) {
        toast(headline);
        const targets = focusTargets(headline);
        if (targets.tasks[0]) { selectTask(targets.tasks[0]); camera.setMode("focus"); }
        else if (targets.drones[0]) { selectDrone(targets.drones[0]); camera.setMode("follow"); }
      }
    }
  }

  function connect(): void {
    if (stopped) return;
    connection.textContent = reconnectAttempt ? `RETRY ${reconnectAttempt}` : "CONNECTING";
    connection.setAttribute("data-tone", reconnectAttempt ? "warn" : "idle");
    banner.className = "mc-banner";
    banner.innerHTML = `<span class="mc-spinner"></span><b>Connecting to runtime</b><p><code>${escapeHtml(wsBase)}/ws</code><br>The backend remains authoritative.</p>`;
    const socket = new WebSocket(`${wsBase}/ws`);
    socket.onmessage = (event) => {
      try {
        const value: unknown = JSON.parse(String(event.data));
        if (!isSnapshot(value)) throw new Error("invalid runtime snapshot");
        accept(value);
      } catch (error) {
        connection.textContent = "BAD SNAPSHOT";
        connection.setAttribute("data-tone", "crit");
        console.error(error);
      }
    };
    socket.onerror = () => socket.close();
    socket.onclose = () => {
      if (stopped) return;
      reconnectAttempt += 1;
      const delay = Math.min(8000, 500 * 2 ** Math.min(4, reconnectAttempt));
      connection.textContent = "DISCONNECTED";
      connection.setAttribute("data-tone", "crit");
      window.setTimeout(connect, delay);
    };
  }

  function fillTargets(definition: WorldDefinition): void {
    targetSelect.replaceChildren(new Option("Map cursor / selected point", ""));
    for (const region of definition.regions) targetSelect.add(new Option(`Region ${region.id}`, region.id));
  }

  async function compile(amend: boolean): Promise<void> {
    const utterance = commandText.value.trim();
    if (!utterance) { textStatus.textContent = "Enter an instruction first."; return; }
    textStatus.textContent = amend ? "Compiling amendment…" : "Compiling plan preview…";
    confirmPlan.disabled = true;
    const selectedRegion = targetSelect.value || selectedRegionFor(selectedTask ? taskById.get(selectedTask) : undefined);
    try {
      const payload = {
        utterance,
        selected_region: selectedRegion || null,
        map_cursor: mapCursor,
        selected_drone_id: selectedDrone,
        start: false,
      };
      const response = await request<CompileResult>(amend ? "/api/mission-plans/amend" : "/api/mission-plans/compile", "POST", payload);
      const result = response.result;
      compiledPlan = result.status === "READY" ? result.plan : null;
      confirmPlan.disabled = !compiledPlan || amend;
      textStatus.textContent = result.status === "READY"
        ? (amend ? "Amendment applied to the active plan." : "Plan validated. Review it, then confirm.")
        : result.clarification_question ?? result.rejection_reason ?? result.status;
      preview.textContent = result.interpretation_summary || (result.plan ? JSON.stringify(result.plan, null, 2) : "");
    } catch (error) {
      textStatus.textContent = error instanceof Error ? error.message : "Compiler request failed.";
    }
  }

  shell.query("#mc-command-send-text").addEventListener("click", () => void compile(false));
  shell.query("#mc-command-amend").addEventListener("click", () => void compile(true));
  confirmPlan.addEventListener("click", async () => {
    const planId = typeof compiledPlan?.id === "string" ? compiledPlan.id : null;
    if (!compiledPlan || !planId) return;
    try {
      await request(`/api/mission-plans/${encodeURIComponent(planId)}/start`, "POST", compiledPlan);
      textStatus.textContent = `Plan ${planId} started.`;
      confirmPlan.disabled = true;
    } catch (error) {
      textStatus.textContent = error instanceof Error ? error.message : "Plan start failed.";
    }
  });

  shell.query("#mc-question-send").addEventListener("click", async () => {
    const question = shell.query<HTMLTextAreaElement>("#mc-question").value.trim();
    const result = shell.query("#mc-question-result");
    if (!question) { result.textContent = "Enter a question first."; return; }
    try {
      const response = await request<{ answer: string; read_only: boolean }>("/api/mission-intel/query", "POST", { question });
      result.textContent = `${response.answer}${response.read_only ? " · read-only" : ""}`;
    } catch (error) {
      result.textContent = error instanceof Error ? error.message : "Query failed.";
    }
  });

  shell.query("#mc-command-submit").addEventListener("click", async () => {
    const type = shell.query<HTMLSelectElement>("#mc-command-type").value;
    const regionId = targetSelect.value || null;
    const region = world.world?.regions.find((item) => item.id === regionId);
    const point = region?.center ?? mapCursor;
    if (!point) { commandStatus.textContent = "Choose a region or pick a point on the map."; return; }
    try {
      await request("/api/missions", "POST", {
        type,
        target: { point, waypoints: [], region_id: regionId, entity_id: null },
        desired_units: Number(shell.query<HTMLInputElement>("#mc-command-units").value),
        minimum_units: 1,
        priority: Number(shell.query<HTMLInputElement>("#mc-command-priority").value),
      });
      commandStatus.textContent = `${type} objective submitted to the runtime.`;
    } catch (error) {
      commandStatus.textContent = error instanceof Error ? error.message : "Mission submission failed.";
    }
  });

  let picking = false;
  shell.query("#mc-command-pick").addEventListener("click", () => {
    picking = true;
    commandStatus.textContent = "Click a visible map surface to set the target.";
  });
  const pickHandler = new Cesium.ScreenSpaceEventHandler(viewer.canvas);
  pickHandler.setInputAction((event: { position: Cesium.Cartesian2 }) => {
    if (!picking) return;
    let fixed = viewer.scene.pickPositionSupported ? viewer.scene.pickPosition(event.position) : undefined;
    if (!fixed) {
      const ray = viewer.camera.getPickRay(event.position);
      if (ray) fixed = viewer.scene.globe.pick(ray, viewer.scene);
    }
    if (!fixed) { commandStatus.textContent = "Pick a visible map surface, not the sky."; return; }
    mapCursor = frame.toLocal(fixed);
    targetSelect.value = "";
    picking = false;
    commandStatus.textContent = `Target set at ${mapCursor.x.toFixed(0)}, ${mapCursor.y.toFixed(0)} m.`;
    renderPanels();
  }, Cesium.ScreenSpaceEventType.LEFT_CLICK);

  viewer.selectedEntityChanged.addEventListener((entity) => {
    if (!entity) return;
    const droneId = drones.droneIdFromEntity(entity.id);
    const taskId = objectives.taskIdFromEntity(entity.id);
    if (droneId) selectDrone(droneId);
    if (taskId) selectTask(taskId);
  });

  shell.query("#mc-tabs").addEventListener("click", (event) => {
    const button = (event.target as HTMLElement).closest<HTMLButtonElement>("[data-tab]");
    if (!button) return;
    for (const tab of shell.all<HTMLButtonElement>("[data-tab]")) tab.setAttribute("aria-selected", String(tab === button));
    for (const panel of shell.all<HTMLElement>("[data-panel]")) panel.classList.toggle("mc-hidden", panel.dataset.panel !== button.dataset.tab);
  });
  shell.query("#mc-camera").addEventListener("click", (event) => {
    const button = (event.target as HTMLElement).closest<HTMLButtonElement>("[data-camera]");
    if (button) camera.setMode(button.dataset.camera as CameraMode);
  });
  camera.onChange((mode) => {
    for (const button of shell.all<HTMLButtonElement>("[data-camera]")) button.classList.toggle("is-active", button.dataset.camera === mode);
  });
  shell.query("#mc-demo").addEventListener("click", (event) => {
    demoMode = !demoMode;
    const button = event.currentTarget as HTMLButtonElement;
    button.setAttribute("aria-pressed", String(demoMode));
    button.classList.toggle("is-active", demoMode);
  });
  shell.query("#mc-legend-grid").innerHTML = [
    ["#3ddc97", "mission"], ["#b78bff", "relay"], ["#ffb454", "degraded"],
    ["#ff6b6b", "critical/down"], ["#63a9ff", "observed belief"],
  ].map(([color, label]) => `<span class="mc-legend-item"><i style="background:${color}"></i>${label}</span>`).join("");

  void request<WorldDefinition>("/api/world", "GET").then((definition) => {
    pendingWorld = definition;
    if (snapshot) accept(snapshot);
    shell.query("#mc-world").classList.remove("mc-hidden");
    shell.query("#mc-world").textContent = "MAP LIVE";
  }).catch(() => {
    shell.query("#mc-world").classList.remove("mc-hidden");
    shell.query("#mc-world").textContent = "MAP FALLBACK";
  });

  const animate = (now: number): void => {
    if (!stopped && snapshot) {
      drones.update(snapshot, buffer.sample(now), taskById);
      network.update(snapshot);
    }
    if (!stopped) requestAnimationFrame(animate);
  };
  requestAnimationFrame(animate);
  connect();
  camera.setMode("overview");
  window.addEventListener("beforeunload", () => {
    stopped = true;
    pickHandler.destroy();
  });
}
