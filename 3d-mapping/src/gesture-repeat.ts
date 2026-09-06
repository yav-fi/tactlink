const RELEASE_GRACE_MS = 220;

const repeatCadence = (action: string): number | undefined => {
  if (action === "rotate_heading") return 1_800;
  if (action === "takeoff" || action === "descend") return 750;
  if (action.startsWith("fly_")) return 700;
};

export function isMomentaryGestureAction(action: string): boolean {
  return action === "takeoff" || action === "descend" || action === "orbit" || action === "rotate_heading" || action.startsWith("fly_");
}

/** Keeps held flight gestures active and emits one stop when their pose leaves. */
export class GestureCommandRepeater {
  private active?: { action: string; source: string; lastFire: number; lostSince?: number };

  start(action: string, source: string, nowMs: number): void {
    if (!isMomentaryGestureAction(action)) {
      this.active = undefined;
      return;
    }
    this.active = { action, source, lastFire: nowMs };
  }

  update(source: string | undefined, nowMs: number): string[] {
    if (!this.active) return [];
    if (source !== this.active.source) {
      this.active.lostSince ??= nowMs;
      if (nowMs - this.active.lostSince < RELEASE_GRACE_MS) return [];
      this.active = undefined;
      return ["gesture_stop"];
    }
    this.active.lostSince = undefined;
    const cadence = repeatCadence(this.active.action);
    if (cadence === undefined || nowMs - this.active.lastFire < cadence) return [];
    this.active.lastFire = nowMs;
    return [this.active.action];
  }
}
