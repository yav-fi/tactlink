import { escapeHtml, type Shell } from "../shell";
import type { InterferenceKey, RuntimeSnapshot } from "../types";

/**
 * Demo disturbance controls.
 *
 * Kept in their own tab so a failure injection is never one pixel away from a
 * camera button. Only the reset is confirmed; the failure buttons are the point
 * of the demo and must stay instant.
 */

const SLIDERS: { key: InterferenceKey; label: string; max: number; step: number }[] = [
  { key: "network_interference", label: "Network interference", max: 1, step: 0.05 },
  { key: "gps_interference", label: "GPS interference", max: 1, step: 0.05 },
  { key: "sensor_interference", label: "Sensor interference", max: 1, step: 0.05 },
  { key: "node_failure_rate", label: "Node failure rate", max: 0.2, step: 0.005 },
];

export type DisturbanceHandlers = {
  setInterference: (config: Record<InterferenceKey, number>) => Promise<void>;
  setPreset: (preset: string) => Promise<void>;
  failDrone: () => Promise<void>;
  failControl: () => Promise<void>;
  recoverDrone: (nodeId: string) => Promise<void>;
  recoverControl: () => Promise<void>;
  togglePause: (running: boolean) => Promise<void>;
  reset: () => Promise<void>;
};

export class DisturbancePanel {
  private readonly sliders: HTMLElement;
  private readonly preset: HTMLSelectElement;
  private readonly status: HTMLElement;
  private readonly pauseButton: HTMLButtonElement;
  private readonly recoverDroneButton: HTMLButtonElement;
  private readonly recoverControlButton: HTMLButtonElement;
  private offlineNodes: string[] = [];
  private running = true;

  constructor(shell: Shell, private readonly handlers: DisturbanceHandlers) {
    this.sliders = shell.query("#mc-sliders");
    this.preset = shell.query<HTMLSelectElement>("#mc-preset");
    this.status = shell.query("#mc-disturb-status");
    this.pauseButton = shell.query<HTMLButtonElement>("#mc-pause");
    this.recoverDroneButton = shell.query<HTMLButtonElement>("#mc-recover-drone");
    this.recoverControlButton = shell.query<HTMLButtonElement>("#mc-recover-control");

    this.sliders.innerHTML = SLIDERS.map((slider) => `
      <div class="mc-slider" data-tone="ok" data-key="${slider.key}">
        <div class="mc-slider-head">
          <span>${escapeHtml(slider.label)}</span><b data-value="${slider.key}">0%</b>
        </div>
        <input type="range" min="0" max="${slider.max}" step="${slider.step}" value="0"
               data-interference="${slider.key}" aria-label="${escapeHtml(slider.label)}" />
      </div>`).join("");

    this.sliders.addEventListener("input", (event) => {
      const input = event.target as HTMLInputElement;
      if (!input.dataset.interference) return;
      this.paintSlider(input.dataset.interference as InterferenceKey, Number(input.value));
    });
    this.sliders.addEventListener("change", () => void this.push());

    this.preset.addEventListener("change", () => this.run(() => this.handlers.setPreset(this.preset.value), `Scenario set to ${this.preset.value}.`));
    shell.query("#mc-fail-drone").addEventListener("click", () => this.run(this.handlers.failDrone, "Failed a random drone."));
    shell.query("#mc-fail-control").addEventListener("click", () => this.run(this.handlers.failControl, "Mission control offline; nodes continue peer-to-peer."));
    this.recoverControlButton.addEventListener("click", () => this.run(this.handlers.recoverControl, "Mission control restored."));
    this.recoverDroneButton.addEventListener("click", () => {
      const node = this.offlineNodes[0];
      if (!node) return;
      void this.run(() => this.handlers.recoverDrone(node), `Recovered ${node}.`);
    });
    this.pauseButton.addEventListener("click", () => this.run(() => this.handlers.togglePause(this.running), this.running ? "Runtime paused." : "Runtime resumed."));
    shell.query("#mc-reset").addEventListener("click", () => {
      if (!window.confirm("Reset the runtime to its deterministic initial state? This clears the current mission, trails and event history.")) return;
      void this.run(this.handlers.reset, "Runtime reset.");
    });
  }

  private paintSlider(key: InterferenceKey, value: number): void {
    const label = this.sliders.querySelector<HTMLElement>(`[data-value="${key}"]`);
    const wrapper = this.sliders.querySelector<HTMLElement>(`[data-key="${key}"]`);
    const scale = key === "node_failure_rate" ? value / 0.2 : value;
    if (label) label.textContent = key === "node_failure_rate" ? `${(value * 100).toFixed(1)}%` : `${Math.round(value * 100)}%`;
    if (wrapper) wrapper.dataset.tone = scale >= 0.6 ? "crit" : scale >= 0.3 ? "warn" : "ok";
  }

  private async push(): Promise<void> {
    const config = {} as Record<InterferenceKey, number>;
    for (const slider of SLIDERS) {
      const input = this.sliders.querySelector<HTMLInputElement>(`[data-interference="${slider.key}"]`);
      config[slider.key] = Number(input?.value ?? 0);
    }
    await this.run(() => this.handlers.setInterference(config), "Interference updated.");
  }

  private async run(action: () => Promise<void>, message: string): Promise<void> {
    try {
      await action();
      this.status.textContent = message;
    } catch (error) {
      this.status.textContent = error instanceof Error ? error.message : "The runtime rejected that request.";
    }
  }

  sync(snapshot: RuntimeSnapshot): void {
    this.running = snapshot.running;
    this.pauseButton.textContent = snapshot.running ? "Pause" : "Resume";
    this.offlineNodes = snapshot.drones.filter((drone) => !drone.truth.online).map((drone) => drone.identity.node_id);
    this.recoverDroneButton.disabled = this.offlineNodes.length === 0;
    this.recoverDroneButton.textContent = this.offlineNodes.length
      ? `Recover ${this.offlineNodes[0]}`
      : "Recover drone";
    this.recoverControlButton.disabled = snapshot.control_available;
    if (document.activeElement !== this.preset) this.preset.value = snapshot.scenario;
    for (const slider of SLIDERS) {
      const input = this.sliders.querySelector<HTMLInputElement>(`[data-interference="${slider.key}"]`);
      if (!input || document.activeElement === input) continue;
      input.value = String(snapshot.interference[slider.key]);
      this.paintSlider(slider.key, snapshot.interference[slider.key]);
    }
  }
}
