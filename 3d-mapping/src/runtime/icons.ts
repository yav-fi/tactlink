import { HEALTH_STYLES, ROLE_STYLES, type HealthKey, type RoleKey } from "./taxonomy";

/**
 * Canvas-drawn aircraft icons for the far-zoom billboard layer.
 *
 * Every role gets a different silhouette and every health state a different
 * overlay, so role and health both survive greyscale, colour-blind palettes and
 * a 20-pixel icon. Icons are cached: one canvas per role/health/selection.
 */

const SIZE = 72;
const cache = new Map<string, HTMLCanvasElement>();

function chevron(context: CanvasRenderingContext2D, scale: number, filled: boolean, color: string): void {
  context.beginPath();
  context.moveTo(0, -22 * scale);
  context.lineTo(15 * scale, 14 * scale);
  context.lineTo(0, 6 * scale);
  context.lineTo(-15 * scale, 14 * scale);
  context.closePath();
  if (filled) {
    context.fillStyle = color;
    context.fill();
  }
  context.lineWidth = 2.4;
  context.strokeStyle = filled ? "rgba(4,8,12,0.85)" : color;
  context.stroke();
}

function arc(context: CanvasRenderingContext2D, radius: number, from: number, to: number, color: string, width: number): void {
  context.beginPath();
  context.arc(0, 0, radius, from, to);
  context.strokeStyle = color;
  context.lineWidth = width;
  context.lineCap = "round";
  context.stroke();
}

function drawRole(context: CanvasRenderingContext2D, role: RoleKey, color: string): void {
  switch (role) {
    case "SEARCH":
      chevron(context, 1, true, color);
      arc(context, 25, Math.PI * 0.18, Math.PI * 0.82, color, 3);
      break;
    case "WATCH":
      chevron(context, 0.86, true, color);
      arc(context, 26, 0, Math.PI * 2, color, 2.6);
      context.beginPath();
      context.arc(0, -2, 4.2, 0, Math.PI * 2);
      context.fillStyle = color;
      context.fill();
      break;
    case "RELAY":
      chevron(context, 0.82, true, color);
      arc(context, 22, Math.PI * 1.15, Math.PI * 1.45, color, 3);
      arc(context, 28, Math.PI * 1.12, Math.PI * 1.48, color, 2.2);
      arc(context, 22, Math.PI * 1.55, Math.PI * 1.85, color, 3);
      arc(context, 28, Math.PI * 1.52, Math.PI * 1.88, color, 2.2);
      break;
    case "RETURN":
      chevron(context, 0.9, true, color);
      context.fillStyle = color;
      context.fillRect(-14, 18, 28, 5);
      break;
    case "FOLLOW":
      chevron(context, 0.82, true, color);
      context.beginPath();
      context.moveTo(-12, 24);
      context.lineTo(0, 12);
      context.lineTo(12, 24);
      context.strokeStyle = color;
      context.lineWidth = 3;
      context.stroke();
      break;
    case "HOLD":
      chevron(context, 0.86, true, color);
      context.fillStyle = color;
      context.fillRect(-22, -2, 12, 5);
      context.fillRect(10, -2, 12, 5);
      break;
    case "TRACE":
      chevron(context, 0.86, true, color);
      context.setLineDash([4, 4]);
      context.beginPath();
      context.moveTo(0, 16);
      context.lineTo(0, 30);
      context.strokeStyle = color;
      context.lineWidth = 3;
      context.stroke();
      context.setLineDash([]);
      break;
    case "REGROUP":
      chevron(context, 0.8, true, color);
      context.strokeStyle = color;
      context.lineWidth = 3;
      for (const side of [-1, 1]) {
        context.beginPath();
        context.moveTo(side * 26, 20);
        context.lineTo(side * 14, 8);
        context.stroke();
      }
      break;
    case "GOTO":
      chevron(context, 1, true, color);
      break;
    default:
      chevron(context, 0.9, false, color);
      break;
  }
}

