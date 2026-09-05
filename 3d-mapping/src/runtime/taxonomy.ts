import type { RuntimeDrone, RuntimeTask } from "./types";

/**
 * Role and health taxonomy shared by every surface.
 *
 * Role (what a drone is doing) and health (how well it is doing it) are kept as
 * two orthogonal channels so a degraded relay reads as both, and every channel
 * carries a glyph and a text badge as well as a colour.
 */

export type RoleKey =
  | "SEARCH" | "WATCH" | "RELAY" | "RETURN" | "FOLLOW"
  | "HOLD" | "TRACE" | "GOTO" | "REGROUP" | "IDLE";

export type HealthKey = "NOMINAL" | "DEGRADED" | "LOST" | "OFFLINE";

/** Icon glyphs are distinct silhouettes, so role survives greyscale printing. */
export type RoleGlyph = "sweep" | "eye" | "broadcast" | "home" | "chase" | "hold" | "trace" | "arrow" | "rally" | "idle";

export type RoleStyle = {
  key: RoleKey;
  label: string;
  badge: string;
  glyph: RoleGlyph;
  color: string;
  /** Dash pattern (Cesium 16-bit mask) for this role's planned route. */
  dash: number;
  description: string;
};

export type HealthStyle = {
  key: HealthKey;
  label: string;
  color: string;
  /** Rank used to sort the roster and to decide alarm precedence. */
  severity: number;
};

export const ROLE_STYLES: Record<RoleKey, RoleStyle> = {
  SEARCH: { key: "SEARCH", label: "Search", badge: "SRCH", glyph: "sweep", color: "#ffb454", dash: 0xffff, description: "Sweeping a region to build coverage" },
  WATCH: { key: "WATCH", label: "Watch", badge: "WTCH", glyph: "eye", color: "#3ddc97", dash: 0xffff, description: "Holding overwatch on a region" },
  RELAY: { key: "RELAY", label: "Relay", badge: "RLAY", glyph: "broadcast", color: "#b78bff", dash: 0xf0f0, description: "Positioning to carry the network" },
  RETURN: { key: "RETURN", label: "Return", badge: "RTB", glyph: "home", color: "#63a9ff", dash: 0xcccc, description: "Returning to its launch point" },
  FOLLOW: { key: "FOLLOW", label: "Follow", badge: "FOLW", glyph: "chase", color: "#2fd4e8", dash: 0xff00, description: "Tracking a moving entity" },
  HOLD: { key: "HOLD", label: "Hold", badge: "HOLD", glyph: "hold", color: "#93a7bd", dash: 0xf000, description: "Station keeping" },
  TRACE: { key: "TRACE", label: "Trace", badge: "TRCE", glyph: "trace", color: "#4fd1c5", dash: 0xff00, description: "Flying an ordered waypoint trace" },
  GOTO: { key: "GOTO", label: "Transit", badge: "GOTO", glyph: "arrow", color: "#63a9ff", dash: 0xffff, description: "Transiting to a point" },
  REGROUP: { key: "REGROUP", label: "Regroup", badge: "RGRP", glyph: "rally", color: "#c58bff", dash: 0xf0f0, description: "Rejoining the formation" },
  IDLE: { key: "IDLE", label: "Idle", badge: "IDLE", glyph: "idle", color: "#7d92a8", dash: 0xf0f0, description: "Available, no task assigned" },
};

export const HEALTH_STYLES: Record<HealthKey, HealthStyle> = {
  NOMINAL: { key: "NOMINAL", label: "Nominal", color: "#3ddc97", severity: 0 },
  DEGRADED: { key: "DEGRADED", label: "Degraded", color: "#ffb454", severity: 1 },
  LOST: { key: "LOST", label: "Lost", color: "#ff6b6b", severity: 3 },
  OFFLINE: { key: "OFFLINE", label: "Offline", color: "#ff6b6b", severity: 4 },
};

const TASK_ROLE: Record<string, RoleKey> = {
  SEARCH: "SEARCH", WATCH: "WATCH", RELAY: "RELAY", RETURN: "RETURN", FOLLOW: "FOLLOW",
  HOLD: "HOLD", TRACE: "TRACE", GOTO: "GOTO", REGROUP: "REGROUP",
};

/**
 * The job a drone is currently performing.
 *
 * A relay assignment wins over the task type because carrying the network is
 * what the operator needs to see; health is reported separately.
 */
export function deriveRole(drone: RuntimeDrone, taskById: Map<string, RuntimeTask>): RoleKey {
  if (drone.role === "RELAY") return "RELAY";
  const task = drone.current_task_id ? taskById.get(drone.current_task_id) : undefined;
  if (task && TASK_ROLE[task.type]) return TASK_ROLE[task.type];
  if (drone.state === "RETURNING") return "RETURN";
  if (drone.state === "HOLDING") return "HOLD";
  return "IDLE";
}

export function deriveHealth(drone: RuntimeDrone): HealthKey {
  if (!drone.truth.online) return "OFFLINE";
  if (drone.state === "LOST") return "LOST";
  if (drone.state === "DEGRADED" || drone.estimated.localization_mode === "DEAD_RECKONING") return "DEGRADED";
  return "NOMINAL";
}

/** "drone-2" -> "D2"; anything unexpected falls back to an upper-cased id. */
export function shortId(nodeId: string): string {
  const match = /(?:^|[^0-9])([0-9]+)$/.exec(nodeId);
  if (match && nodeId.toLowerCase().startsWith("drone")) return `D${match[1]}`;
  return nodeId.toUpperCase();
}

export type LinkGrade = "STRONG" | "USABLE" | "WEAK" | "CRITICAL" | "DOWN";

export const LINK_GRADES: Record<LinkGrade, { label: string; color: string; dash: number; width: number }> = {
  STRONG: { label: "Strong", color: "#3ddc97", dash: 0xffff, width: 3.4 },
  USABLE: { label: "Usable", color: "#8fd66a", dash: 0xffff, width: 2.4 },
  WEAK: { label: "Weak", color: "#ffb454", dash: 0xf0f0, width: 1.8 },
  CRITICAL: { label: "Critical", color: "#ff8a3d", dash: 0xcccc, width: 1.4 },
  DOWN: { label: "Down", color: "#ff6b6b", dash: 0x8888, width: 1.2 },
};

/**
 * Buckets a backend link quality into a grade.
 *
 * Thresholds follow `NetworkConfig`: 0.35 is the backend's healthy-link mark
 * and 0.08 its minimum usable quality.
 */
export function gradeLink(available: boolean, quality: number): LinkGrade {
  if (!available) return "DOWN";
  if (quality >= 0.7) return "STRONG";
  if (quality >= 0.35) return "USABLE";
  if (quality >= 0.18) return "WEAK";
  return "CRITICAL";
}
