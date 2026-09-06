import type { RuntimeOperator } from "./runtime/types";

export type PhoneSample = {
  id: string; name: string; room: string; pos: [number, number] | null; z: number;
  heading: number; compassValid: boolean; gesture: string; confidence: number;
  flat: boolean; geometryAge: number; age: number; cycle: number; members: number;
};
export type PhoneAlignment = { anchor: string; rotation: number; mirror: boolean };
export const PHONE_TIMEOUT = 1.5;

/** Preserve measured metre offsets; never fill missing geometry with a fabricated position. */
export function placePhones(phones: PhoneSample[], alignment: PhoneAlignment): RuntimeOperator[] {
  const anchor = phones.find(phone => phone.id === alignment.anchor && phone.pos && phone.age <= PHONE_TIMEOUT && phone.geometryAge <= 8);
  if (!anchor?.pos) return [];
  const rotation = alignment.rotation * Math.PI / 180;
  return phones.filter(phone => phone.room === anchor.room && phone.flat === anchor.flat && phone.pos && phone.geometryAge <= 8).map(phone => {
    const x = phone.pos![0] - anchor.pos![0];
    const y = (phone.pos![1] - anchor.pos![1]) * (alignment.mirror ? -1 : 1);
    return {
      operator_id: phone.id, name: phone.name,
      position: { x: Math.cos(rotation) * x - Math.sin(rotation) * y, y: 5 + Math.sin(rotation) * x + Math.cos(rotation) * y, z: phone.flat ? 0 : phone.z - anchor.z },
      heading: phone.compassValid ? phone.heading : (alignment.mirror ? -phone.heading : phone.heading) + rotation,
      gesture: phone.confidence >= 0.65 ? phone.gesture : "None", gesture_confidence: phone.confidence,
      gesture_source: "phone", action: null, is_anchor: phone.id === anchor.id,
      controls: [], nearest_distance: null, age: phone.age,
    };
  });
}

const ACTIONS: Record<string, string> = {
  Thumb_Up: "takeoff", Thumb_Down: "land", Open_Palm: "halt", Pointing_Up: "orbit",
  ILoveYou: "return_home", Closed_Fist: "rotate_heading",
  Three_Finger_Forward: "forward", Dash_Left: "left", Dash_Right: "right",
};

/** One owner of one browser drone. Handoffs require a fresh deliberate hold. */
export class PhoneControl {
  owner = "";
  private held = "None";
  private since = 0;
  private fired = false;
  reset(): void { this.owner = ""; this.held = "None"; this.fired = false; }

  update(operators: RuntimeOperator[], drone: { x: number; y: number }, now: number): {
    operator?: RuntimeOperator; action?: string; progress: number; stop: boolean;
  } {
    const live = operators.filter(operator => operator.age <= PHONE_TIMEOUT);
    const distance = (operator: RuntimeOperator) => Math.hypot(operator.position.x - drone.x, operator.position.y - drone.y);
    const nearest = [...live].sort((a, b) => distance(a) - distance(b) || a.operator_id.localeCompare(b.operator_id))[0];
    const previous = live.find(operator => operator.operator_id === this.owner);
    const operator = previous && nearest && distance(nearest) >= distance(previous) * 0.72 ? previous : nearest;
    const changedOwner = (operator?.operator_id ?? "") !== this.owner;
    const gesture = operator?.gesture ?? "None";
    const changedGesture = gesture !== this.held;
    if (changedOwner || changedGesture) {
      this.owner = operator?.operator_id ?? "";
      this.held = gesture; this.since = now; this.fired = false;
    }
    const action = ACTIONS[gesture];
    const progress = action ? Math.min(1, (now - this.since) / 400) : 0;
    const continuous = ["takeoff", "land", "forward", "left", "right", "orbit"].includes(action);
    const emit = action && progress >= 1 && (!this.fired || continuous);
    if (emit) this.fired = true;
    return { operator, action: emit ? action : undefined, progress, stop: changedOwner || changedGesture || !operator };
  }
}
