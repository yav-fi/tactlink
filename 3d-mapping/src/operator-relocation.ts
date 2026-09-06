import type { LocalVector, RuntimeOperator } from "./runtime/types";

/** Local display offsets survive fresh UWB packets without altering phone data. */
export class OperatorRelocation {
  private group: LocalVector = { x: 0, y: 0, z: 0 };
  private offsets = new Map<string, LocalVector>();
  reset(): void { this.group = { x: 0, y: 0, z: 0 }; this.offsets.clear(); }
  apply(people: RuntimeOperator[]): RuntimeOperator[] {
    return people.map(person => {
      const offset = this.offsets.get(person.operator_id) ?? { x: 0, y: 0, z: 0 };
      return { ...person, position: {
        x: person.position.x + this.group.x + offset.x,
        y: person.position.y + this.group.y + offset.y,
        z: person.position.z + this.group.z + offset.z,
      } };
    });
  }
  move(id: string, destination: LocalVector, people: RuntimeOperator[]): boolean {
    if (![destination.x, destination.y, destination.z].every(Number.isFinite)) return false;
    const selected = id === "*" ? people : people.filter(person => person.operator_id === id);
    if (!selected.length) return false;
    const center = selected.reduce((sum, p) => ({ x: sum.x + p.position.x / selected.length,
      y: sum.y + p.position.y / selected.length, z: sum.z + p.position.z / selected.length }), { x: 0, y: 0, z: 0 });
    const delta = { x: destination.x - center.x, y: destination.y - center.y, z: destination.z - center.z };
    const previous = id === "*" ? this.group : this.offsets.get(id) ?? { x: 0, y: 0, z: 0 };
    const next = { x: previous.x + delta.x, y: previous.y + delta.y, z: previous.z + delta.z };
    if (id === "*") this.group = next;
    else this.offsets.set(id, next);
    return true;
  }
}
