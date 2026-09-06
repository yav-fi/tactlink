export const GESTURE_HOLD_MS = 400;
export const GESTURE_RELEASE_MS = 150;
export const GESTURE_DROPOUT_GRACE_MS = 180;

const ACTIONS: Readonly<Record<string, string>> = {
  Thumb_Up: "takeoff",
  Thumb_Down: "descend",
  Pointing_Up: "orbit",
  ILoveYou: "return_home",
  Open_Palm: "halt",
  Closed_Fist: "rotate_heading",
};

export type GestureHoldState = {
  progress: number;
  action?: string;
};

/** Turn frame-by-frame classifications into one deliberate action per hold. */
export class GestureHoldInterpreter {
  private held = "None";
  private heldSince = 0;
  private lastSeen = 0;
  private fired = "";
  private restSince: number | undefined;

  update(gesture: string, nowMs: number): GestureHoldState {
    if (gesture === "None" && this.held !== "None" && nowMs - this.lastSeen <= GESTURE_DROPOUT_GRACE_MS) {
      return { progress: Math.min(1, Math.max(0, nowMs - this.heldSince) / GESTURE_HOLD_MS) };
    }
    if (gesture !== this.held) {
      this.held = gesture;
      this.heldSince = nowMs;
    }

    if (gesture === "None") {
      this.restSince ??= nowMs;
      if (nowMs - this.restSince >= GESTURE_RELEASE_MS) this.fired = "";
      return { progress: 0 };
    }
    this.restSince = undefined;
    this.lastSeen = nowMs;

    const action = ACTIONS[gesture];
    if (!action || this.fired === gesture) return { progress: 0 };
    const heldFor = Math.max(0, nowMs - this.heldSince);
    if (heldFor < GESTURE_HOLD_MS) return { progress: heldFor / GESTURE_HOLD_MS };
    this.fired = gesture;
    return { progress: 1, action };
  }
}
