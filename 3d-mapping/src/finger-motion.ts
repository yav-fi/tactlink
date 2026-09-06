export type HandLandmark = { x: number; y: number };

export type FingerMotionInput = {
  present: boolean;
  fingers: readonly boolean[];
  pointDirection: readonly [number, number];
};

export type FingerMotionState = {
  actions: string[];
  hint: string;
  label?: string;
  progress: number;
};

const WINDOW_MS = 4_000;
const STABLE_MS = 120;
const LOST_MS = 600;
const THREE_HOLD_MS = 450;
const THREE_RELEASE_MS = 400;
const PATTERN = ["V", "H", "V", "H"] as const;
// The finger heuristics below read a projected 2D hand, so a curled finger on a
// rotating hand can register as straight for a frame or two. A label is what
// names the gesture source, and swapping the source cancels whatever command is
// being held, so a single noisy frame must not be able to do it. Shapes have to
// hold still briefly before they are allowed to speak for the hand.
const LABEL_STABLE_MS = 200;

function distance(a: HandLandmark, b: HandLandmark): number {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

/** Recreate the orientation-independent finger geometry used by the original Python tracker. */
export function deriveFingerMotionInput(landmarks: readonly HandLandmark[] | undefined): FingerMotionInput {
  if (!landmarks || landmarks.length < 21) return { present: false, fingers: [false, false, false, false, false], pointDirection: [0, 0] };
  const wrist = landmarks[0];
  const fromWrist = (index: number) => distance(landmarks[index], wrist);
  const straight = (mcp: number, pip: number, tip: number) => {
    const first = { x: landmarks[pip].x - landmarks[mcp].x, y: landmarks[pip].y - landmarks[mcp].y };
    const second = { x: landmarks[tip].x - landmarks[pip].x, y: landmarks[tip].y - landmarks[pip].y };
    const cosine = (first.x * second.x + first.y * second.y) / (Math.hypot(first.x, first.y) * Math.hypot(second.x, second.y) + 1e-6);
    return cosine > 0.35 && fromWrist(tip) > fromWrist(pip) * 1.02;
  };
  const thumb = fromWrist(4) > fromWrist(2) * 1.05
    && distance(landmarks[4], landmarks[5]) > distance(landmarks[3], landmarks[5]) * 1.1;
  const fingers = [thumb, straight(5, 6, 8), straight(9, 10, 12), straight(13, 14, 16), straight(17, 18, 20)];
  const rawX = (landmarks[8].x - landmarks[5].x) + (landmarks[12].x - landmarks[9].x);
  const rawY = (landmarks[8].y - landmarks[5].y) + (landmarks[12].y - landmarks[9].y);
  const length = Math.hypot(rawX, rawY);
  // The legacy webcam was mirrored before landmark extraction. Mirror x here
  // as well so pointing to the operator's right still means east.
  const pointDirection: [number, number] = length > 1e-6 ? [-rawX / length, rawY / length] : [0, 0];
  return { present: true, fingers, pointDirection };
}

export function resolvePointedDirection([dx, dy]: readonly [number, number]): string {
  return Math.abs(dx) >= 0.55 && Math.abs(dx) >= Math.abs(dy)
    ? dx > 0 ? "fly_east" : "fly_west"
    : "fly_north";
}

function twoFingers(input: FingerMotionInput): boolean {
  return Boolean(input.fingers[1] && input.fingers[2] && !input.fingers[3] && !input.fingers[4]);
}

function threeFingers(input: FingerMotionInput): boolean {
  return Boolean(input.fingers[1] && input.fingers[2] && input.fingers[3] && !input.fingers[4]);
}

function orientation([dx, dy]: readonly [number, number]): "V" | "H" | undefined {
  if (dx === 0 && dy === 0) return;
  const angle = Math.abs(Math.atan2(dx, -dy) * 180 / Math.PI);
  if (angle <= 35) return "V";
  if (angle >= 55 && angle <= 125) return "H";
}

class ThreeFingerForward {
  private heldSince: number | undefined;
  private fired = false;
  private awaySince: number | undefined;

  update(input: FingerMotionInput, nowMs: number): { action?: string; progress: number } {
    if (!input.present || !threeFingers(input)) {
      this.awaySince ??= nowMs;
      if (nowMs - this.awaySince >= THREE_RELEASE_MS) this.fired = false;
      this.heldSince = undefined;
      return { progress: 0 };
    }
    this.awaySince = undefined;
    this.heldSince ??= nowMs;
    const progress = this.fired ? 0 : Math.min(1, (nowMs - this.heldSince) / THREE_HOLD_MS);
    if (!this.fired && progress >= 1) {
      this.fired = true;
      return { action: "fly_forward", progress: 1 };
    }
    return { progress };
  }
}

class FingerSwingDetector {
  private confirmed: "V" | "H" | undefined;
  private settling: "V" | "H" | undefined;
  private settlingSince = 0;
  private history: { orientation: "V" | "H"; at: number }[] = [];
  private lastSeen = -Infinity;

  update(input: FingerMotionInput, nowMs: number): string | undefined {
    if (!input.present || !twoFingers(input)) {
      if (nowMs - this.lastSeen > LOST_MS) this.reset();
      return;
    }
    this.lastSeen = nowMs;
    const raw = orientation(input.pointDirection);
    if (!raw) return;
    if (raw !== this.settling) {
      this.settling = raw;
      this.settlingSince = nowMs;
    }
    if (raw === this.confirmed || nowMs - this.settlingSince < STABLE_MS) return;
    this.confirmed = raw;
    this.history.push({ orientation: raw, at: nowMs });
    this.history = this.history.filter(item => nowMs - item.at <= WINDOW_MS);
    if (this.history.length >= 4 && PATTERN.every((value, index) => this.history.at(index - 4)?.orientation === value)) {
      this.reset();
      return resolvePointedDirection(input.pointDirection);
    }
  }

  get progress(): number { return Math.min(1, this.history.length / 4); }
  get hint(): string {
    const sequence = this.history.slice(-4).map(item => item.orientation);
    return sequence.length ? `${sequence.join(">")}${sequence.length < 4 ? ">…" : ""}` : "";
  }

  private reset(): void {
    this.confirmed = undefined;
    this.settling = undefined;
    this.history = [];
  }
}

/** Restores the two landmark-driven gestures from the original Python controller. */
export class FingerMotionInterpreter {
  private readonly swing = new FingerSwingDetector();
  private readonly three = new ThreeFingerForward();
  private shape: "three" | "two" | undefined;
  private shapeSince = 0;

  /** The hand shape, but only once it has stopped flickering. */
  private steadyShape(input: FingerMotionInput, nowMs: number): "three" | "two" | undefined {
    const shape = threeFingers(input) ? "three" : twoFingers(input) ? "two" : undefined;
    if (shape !== this.shape) {
      this.shape = shape;
      this.shapeSince = nowMs;
    }
    return shape && nowMs - this.shapeSince >= LABEL_STABLE_MS ? shape : undefined;
  }

  update(input: FingerMotionInput, nowMs: number): FingerMotionState {
    const actions: string[] = [];
    const swingAction = this.swing.update(input, nowMs);
    if (swingAction) actions.push(swingAction);
    const forward = this.three.update(input, nowMs);
    if (forward.action) actions.push(forward.action);
    const shape = this.steadyShape(input, nowMs);
    if (shape === "three") {
      return { actions, hint: "Hold three-finger W to keep flying forward", label: "Three_Finger_Forward", progress: forward.progress };
    }
    if (shape === "two") {
      return { actions, hint: this.swing.hint ? `Two-finger swing ${this.swing.hint}` : "Swing two fingers vertical, then horizontal", label: "Two_Finger_Wiper", progress: this.swing.progress };
    }
    return { actions, hint: "", progress: 0 };
  }
}
