import type { RuntimeEvent } from "./types";

/**
 * Turns the backend event stream into an operator story.
 *
 * Nothing here invents narrative: every headline, tone and reason is read out
 * of the event the runtime already emitted. The only editorial decision is
 * which tier an event belongs to, which controls default visibility.
 */

export type EventTier = "headline" | "notable" | "routine" | "noise";
export type EventTone = "critical" | "warning" | "positive" | "info";

export type EventStyle = { tier: EventTier; tone: EventTone; title: string };

const STYLES: Record<string, EventStyle> = {
  SIMULATION_STARTED: { tier: "headline", tone: "info", title: "Mission started" },
  SIMULATION_RESET: { tier: "headline", tone: "info", title: "Runtime reset" },
  SIMULATION_PAUSED: { tier: "notable", tone: "info", title: "Runtime paused" },
  SIMULATION_RESUMED: { tier: "notable", tone: "info", title: "Runtime resumed" },

  NODE_FAILED: { tier: "headline", tone: "critical", title: "Drone lost" },
  NODE_RECOVERED: { tier: "headline", tone: "positive", title: "Drone recovered" },
  NODE_STARTED: { tier: "routine", tone: "info", title: "Node online" },
  HEARTBEAT_TIMEOUT: { tier: "notable", tone: "warning", title: "Heartbeat timeout" },

  CONTROL_LOST: { tier: "headline", tone: "critical", title: "Control offline" },
  CONTROL_RECOVERED: { tier: "headline", tone: "positive", title: "Control restored" },
  MISSION_REPLICATED: { tier: "notable", tone: "info", title: "Mission replicated to peers" },

  NETWORK_PARTITION: { tier: "headline", tone: "critical", title: "Network partitioned" },
  NETWORK_PARTITION_RISK: { tier: "headline", tone: "warning", title: "Partition risk" },
  NETWORK_RECONNECTED: { tier: "headline", tone: "positive", title: "Network reconnected" },
  NETWORK_HEALED: { tier: "headline", tone: "positive", title: "Network healed" },
  LINK_DEGRADED: { tier: "notable", tone: "warning", title: "Link degrading" },

  RELAY_TASK_CREATED: { tier: "headline", tone: "info", title: "Relay tasked" },
  RELAY_REPOSITIONING: { tier: "headline", tone: "info", title: "Relay repositioning" },
  RELAY_ESTABLISHED: { tier: "headline", tone: "positive", title: "Relay established" },

  GPS_LOST: { tier: "headline", tone: "warning", title: "GPS lost" },
  GPS_DEGRADED: { tier: "notable", tone: "warning", title: "GPS degraded" },
  GPS_RECOVERED: { tier: "notable", tone: "positive", title: "GPS recovered" },

  TASK_CREATED: { tier: "notable", tone: "info", title: "Objective created" },
  TASK_ASSIGNED: { tier: "notable", tone: "info", title: "Task assigned" },
  TASK_REASSIGNED: { tier: "headline", tone: "warning", title: "Task reassigned" },
  TASK_COMPLETED: { tier: "headline", tone: "positive", title: "Objective complete" },
  TASK_AUCTION_STARTED: { tier: "routine", tone: "info", title: "Auction opened" },
  TASK_AUCTION_WON: { tier: "notable", tone: "info", title: "Auction resolved" },
  TASK_RELEASED: { tier: "notable", tone: "warning", title: "Task released" },
  TASK_BID: { tier: "noise", tone: "info", title: "Bid" },
  REPLAN_REQUESTED: { tier: "notable", tone: "info", title: "Replan requested" },
  RESOURCE_REPRIORITIZED: { tier: "headline", tone: "warning", title: "Resources reprioritized" },

  OBJECTIVE_DEGRADED: { tier: "headline", tone: "warning", title: "Objective degraded" },
  OBJECTIVE_RECOVERED: { tier: "headline", tone: "positive", title: "Objective recovered" },
  MISSION_EFFECTIVENESS_CHANGED: { tier: "notable", tone: "info", title: "Effectiveness changed" },
  MISSION_CAPABILITY_CHANGED: { tier: "routine", tone: "info", title: "Capability changed" },

  WORLD_MODEL_RECONCILED: { tier: "headline", tone: "positive", title: "World model reconciled" },
  WORLD_MODEL_UPDATED: { tier: "noise", tone: "info", title: "World model updated" },
  OBSERVATION_CREATED: { tier: "noise", tone: "info", title: "Observation" },
  OBSERVATION_SHARED: { tier: "noise", tone: "info", title: "Observation shared" },
  COVERAGE_CHANGED: { tier: "routine", tone: "info", title: "Coverage changed" },
  INFORMATION_STALE: { tier: "notable", tone: "warning", title: "Information stale" },

  MESSAGE_DROPPED: { tier: "noise", tone: "info", title: "Message dropped" },
  LINK_QUALITY_CHANGED: { tier: "noise", tone: "info", title: "Link quality changed" },
  LEASE_GRANTED: { tier: "noise", tone: "info", title: "Lease granted" },
  LEASE_EXPIRED: { tier: "routine", tone: "warning", title: "Lease expired" },
  LEASE_CONFLICT_RESOLVED: { tier: "notable", tone: "info", title: "Lease conflict resolved" },

  INTERFERENCE_CHANGED: { tier: "notable", tone: "warning", title: "Interference changed" },
  RANDOM_EVENT: { tier: "routine", tone: "info", title: "Scenario event" },
  SIGNATURE_REJECTED: { tier: "headline", tone: "critical", title: "Signature rejected" },
  WORKER_CONNECTED: { tier: "notable", tone: "info", title: "Worker connected" },
  WORKER_DISCONNECTED: { tier: "notable", tone: "warning", title: "Worker disconnected" },
  COMMUNICATION_BELIEF_UPDATED: { tier: "routine", tone: "info", title: "Comm terrain learned" },
  COMMUNICATION_AWARE_ROUTE: { tier: "notable", tone: "info", title: "Connectivity-aware route" },
  PREDICTION_EMITTED: { tier: "notable", tone: "warning", title: "Risk predicted" },
  PREEMPTIVE_HANDOFF: { tier: "headline", tone: "warning", title: "Preemptive handoff" },
  PREEMPTIVE_RELAY: { tier: "headline", tone: "warning", title: "Preemptive relay" },
  COUNTERFACTUAL_EVALUATED: { tier: "notable", tone: "info", title: "Options forecast" },
  RELAY_PLAN_UPDATED: { tier: "headline", tone: "info", title: "Relay plan updated" },
  WORK_UNIT_CREATED: { tier: "routine", tone: "info", title: "Work unit created" },
  WORK_UNIT_ASSIGNED: { tier: "notable", tone: "info", title: "Compute assigned" },
  WORK_UNIT_COMPLETED: { tier: "notable", tone: "positive", title: "Compute completed" },
  WORK_UNIT_REASSIGNED: { tier: "headline", tone: "warning", title: "Compute reassigned" },
  EDGE_NODE_JOINED: { tier: "headline", tone: "positive", title: "Edge node joined" },
  EDGE_NODE_LEFT: { tier: "headline", tone: "warning", title: "Edge node left" },
};