function drawHealth(context: CanvasRenderingContext2D, health: HealthKey): void {
  if (health === "NOMINAL") return;
  const color = HEALTH_STYLES[health].color;
  if (health === "DEGRADED") {
    context.setLineDash([5, 5]);
    arc(context, 31, 0, Math.PI * 2, color, 3);
    context.setLineDash([]);
    return;
  }
  arc(context, 31, 0, Math.PI * 2, color, 3);
  context.strokeStyle = color;
  context.lineWidth = 4;
  context.lineCap = "round";
  context.beginPath();
  context.moveTo(-15, -15);
  context.lineTo(15, 15);
  context.moveTo(15, -15);
  context.lineTo(-15, 15);
  context.stroke();
}

/**
 * Returns a cached icon canvas. The nose points to the top of the image, which
 * the billboard's aligned axis then rotates onto the flight direction.
 */
export function aircraftIcon(role: RoleKey, health: HealthKey, selected: boolean): HTMLCanvasElement {
  const key = `${role}|${health}|${selected}`;
  const existing = cache.get(key);
  if (existing) return existing;

  const canvas = document.createElement("canvas");
  canvas.width = SIZE;
  canvas.height = SIZE;
  const context = canvas.getContext("2d")!;
  context.translate(SIZE / 2, SIZE / 2);

  // Dark contrast plate keeps the icon readable over bright photorealistic tiles.
  context.beginPath();
  context.arc(0, 0, 30, 0, Math.PI * 2);
  context.fillStyle = "rgba(6,11,17,0.55)";
  context.fill();

  if (selected) {
    arc(context, 33, 0, Math.PI * 2, "#ffffff", 3);
  }

  const dimmed = health === "OFFLINE" || health === "LOST";
  drawRole(context, role, dimmed ? "#8c9bab" : ROLE_STYLES[role].color);
  drawHealth(context, health);

  cache.set(key, canvas);
  return canvas;
}

/** Marker used for the last confirmed position of a lost drone. */
export function lastKnownIcon(): HTMLCanvasElement {
  const key = "last-known";
  const existing = cache.get(key);
  if (existing) return existing;
  const canvas = document.createElement("canvas");
  canvas.width = SIZE;
  canvas.height = SIZE;
  const context = canvas.getContext("2d")!;
  context.translate(SIZE / 2, SIZE / 2);
  context.setLineDash([6, 5]);
  arc(context, 26, 0, Math.PI * 2, "#ff6b6b", 3);
  context.setLineDash([]);
  context.strokeStyle = "#ff6b6b";
  context.lineWidth = 3;
  context.beginPath();
  context.moveTo(0, -12);
  context.lineTo(0, 12);
  context.moveTo(-12, 0);
  context.lineTo(12, 0);
  context.stroke();
  cache.set(key, canvas);
  return canvas;
}

/** Objective marker; `shape` separates task types without relying on colour. */
export function objectiveIcon(kind: "search" | "watch" | "relay" | "point", color: string): HTMLCanvasElement {
  const key = `objective|${kind}|${color}`;
  const existing = cache.get(key);
  if (existing) return existing;
  const canvas = document.createElement("canvas");
  canvas.width = SIZE;
  canvas.height = SIZE;
  const context = canvas.getContext("2d")!;
  context.translate(SIZE / 2, SIZE / 2);
  context.strokeStyle = color;
  context.fillStyle = color;
  context.lineWidth = 3.5;
  context.beginPath();
  if (kind === "search") {
    context.rect(-18, -18, 36, 36);
    context.stroke();
    context.setLineDash([5, 4]);
    context.beginPath();
    context.moveTo(-18, 0);
    context.lineTo(18, 0);
    context.moveTo(0, -18);
    context.lineTo(0, 18);
    context.stroke();
    context.setLineDash([]);
  } else if (kind === "watch") {
    context.arc(0, 0, 18, 0, Math.PI * 2);
    context.stroke();
    context.beginPath();
    context.arc(0, 0, 6, 0, Math.PI * 2);
    context.fill();
  } else if (kind === "relay") {
    context.moveTo(0, -20);
    context.lineTo(18, 12);
    context.lineTo(-18, 12);
    context.closePath();
    context.stroke();
  } else {
    context.moveTo(0, -18);
    context.lineTo(18, 0);
    context.lineTo(0, 18);
    context.lineTo(-18, 0);
    context.closePath();
    context.stroke();
  }
  cache.set(key, canvas);
  return canvas;
}
