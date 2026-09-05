import { escapeHtml, percent, type Shell } from "../shell";
import { shortId } from "../taxonomy";
import { toneFor } from "./status";
import type { CoverageMode } from "../layers/coverage";
import type { RuntimeSnapshot } from "../types";

/**
 * Map layer visibility, plus the world-belief source selector.
 *
 * Every toggle maps to exactly one renderer option; nothing here changes what
 * the backend does.
 */

export type LayerKey =
  | "trails" | "plans" | "altitude" | "estimates"
  | "links" | "downLinks" | "linkLabels" | "relayRange"
  | "coverage" | "patterns" | "comms" | "regions" | "obstacles";

export type LayerState = Record<LayerKey, boolean>;

export const DEFAULT_LAYERS: LayerState = {
  trails: true, plans: true, altitude: true, estimates: false,
  links: true, downLinks: true, linkLabels: false, relayRange: true,
  coverage: true, patterns: true, comms: false, regions: true, obstacles: true,
};

const GROUPS: { title: string; items: { key: LayerKey; label: string; hint: string }[] }[] = [
  {
    title: "Aircraft",
    items: [
      { key: "trails", label: "Flight trails", hint: "flown" },
      { key: "plans", label: "Planned routes", hint: "intent" },
      { key: "altitude", label: "Altitude stems and shadows", hint: "height" },
      { key: "estimates", label: "Localization estimate and error", hint: "belief" },
    ],
  },
  {
    title: "Network",
    items: [
      { key: "links", label: "Peer links", hint: "" },
      { key: "downLinks", label: "Unavailable links", hint: "broken" },
      { key: "linkLabels", label: "Link quality labels", hint: "" },
      { key: "relayRange", label: "Relay reach rings", hint: "" },
    ],
  },
  {
    title: "World",
    items: [
      { key: "coverage", label: "Search coverage", hint: "" },
      { key: "patterns", label: "Objective patterns", hint: "" },
      { key: "comms", label: "Comm terrain (observed)", hint: "beta" },
      { key: "regions", label: "Mission regions", hint: "" },
      { key: "obstacles", label: "Obstacles", hint: "" },
    ],
  },
];

export class LayersPanel {
  private readonly host: HTMLElement;
  private readonly stats: HTMLElement;
  private readonly note: HTMLElement;
  private readonly modeSelect: HTMLSelectElement;
  readonly state: LayerState = { ...DEFAULT_LAYERS };
  mode: CoverageMode = "operator";

  constructor(
    shell: Shell,
    private readonly onChange: (state: LayerState) => void,
    private readonly onModeChange: (mode: CoverageMode) => void,
  ) {
    this.host = shell.query("#mc-layer-toggles");
    this.stats = shell.query("#mc-coverage-stats");
    this.note = shell.query("#mc-coverage-note");
    this.modeSelect = shell.query<HTMLSelectElement>("#mc-coverage-mode");

    this.host.innerHTML = GROUPS.map((group) => `
      <h3 style="margin-top:6px">${escapeHtml(group.title)}</h3>
      ${group.items.map((item) => `
        <label class="mc-toggle-row">
          <input type="checkbox" data-layer="${item.key}"${this.state[item.key] ? " checked" : ""} />
          <span>${escapeHtml(item.label)}</span>
          ${item.hint ? `<small>${escapeHtml(item.hint)}</small>` : ""}
        </label>`).join("")}
    `).join("");

    this.host.addEventListener("change", (event) => {
      const input = event.target as HTMLInputElement;
      const key = input.dataset.layer as LayerKey | undefined;
      if (!key) return;
      this.state[key] = input.checked;
      this.onChange(this.state);
    });

    this.modeSelect.addEventListener("change", () => {
      this.mode = this.modeSelect.value as CoverageMode;
      this.onModeChange(this.mode);
    });
  }

  setLayer(key: LayerKey, value: boolean): void {
    this.state[key] = value;
    const input = this.host.querySelector<HTMLInputElement>(`[data-layer="${key}"]`);
    if (input) input.checked = value;
    this.onChange(this.state);
  }

  render(snapshot: RuntimeSnapshot, selectedDrone: string | null): void {
    const regions = snapshot.world_knowledge.regions;
    this.stats.innerHTML = regions.length
      ? regions.map((region) => `
        <div class="mc-status-row">
          <b>${escapeHtml(region.region_id)}</b>
          <span style="color:${toneFor(region.coverage, 0.5, 0.2) === "ok" ? "#3ddc97" : toneFor(region.coverage, 0.5, 0.2) === "warn" ? "#ffb454" : "#ff6b6b"}">
            ${percent(region.coverage)} cov · ${percent(region.fresh_coverage)} fresh · ${percent(region.mean_confidence)} conf
          </span>
        </div>`).join("")
      : `<p class="mc-empty">No coverage grids published.</p>`;

    this.note.innerHTML = this.mode === "operator"
      ? "<strong>Global operator view.</strong> This is the union of every node's shared belief, assembled by the console for the operator. Node autonomy never sees it."
      : selectedDrone
        ? `<strong>Observations by ${escapeHtml(shortId(selectedDrone))}.</strong> Only cells whose latest observation came from this node. Cells it learned from peers are not published per node, so this is a subset of its belief, not its full world model.`
        : "<strong>Select a drone</strong> to show only the cells that node observed itself.";
  }
}