const FALLBACK: EventStyle = { tier: "routine", tone: "info", title: "Runtime event" };

export function styleFor(eventType: string): EventStyle {
  return STYLES[eventType] ?? { ...FALLBACK, title: eventType.replaceAll("_", " ").toLowerCase() };
}

const TIER_RANK: Record<EventTier, number> = { headline: 0, notable: 1, routine: 2, noise: 3 };

export type EventFilter = "headline" | "notable" | "all";

export function passesFilter(eventType: string, filter: EventFilter): boolean {
  const rank = TIER_RANK[styleFor(eventType).tier];
  if (filter === "headline") return rank <= 0;
  if (filter === "notable") return rank <= 1;
  return rank <= 2;
}

const asNumber = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;

const percent = (value: number) => `${Math.round(value * 100)}%`;

/**
 * Builds the "why" line from fields the runtime already publishes.
 *
 * Returns null when the event carries no causal payload, rather than guessing.
 */
export function explainEvent(event: RuntimeEvent): string | null {
  const payload = event.payload ?? {};
  switch (event.event_type) {
    case "RELAY_TASK_CREATED": {
      const health = asNumber(payload.baseline_network_health);
      const components = Array.isArray(payload.components) ? payload.components.length : null;
      const parts = ["Projected partition threatened higher-priority objectives"];
      if (components && components > 1) parts.push(`${components} isolated groups`);
      if (health !== null) parts.push(`network at ${percent(health)}`);
      return parts.join(" · ");
    }
    case "RESOURCE_REPRIORITIZED": {
      const priority = asNumber(payload.deferred_priority);
      const preserving = event.affected_entities.slice(1).join(", ");
      return `Deferred${priority === null ? "" : ` priority ${priority}`} to preserve ${preserving || "higher-priority objectives"}`;
    }
    case "OBJECTIVE_DEGRADED": {
      const components = payload.components as Record<string, number> | undefined;
      if (!components) return null;
      const worst = Object.entries(components).sort((a, b) => a[1] - b[1])[0];
      return worst ? `Weakest component: ${worst[0]} at ${percent(worst[1])}` : null;
    }
    case "MISSION_EFFECTIVENESS_CHANGED": {
      const previous = asNumber(payload.previous);
      const current = asNumber(payload.current);
      if (previous === null || current === null) return null;
      return `${percent(previous)} → ${percent(current)}`;
    }
    case "NETWORK_PARTITION_RISK":
    case "NETWORK_HEALED": {
      const health = asNumber(payload.network_health);
      return health === null ? null : `Network health ${percent(health)}`;
    }
    case "TASK_REASSIGNED":
    case "TASK_ASSIGNED": {
      const previous = payload.previous ?? payload.previous_assignment;
      if (Array.isArray(previous) && previous.length) return `Previously held by ${previous.join(", ")}`;
      return null;
    }
    case "TASK_AUCTION_WON": {
      const bids = Array.isArray(payload.bids) ? payload.bids.length : null;
      const round = asNumber(payload.round);
      if (bids === null) return null;
      return `${bids} bid${bids === 1 ? "" : "s"}${round === null ? "" : ` in round ${round}`}`;
    }
    case "MESSAGE_DROPPED":
      return typeof payload.reason === "string" ? String(payload.reason) : null;
    case "COMMUNICATION_AWARE_ROUTE":
      return typeof payload.explanation === "string" ? payload.explanation : null;
    case "PREDICTION_EMITTED":
    case "PREEMPTIVE_HANDOFF":
    case "PREEMPTIVE_RELAY": {
      const prediction = payload.prediction as Record<string, unknown> | undefined;
      return typeof prediction?.explanation === "string"
        ? prediction.explanation
        : typeof payload.explanation === "string" ? payload.explanation : null;
    }
    case "COUNTERFACTUAL_EVALUATED": {
      const selected = payload.selected;
      const margin = asNumber(payload.margin);
      return selected ? `Selected ${String(selected)}${margin === null ? "" : ` · margin ${margin.toFixed(2)}`}` : null;
    }
    case "WORK_UNIT_ASSIGNED":
    case "WORK_UNIT_COMPLETED":
    case "WORK_UNIT_REASSIGNED":
      return typeof payload.executed_by === "string"
        ? `Executed by ${payload.executed_by}`
        : typeof payload.assigned_to === "string" ? `Assigned to ${payload.assigned_to}` : null;
    default:
      return null;
  }
}

/** Ids in `affected_entities` that name a drone, used for click-to-focus. */
export function focusTargets(event: RuntimeEvent): { drones: string[]; tasks: string[] } {
  const drones: string[] = [];
  const tasks: string[] = [];
  for (const entity of event.affected_entities ?? []) {
    if (entity.startsWith("drone-")) drones.push(entity);
    else if (entity.startsWith("task-")) tasks.push(entity);
  }
  if (event.source.startsWith("drone-") && !drones.includes(event.source)) drones.push(event.source);
  return { drones, tasks };
}
