/** Configurable demo estimates, not a calibrated aircraft performance model. */
export const BATTERY_PRESETS = {
  lightweight: { capacityWh: 35, massKg: 0.22 },
  standard: { capacityWh: 60, massKg: 0.38 },
  endurance: { capacityWh: 95, massKg: 0.62 },
};
export class Battery {
  enabled = false;
  capacityWh = 60;
  massKg = 0.38;
  health = 1;
  remainingWh = 60;
  powerW = 0;
  reserve = 0.2;
  get usableWh(): number { return this.capacityWh * this.health; }
  get fraction(): number { return this.remainingWh / this.usableWh; }
  get depleted(): boolean { return this.enabled && this.remainingWh <= 0; }
  replace(capacityWh: number, massKg: number, health: number): void {
    if (![capacityWh, massKg, health].every(Number.isFinite) || capacityWh < 1 || capacityWh > 500 || massKg < 0.05 || massKg > 3 || health < 0.1 || health > 1) throw new Error('Use 1–500 Wh, 0.05–3 kg, and 10–100% health.');
    this.capacityWh = capacityWh; this.massKg = massKg; this.health = health;
    this.remainingWh = this.usableWh; this.powerW = 0;
  }
  estimatePower(horizontal: number, vertical: number, acceleration = 0): number {
    const mass = 0.85 + this.massKg;
    const hover = 155 * Math.pow(mass / 1.23, 1.5);
    // A modest forward-flight efficiency benefit, followed by cubic drag cost.
    return hover * (1 - 0.15 * Math.min(horizontal / 8, 1)) + 0.022 * horizontal ** 3
      + mass * 9.81 * Math.max(vertical, 0) / 0.65 + 5 * Math.abs(Math.min(vertical, 0))
      + 5 * mass * Math.abs(acceleration);
  }
  step(seconds: number, horizontal: number, vertical: number, acceleration: number, airborne: boolean): void {
    if (!this.enabled || seconds <= 0 || !Number.isFinite(seconds)) { this.powerW = 0; return; }
    this.powerW = this.depleted ? 0 : airborne ? this.estimatePower(horizontal, vertical, acceleration) : 2;
    this.remainingWh = Math.max(0, this.remainingWh - this.powerW * seconds / 3600);
  }
}
