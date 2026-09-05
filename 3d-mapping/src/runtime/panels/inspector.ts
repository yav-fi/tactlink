import { escapeHtml, percent, type Shell } from "../shell";
import { HEALTH_STYLES, ROLE_STYLES, deriveHealth, deriveRole, gradeLink, shortId } from "../taxonomy";
import { toneFor, type Tone } from "./status";
import type { RuntimeDrone, RuntimeSnapshot, RuntimeTask } from "../types";

/**
 * Selected-entity inspector.
 *
 * The first block answers the operator's questions; the technical block is
 * collapsed so debugging fields never compete with the mission read-out.
 */

type Row = [label: string, value: string, tone?: Tone];

const rows = (items: Row[]): string =>
  `<dl class="mc-kv">${items.map(([label, value, tone]) =>
    `<dt>${escapeHtml(label)}</dt><dd${tone ? ` data-tone="${tone}"` : ""}>${escapeHtml(value)}</dd>`,
  ).join("")}</dl>`;

const GPS_TONE: Record<string, Tone> = {
  GPS: "ok", DEGRADED_GPS: "warn", DEAD_RECKONING: "crit", UNKNOWN: "crit",
};

export class Inspector {
  private readonly host: HTMLElement;

  constructor(
    shell: Shell,
    private readonly onFollow: (id: string) => void,
    private readonly onFocusTask: (id: string) => void,
  ) {
    this.host = shell.query("#mc-inspect");
    this.host.addEventListener("click", (event) => {
      const target = (event.target as HTMLElement).closest<HTMLElement>("[data-action]");
      if (!target) return;
      if (target.dataset.action === "follow" && target.dataset.drone) this.onFollow(target.dataset.drone);
      if (target.dataset.action === "focus-task" && target.dataset.task) this.onFocusTask(target.dataset.task);
    });
  }

  renderEmpty(): void {
    this.host.innerHTML = `
      <h2>Inspector</h2>
      <p class="mc-empty">Select a drone on the map or in the fleet list to inspect its task, link budget and localization.</p>`;
  }

  renderTask(task: RuntimeTask, snapshot: RuntimeSnapshot): void {
    const role = ROLE_STYLES[task.type as keyof typeof ROLE_STYLES] ?? ROLE_STYLES.GOTO;
    const region = task.target.region_id
      ? snapshot.world_knowledge.regions.find((item) => item.region_id === task.target.region_id)
      : undefined;

    const components = Object.entries(task.effectiveness_components)
      .sort((a, b) => a[1] - b[1])
      .map(([name, value]): Row => [name.replaceAll("_", " "), percent(value), toneFor(value, 0.6, 0.35)]);

    this.host.innerHTML = `
      <h2>Objective <span style="color:${role.color}">${escapeHtml(task.type)}</span></h2>
      ${rows([
        ["Status", task.status, task.status === "DEGRADED" ? "warn" : "ok"],
        ["Effectiveness", percent(task.effectiveness), toneFor(task.effectiveness, 0.6, 0.35)],
        ["Capability", percent(task.capability), toneFor(task.capability, 0.6, 0.35)],
        ["Progress", percent(task.progress)],
        ["Priority", `P${task.priority}`],
        ["Assigned", task.assigned_nodes.map(shortId).join(", ") || "awaiting auction"],
        ["Units", `${task.assigned_nodes.length} of ${task.desired_units} desired · ${task.minimum_units} minimum`],
        ["Region", task.target.region_id ?? "point target"],
        ["Requires", task.required_capabilities.join(", ") || "none"],
      ])}
      ${region ? `<h3>Region ${escapeHtml(region.region_id)}</h3>${rows([
        ["Coverage", percent(region.coverage), toneFor(region.coverage, 0.5, 0.2)],
        ["Fresh coverage", percent(region.fresh_coverage), toneFor(region.fresh_coverage, 0.4, 0.15)],
        ["Mean confidence", percent(region.mean_confidence), toneFor(region.mean_confidence, 0.5, 0.25)],
        ["Cells", `${region.observed_cells} observed of ${region.total_cells}`],
      ])}` : ""}
      ${components.length ? `<h3>Effectiveness components</h3>${rows(components)}` : ""}
      <div class="mc-actions mc-actions-wide" style="margin-top:12px">
        <button type="button" data-action="focus-task" data-task="${escapeHtml(task.id)}">Focus this objective</button>
      </div>
      <p class="mc-note">Task id <strong>${escapeHtml(task.id)}</strong></p>`;
  }

