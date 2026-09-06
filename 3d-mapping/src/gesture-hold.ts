export const GESTURE_HOLD_MS = 400;
export const GESTURE_RELEASE_MS = 150;

const ACTIONS: Readonly<Record<string, string>> = {
  Thumb_Up: "takeoff",
  Thumb_Down: "land",
  Pointing_Up: "orbit",
  ILoveYou: "return_home",
  Open_Palm: "halt",
};

export type GestureHoldState = {
  progress: number;
  action?: string;
};

/** Turn frame-by-frame classifications into one deliberate action per hold. */
export class GestureHoldInterpreter {
  private held = "None";
  private heldSince = 0;
  private fired = "";
  private restSince: number | undefined;

  update(gesture: string, nowMs: number): GestureHoldState {
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

    const action = ACTIONS[gesture];
    if (!action || this.fired === gesture) return { progress: 0 };
    const heldFor = Math.max(0, nowMs - this.heldSince);
    if (heldFor < GESTURE_HOLD_MS) return { progress: heldFor / GESTURE_HOLD_MS };
    this.fired = gesture;
    return { progress: 1, action };
  }
}
