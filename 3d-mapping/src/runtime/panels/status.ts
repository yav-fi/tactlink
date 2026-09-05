import { escapeHtml, percent, type Shell } from "../shell";
import { HEALTH_STYLES, ROLE_STYLES, deriveHealth, deriveRole, shortId } from "../taxonomy";
import type { RuntimeSnapshot, RuntimeTask } from "../types";

/**
 * Mission HUD, fleet roster and objective list.
 *
 * The HUD answers "is the mission succeeding and is anything broken" in four
 * numbers and five status rows; everything more detailed lives in the
 * inspector, so the top-level surface stays readable at a glance.
 */

export type Tone = "ok" | "warn" | "crit" | "info" | "idle";

export const toneFor = (value: number, warn = 0.55, crit = 0.3): Tone =>
  value >= warn ? "ok" : value >= crit ? "warn" : "crit";

const TONE_COLOR: Record<Tone, string> = {
  ok: "#3ddc97", warn: "#ffb454", crit: "#ff6b6b", info: "#63a9ff", idle: "#6c8098",
};

export type MissionState = { label: string; tone: Tone };

export function missionState(snapshot: RuntimeSnapshot): MissionState {
  if (!snapshot.running) return { label: "RUNTIME PAUSED", tone: "idle" };
  const active = snapshot.missions.filter((task) => task.status !== "COMPLETED" && task.status !== "CANCELLED");
  if (!active.length) return { label: "STANDBY", tone: "idle" };
  const lost = snapshot.drones.filter((drone) => !drone.truth.online).length;
  const partitioned = snapshot.network.connected_components.length > 1;
  if (snapshot.mission_effectiveness < 0.35 || snapshot.network.largest_component_fraction < 0.6) {
    return { label: "MISSION CRITICAL", tone: "crit" };
  }
  if (lost > 0 || partitioned || !snapshot.control_available || snapshot.mission_effectiveness < 0.6) {
    return { label: "MISSION DEGRADED", tone: "warn" };
  }
  return { label: "MISSION ACTIVE", tone: "ok" };
}

/** Cell-weighted coverage across every published region. */
export function overallCoverage(snapshot: RuntimeSnapshot): { coverage: number; fresh: number; confidence: number } {
  const regions = snapshot.world_knowledge.regions;
  const cells = regions.reduce((total, region) => total + region.total_cells, 0);
  if (!cells) return { coverage: 0, fresh: 0, confidence: 0 };
  const weighted = (pick: (region: (typeof regions)[number]) => number) =>
    regions.reduce((total, region) => total + pick(region) * region.total_cells, 0) / cells;
  return {
    coverage: weighted((region) => region.coverage),
    fresh: weighted((region) => region.fresh_coverage),
    confidence: weighted((region) => region.mean_confidence),
  };
}

const metricTile = (label: string, value: number, tone: Tone): string => `
  <div class="mc-metric">
    <dt>${escapeHtml(label)}</dt>
    <dd style="color:${TONE_COLOR[tone]}">${percent(value)}</dd>
    <div class="mc-bar"><span style="width:${Math.round(Math.min(1, Math.max(0, value)) * 100)}%;background:${TONE_COLOR[tone]}"></span></div>
  </div>`;

const statusRow = (label: string, value: string, tone: Tone): string => `
  <div class="mc-status-row" data-tone="${tone}">
    <b>${escapeHtml(label)}</b><span style="color:${TONE_COLOR[tone]}">${escapeHtml(value)}</span>
  </div>`;

export class StatusPanels {
  private readonly metrics: HTMLElement;
  private readonly statuses: HTMLElement;
  private readonly roster: HTMLElement;
  private readonly objectives: HTMLElement;
  private readonly fleetCount: HTMLElement;
  private readonly objectiveCount: HTMLElement;
  private readonly missionSub: HTMLElement;

  constructor(
    shell: Shell,
    private readonly onSelectDrone: (id: string) => void,
    private readonly onSelectTask: (id: string) => void,
  ) {
    this.metrics = shell.query("#mc-metrics");
    this.statuses = shell.query("#mc-statuses");
    this.roster = shell.query("#mc-roster");
    this.objectives = shell.query("#mc-objectives");
    this.fleetCount = shell.query("#mc-fleet-count");
    this.objectiveCount = shell.query("#mc-objective-count");
    this.missionSub = shell.query("#mc-mission-sub");

    this.roster.addEventListener("click", (event) => {
      const card = (event.target as HTMLElement).closest<HTMLElement>("[data-drone]");
      if (card?.dataset.drone) this.onSelectDrone(card.dataset.drone);
    });
    this.objectives.addEventListener("click", (event) => {
      const card = (event.target as HTMLElement).closest<HTMLElement>("[data-task]");
      if (card?.dataset.task) this.onSelectTask(card.dataset.task);
    });
  }

