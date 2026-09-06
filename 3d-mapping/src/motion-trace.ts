export const MOTION_TRACE_LIFETIME_MS = 2_800;

export function motionTraceAlpha(ageMs: number): number {
  const life = 1 - Math.max(0, ageMs) / MOTION_TRACE_LIFETIME_MS;
  return Math.max(0, Math.min(0.9, Math.pow(Math.max(0, life), 1.85) * 0.9));
}
