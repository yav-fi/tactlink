import type { RuntimeOperator } from "./runtime/types";

export type PhoneSample = {
  id: string; name: string; room: string; pos: [number, number] | null; z: number;
  heading: number; compassValid: boolean; gesture: string; confidence: number;
  flat: boolean; twoPhone?: boolean; geometryAge: number; age: number; cycle: number; members: number;
};
export type PhoneAlignment = { anchor: string; rotation: number; mirror: boolean };
export const PHONE_TIMEOUT = 1.5;

/** Preserve measured metre offsets; never fill missing geometry with a fabricated position. */
export function placePhones(phones: PhoneSample[], alignment: PhoneAlignment): RuntimeOperator[] {
  const anchor = phones.find(phone => phone.id === alignment.anchor && phone.pos && phone.age <= PHONE_TIMEOUT && phone.geometryAge <= 8);
  if (!anchor?.pos) return [];
  const rotation = alignment.rotation * Math.PI / 180;
  return phones.filter(phone => phone.room === anchor.room && phone.flat === anchor.flat && !!phone.twoPhone === !!anchor.twoPhone && phone.pos && phone.geometryAge <= 8).map(phone => {
    const x = phone.pos![0] - anchor.pos![0];
    const y = (phone.pos![1] - anchor.pos![1]) * (alignment.mirror ? -1 : 1);
    return {
      operator_id: phone.id, name: phone.name,
      position: { x: Math.cos(rotation) * x - Math.sin(rotation) * y, y: 5 + Math.sin(rotation) * x + Math.cos(rotation) * y, z: phone.flat ? 0 : phone.z - anchor.z },
      heading: phone.compassValid ? phone.heading : (alignment.mirror ? -phone.heading : phone.heading) + rotation,
      // Match the on-device MediaPipe acceptance threshold after temporal filtering.
      gesture: phone.confidence >= 0.55 ? phone.gesture : "None", gesture_confidence: phone.confidence,
      gesture_source: "phone", action: null, is_anchor: phone.id === anchor.id,
      controls: [], nearest_distance: null, age: phone.age,
    };
  });
}

const ACTIONS: Record<string, string> = {
  Closed_Fist: "follow", One_Finger_Up: "takeoff", Two_Fingers_Down: "land",
};

/** One owner of one browser drone. Handoffs require a fresh deliberate hold. */
export class PhoneControl {
  owner = "";
  private held = "None";
  private since = 0;
  private fired = false;
  following = "";
  private claimedOwner = "";
  private claims = new Map<string, { since: number; lastSeen: number; missingSince?: number; fired: boolean }>();
  claimProgress(id: string): number {
    const claim = this.claims.get(id);
    return claim && !claim.fired ? Math.min(1, (claim.lastSeen - claim.since) / 400) : 0;
  }
  reset(): void { this.owner = ""; this.following = ""; this.claimedOwner = ""; this.held = "None"; this.fired = false; this.claims.clear(); }

  update(operators: RuntimeOperator[], drone: { x: number; y: number }, now: number): {
    operator?: RuntimeOperator; action?: string; progress: number; stop: boolean; following?: boolean;
  } {
    const live = operators.filter(operator => operator.age <= PHONE_TIMEOUT);
    // Any phone can deliberately claim follow; distance must not prevent a handoff.
    for (const key of this.claims.keys()) if (!live.some(p => key === p.operator_id)) this.claims.delete(key);
    const ready: RuntimeOperator[] = [];
    for (const person of live) {
      const key = person.operator_id;
      let claim = this.claims.get(key);
      if (person.gesture !== "Closed_Fist") {
        if (claim) {
          claim.missingSince ??= now;
          if (now - claim.missingSince >= 250) this.claims.delete(key);
        }
        continue;
      }
      if (claim?.missingSince !== undefined) {
        if (now - claim.missingSince >= 250) claim = undefined;
        else {
          // Pause the hold through brief misclassification; never count missing frames.
          claim.since += now - claim.lastSeen;
          claim.missingSince = undefined;
        }
      }
      if (!claim) {
        claim = { since: now, lastSeen: now, fired: false }; this.claims.set(key, claim);
      }
      claim.lastSeen = now;
      if (!claim.fired && now - claim.since >= 400) { claim.fired = true; ready.push(person); }
    }
    const claimant = ready.filter(p => p.gesture === "Closed_Fist")
      .sort((a, b) => this.claims.get(b.operator_id)!.since - this.claims.get(a.operator_id)!.since || a.operator_id.localeCompare(b.operator_id))[0];
    if (claimant) {
      const changed = this.following !== claimant.operator_id;
      this.following = claimant.operator_id; this.owner = claimant.operator_id; this.held = claimant.gesture;
      this.claimedOwner = claimant.operator_id;
      return { operator: claimant, action: "follow", progress: 1, stop: changed, following: true };
    }
    if (this.following) {
      const followed = live.find(p => p.operator_id === this.following);
      if (!followed) {
        this.following = ""; this.claimedOwner = ""; this.owner = ""; this.held = "None"; this.fired = false;
        return { progress: 0, stop: true };
      }
      if (followed.gesture === "None" || followed.gesture === "Closed_Fist" || !ACTIONS[followed.gesture]) {
        this.owner = followed.operator_id; this.held = "None";
        return { operator: followed, action: "follow", progress: 1, stop: false, following: true };
      }
      // A single wrong camera label must not hand control back to the nearest person.
      if (this.held !== followed.gesture) { this.held = followed.gesture; this.since = now; }
      if (now - this.since < 400) return { operator: followed, action: "follow", progress: 1, stop: false, following: true };
      this.following = ""; this.fired = true;
      return { operator: followed, action: ACTIONS[followed.gesture], progress: 1, stop: true };
    }
    const distance = (operator: RuntimeOperator) => Math.hypot(operator.position.x - drone.x, operator.position.y - drone.y);
    const claimed = live.find(operator => operator.operator_id === this.claimedOwner);
    if (this.claimedOwner && !claimed) { this.reset(); return { progress: 0, stop: true }; }
    const nearest = [...live].sort((a, b) => distance(a) - distance(b) || a.operator_id.localeCompare(b.operator_id))[0];
    const previous = live.find(operator => operator.operator_id === this.owner);
    const operator = claimed ?? (previous && nearest && distance(nearest) >= distance(previous) * 0.72 ? previous : nearest);
    const changedOwner = (operator?.operator_id ?? "") !== this.owner;
    const gesture = operator?.gesture ?? "None";
    const changedGesture = gesture !== this.held;
    if (changedOwner || changedGesture) {
      this.owner = operator?.operator_id ?? "";
      this.held = gesture; this.since = now; this.fired = false;
    }
    const action = gesture === "Closed_Fist" ? undefined : ACTIONS[gesture];
    const progress = action ? Math.min(1, (now - this.since) / 400) : 0;
    const continuous = ["takeoff", "land"].includes(action ?? "");
    const emit = action && progress >= 1 && (!this.fired || continuous);
    if (emit) this.fired = true;
    return { operator, action: emit ? action : undefined, progress, stop: changedOwner || changedGesture || !operator };
  }
}