  render(snapshot: RuntimeSnapshot, taskById: Map<string, RuntimeTask>, selectedDrone: string | null, selectedTask: string | null): void {
    const coverage = overallCoverage(snapshot);
    const network = snapshot.network;

    this.metrics.innerHTML = [
      metricTile("Effectiveness", snapshot.mission_effectiveness, toneFor(snapshot.mission_effectiveness, 0.6, 0.35)),
      metricTile("Capability", snapshot.mission_capability, toneFor(snapshot.mission_capability, 0.6, 0.35)),
      metricTile("Coverage", coverage.coverage, toneFor(coverage.coverage, 0.5, 0.2)),
      metricTile("Network", network.network_health, toneFor(network.network_health, 0.6, 0.35)),
    ].join("");

    const online = snapshot.drones.filter((drone) => drone.truth.online).length;
    const degraded = snapshot.drones.filter((drone) => deriveHealth(drone) === "DEGRADED").length;
    const partitions = network.connected_components.length;
    const relays = network.relay_nodes.map(shortId).join(" ");

    this.statuses.innerHTML = [
      statusRow("Nodes", `${online} / ${snapshot.drones.length}`, online === snapshot.drones.length ? "ok" : online === 0 ? "crit" : "warn"),
      statusRow("Control", snapshot.control_available ? "ONLINE" : "OFFLINE · PEER-TO-PEER", snapshot.control_available ? "ok" : "crit"),
      statusRow(
        "Localization",
        network.gps_degraded_count === 0 ? "NOMINAL" : `DEGRADED ON ${network.gps_degraded_count}`,
        network.gps_degraded_count === 0 ? "ok" : "warn",
      ),
      statusRow("Network", partitions > 1 ? `PARTITIONED · ${partitions} GROUPS` : degraded ? "STRAINED" : "CONNECTED", partitions > 1 ? "crit" : degraded ? "warn" : "ok"),
      statusRow("Relay", relays || "NONE TASKED", relays ? "info" : "idle"),
    ].join("");

    const active = snapshot.missions.filter((task) => task.status !== "COMPLETED" && task.status !== "CANCELLED");
    this.missionSub.textContent = active.length ? `${active.length} active` : "";
    this.fleetCount.textContent = `${online}/${snapshot.drones.length}`;
    this.objectiveCount.textContent = String(active.length);

    this.renderRoster(snapshot, taskById, selectedDrone);
    this.renderObjectives(active, selectedTask);
  }

  private renderRoster(snapshot: RuntimeSnapshot, taskById: Map<string, RuntimeTask>, selected: string | null): void {
    if (!snapshot.drones.length) {
      this.roster.innerHTML = `<p class="mc-empty">No nodes reported by the runtime.</p>`;
      return;
    }
    const sorted = [...snapshot.drones].sort((a, b) => {
      const severity = HEALTH_STYLES[deriveHealth(b)].severity - HEALTH_STYLES[deriveHealth(a)].severity;
      return severity || a.identity.node_id.localeCompare(b.identity.node_id);
    });

    this.roster.innerHTML = sorted.map((drone) => {
      const id = drone.identity.node_id;
      const role = ROLE_STYLES[deriveRole(drone, taskById)];
      const health = HEALTH_STYLES[deriveHealth(drone)];
      const offline = !drone.truth.online;
      const speed = Math.hypot(drone.truth.velocity.x, drone.truth.velocity.y);
      const battery = drone.truth.actual_battery;
      const task = drone.current_task_id ? taskById.get(drone.current_task_id) : undefined;
      const detail = offline
        ? "No contact · last known position marked"
        : task
          ? `${task.type} · ${percent(drone.task_progress)} done`
          : role.description;
      return `
        <button type="button" class="mc-card${id === selected ? " is-selected" : ""}${offline ? " is-offline" : ""}" data-drone="${escapeHtml(id)}" aria-pressed="${id === selected}">
          <span class="mc-swatch" style="background:${offline ? health.color : role.color}"></span>
          <span class="mc-card-main">
            <span class="mc-card-role" style="color:${offline ? health.color : role.color}">${escapeHtml(shortId(id))} · ${escapeHtml(role.badge)}${health.key === "NOMINAL" ? "" : ` · ${escapeHtml(health.label.toUpperCase())}`}</span>
            <span class="mc-card-sub">${escapeHtml(detail)}</span>
          </span>
          <span class="mc-card-right">${offline ? "—" : `${speed.toFixed(1)} m/s`}<br>${offline ? "OFFLINE" : `${Math.round(battery * 100)}% BATT`}</span>
        </button>`;
    }).join("");
  }

  private renderObjectives(tasks: RuntimeTask[], selected: string | null): void {
    if (!tasks.length) {
      this.objectives.innerHTML = `<p class="mc-empty">No active objectives. Use the Command tab to task the fleet.</p>`;
      return;
    }
    this.objectives.innerHTML = [...tasks].sort((a, b) => b.priority - a.priority).map((task) => {
      const role = ROLE_STYLES[task.type as keyof typeof ROLE_STYLES] ?? ROLE_STYLES.GOTO;
      const tone = toneFor(task.effectiveness, 0.6, 0.35);
      const holders = task.assigned_nodes.map(shortId).join(" ") || "AWAITING AUCTION";
      return `
        <button type="button" class="mc-card${task.id === selected ? " is-selected" : ""}" data-task="${escapeHtml(task.id)}" aria-pressed="${task.id === selected}">
          <span class="mc-swatch" style="background:${role.color}"></span>
          <span class="mc-card-main">
            <span class="mc-card-role" style="color:${role.color}">${escapeHtml(task.type)} · P${task.priority}${task.status === "DEGRADED" ? " · DEGRADED" : ""}</span>
            <span class="mc-card-sub">${escapeHtml(task.target.region_id ?? "point target")} · ${escapeHtml(holders)}</span>
          </span>
          <span class="mc-card-right" style="color:${TONE_COLOR[tone]}">${percent(task.effectiveness)}<br>EFF</span>
        </button>`;
    }).join("");
  }
}
