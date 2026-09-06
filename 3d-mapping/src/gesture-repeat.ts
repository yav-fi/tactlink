// A hand that has left the frame, or one that has plainly changed pose, should
// stop the drone promptly.
const RELEASE_GRACE_MS = 220;
// A hand still in frame whose pose momentarily fails to classify is a different
// matter. MediaPipe detects a hand far more steadily than it classifies one, so
// treating an unclassified frame as a release cancels held commands constantly:
// the drone stops, and the pose then has to be re-held for the full hold time
// before it moves again. That is roughly six tenths of a second of dead air per
// dropped classification, which is what makes a held climb rise in steps.
const UNCLASSIFIED_GRACE_MS = 1_200;

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

  update(source: string | undefined, nowMs: number, handPresent = false): string[] {
    if (!this.active) return [];
    if (source !== this.active.source) {
      this.active.lostSince ??= nowMs;
      const grace = handPresent && source === undefined ? UNCLASSIFIED_GRACE_MS : RELEASE_GRACE_MS;
      if (nowMs - this.active.lostSince < grace) return [];
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