  renderDrone(drone: RuntimeDrone, snapshot: RuntimeSnapshot, taskById: Map<string, RuntimeTask>): void {
    const id = drone.identity.node_id;
    const role = ROLE_STYLES[deriveRole(drone, taskById)];
    const health = HEALTH_STYLES[deriveHealth(drone)];
    const task = drone.current_task_id ? taskById.get(drone.current_task_id) : undefined;
    const peers = Object.values(drone.peers);
    const reachable = peers.filter((peer) => peer.available).length;
    const links = snapshot.links.filter((link) => link.source_id === id || link.target_id === id);
    const speed = Math.hypot(drone.truth.velocity.x, drone.truth.velocity.y);
    const climb = drone.truth.velocity.z;
    const offline = !drone.truth.online;

    const linkRows = links.map((link): Row => {
      const other = link.source_id === id ? link.target_id : link.source_id;
      const grade = gradeLink(link.available, link.quality);
      return [
        shortId(other),
        `${percent(link.quality)} · ${Math.round(link.distance_m)} m · ${grade}${link.obstructed ? " · LOS BLOCKED" : ""}`,
        grade === "STRONG" || grade === "USABLE" ? "ok" : grade === "DOWN" ? "crit" : "warn",
      ];
    });

    this.host.innerHTML = `
      <h2>${escapeHtml(shortId(id))} <span style="color:${offline ? health.color : role.color}">${escapeHtml(offline ? health.label.toUpperCase() : role.label)}</span></h2>
      ${offline ? `<div class="mc-warning-block">No contact since this node went offline. Its last confirmed position is marked on the map.</div>` : ""}
      ${rows([
        ["Role", `${role.label} (${role.badge})`],
        ["State", drone.state, offline ? "crit" : drone.state === "DEGRADED" ? "warn" : "ok"],
        ["Task", task ? `${task.type} · ${task.id}` : "none assigned"],
        ["Task progress", task ? percent(drone.task_progress) : "—"],
        ["Task effectiveness", task ? percent(task.effectiveness) : "—", task ? toneFor(task.effectiveness, 0.6, 0.35) : undefined],
        ["Battery", percent(drone.truth.actual_battery), toneFor(drone.truth.actual_battery, 0.4, 0.15)],
        ["Ground speed", `${speed.toFixed(1)} m/s`],
        ["Climb rate", `${climb >= 0 ? "+" : ""}${climb.toFixed(1)} m/s`],
        ["Altitude", `${drone.truth.position.z.toFixed(1)} m above origin plane`],
        ["Localization", drone.estimated.localization_mode, GPS_TONE[drone.estimated.localization_mode] ?? "warn"],
        ["Position error", `${drone.estimated.position_uncertainty.toFixed(1)} m`, drone.estimated.position_uncertainty > 6 ? "crit" : drone.estimated.position_uncertainty > 3 ? "warn" : "ok"],
        ["Sensor confidence", percent(drone.estimated.sensor_confidence), toneFor(drone.estimated.sensor_confidence, 0.7, 0.4)],
        ["Peers reachable", `${reachable} of ${peers.length}`, reachable === peers.length ? "ok" : reachable === 0 ? "crit" : "warn"],
        ["Capabilities", drone.identity.capabilities.join(", ") || "none"],
        ["Observations", String(drone.observation_count)],
        ["Known cells", Object.entries(drone.known_cells).map(([region, count]) => `${region} ${count}`).join(" · ") || "none"],
      ])}
      ${linkRows.length ? `<h3>Link budget</h3>${rows(linkRows)}` : ""}
      <div class="mc-actions" style="margin-top:12px">
        <button type="button" data-action="follow" data-drone="${escapeHtml(id)}">Follow this drone</button>
        ${task ? `<button type="button" data-action="focus-task" data-task="${escapeHtml(task.id)}">Focus its objective</button>` : ""}
      </div>
      <details style="margin-top:12px">
        <summary style="cursor:pointer;font:600 10px/1 system-ui,sans-serif;letter-spacing:.14em;text-transform:uppercase;color:#6c8098">Technical detail</summary>
        <div style="margin-top:10px">
          ${rows([
            ["Node id", id],
            ["Name", drone.identity.name],
            ["Trust", drone.identity.trust_level],
            ["Authorization", drone.identity.authorization_level],
            ["Airframe", String(drone.identity.metadata.airframe ?? "unknown")],
            ["Relay capable", drone.identity.resources.relay ? "yes" : "no"],
            ["Compute score", drone.identity.resources.compute_score.toFixed(2)],
            ["Sensors", drone.identity.resources.sensors.join(", ") || "none"],
            ["Truth xyz", `${drone.truth.position.x.toFixed(1)}, ${drone.truth.position.y.toFixed(1)}, ${drone.truth.position.z.toFixed(1)}`],
            ["Estimated xyz", `${drone.estimated.position.x.toFixed(1)}, ${drone.estimated.position.y.toFixed(1)}, ${drone.estimated.position.z.toFixed(1)}`],
            ["Backend heading", `${(drone.truth.heading * 180 / Math.PI).toFixed(1)}° ccw from east`],
            ["Task queue", drone.task_queue.join(", ") || "empty"],
            ["Mission revision", String(drone.local_mission_revision)],
            ["World reconciled", drone.last_world_reconciliation === null ? "never" : `T+${drone.last_world_reconciliation.toFixed(1)} s`],
            ["Policy", drone.policy.allowed ? "allowed" : `blocked: ${drone.policy.reason_codes.join(", ")}`, drone.policy.allowed ? "ok" : "crit"],
            ["Policy warnings", drone.policy.warnings.join(", ") || "none"],
          ])}
        </div>
      </details>`;
  }
}
