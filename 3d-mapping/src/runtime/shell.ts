/**
 * Runtime-mode DOM shell.
 *
 * Built in one place so the markup, its ids and the panels that drive it stay
 * together, and so the local sandbox in `index.html` keeps its own markup
 * untouched.
 */

const TEMPLATE = `
<header class="mc-top">
  <div class="mc-brand">
    <span class="mc-mark" aria-hidden="true"></span>
    <div><b>MISSION CONTROL</b><small>DNHacks 2026 · distributed runtime</small></div>
  </div>
  <div class="mc-headline">
    <span class="mc-state" id="mc-mission-state">STANDBY</span>
    <span class="mc-chip" data-tone="idle" id="mc-scenario">SCENARIO —</span>
    <span class="mc-chip" data-tone="idle" id="mc-clock">T+00.0</span>
    <span class="mc-chip" data-tone="idle" id="mc-connection">CONNECTING</span>
    <span class="mc-chip mc-hidden" data-tone="warn" id="mc-world">MAP</span>
  </div>
  <div class="mc-top-right">
    <div class="mc-segmented" role="group" aria-label="Camera mode" id="mc-camera">
      <button type="button" data-camera="overview" title="Fit every drone and objective">Overview</button>
      <button type="button" data-camera="follow" title="Track the selected drone, orbit stays free">Follow</button>
      <button type="button" data-camera="chase" title="Cinematic third-person behind the selected drone">Chase</button>
      <button type="button" data-camera="nose" title="Nose view from the selected drone">Nose</button>
      <button type="button" data-camera="top" title="Top-down planning view">Top</button>
      <button type="button" data-camera="focus" title="Fit the selected objective's region">Focus</button>
    </div>
    <button type="button" id="mc-demo" aria-pressed="false" title="Announce runtime events and follow them with the camera">Demo mode</button>
  </div>
</header>

<aside class="mc-rail mc-left" aria-label="Mission status">
  <section class="mc-panel">
    <h2>Mission <span id="mc-mission-sub"></span></h2>
    <dl class="mc-metrics" id="mc-metrics"></dl>
    <div class="mc-statuses" id="mc-statuses"></div>
  </section>
  <section class="mc-panel">
    <h2>Fleet <span id="mc-fleet-count"></span></h2>
    <div class="mc-roster" id="mc-roster"></div>
  </section>
  <section class="mc-panel">
    <h2>Objectives <span id="mc-objective-count"></span></h2>
    <div class="mc-roster" id="mc-objectives"></div>
  </section>
</aside>

<div class="mc-centre">
  <div id="mc-banner"></div>
  <div class="mc-legend" id="mc-legend">
    <h2>Legend</h2>
    <div class="mc-legend-grid" id="mc-legend-grid"></div>
  </div>
</div>

<aside class="mc-rail mc-right" aria-label="Inspector and controls">
  <nav class="mc-tabs" role="tablist" id="mc-tabs">
    <button type="button" role="tab" data-tab="inspect" aria-selected="true">Inspect</button>
    <button type="button" role="tab" data-tab="events" aria-selected="false">Events</button>
    <button type="button" role="tab" data-tab="layers" aria-selected="false">Layers</button>
    <button type="button" role="tab" data-tab="command" aria-selected="false">Command</button>
    <button type="button" role="tab" data-tab="disturb" aria-selected="false">Disturb</button>
  </nav>

  <section class="mc-panel" role="tabpanel" data-panel="inspect" id="mc-inspect"></section>

  <section class="mc-panel mc-hidden" role="tabpanel" data-panel="events">
    <h2>Event timeline
      <span class="mc-segmented" id="mc-event-filter">
        <button type="button" data-filter="headline" title="Only mission-critical transitions">Key</button>
        <button type="button" data-filter="notable" class="is-active" title="Key transitions plus assignments">Notable</button>
        <button type="button" data-filter="all" title="Everything except packet-level chatter">All</button>
      </span>
    </h2>
    <div class="mc-timeline" id="mc-timeline" aria-live="polite"></div>
    <p class="mc-note">Packet-level chatter is always suppressed. Select an event to focus the drones or objective it names.</p>
  </section>

  <section class="mc-panel mc-hidden" role="tabpanel" data-panel="layers">
    <h2>Map layers</h2>
    <div class="mc-toggles" id="mc-layer-toggles"></div>
    <h3>World belief</h3>
    <label class="mc-field"><span>Coverage source</span>
      <select id="mc-coverage-mode">
        <option value="operator">Global operator view</option>
        <option value="node">Observations by selected drone</option>
      </select>
    </label>
    <div id="mc-coverage-stats"></div>
    <p class="mc-note" id="mc-coverage-note"></p>
  </section>

  <section class="mc-panel mc-hidden" role="tabpanel" data-panel="command">
    <h2>Mission command</h2>
    <label class="mc-field"><span>Objective</span>
      <select id="mc-command-type">
        <option value="SEARCH">SEARCH — sweep a region</option>
        <option value="WATCH">WATCH — hold overwatch</option>
        <option value="TRACE">TRACE — fly a route</option>
        <option value="GOTO">GOTO — transit to a point</option>
      </select>
    </label>
    <label class="mc-field"><span>Target</span>
      <select id="mc-command-target"></select>
    </label>
    <div class="mc-actions">
      <label class="mc-field"><span>Units</span><input id="mc-command-units" type="number" min="1" max="8" value="2" /></label>
      <label class="mc-field"><span>Priority</span><input id="mc-command-priority" type="number" min="0" max="100" value="80" /></label>
    </div>
    <div class="mc-actions mc-actions-wide">
      <button type="button" id="mc-command-pick">Pick target on map</button>
      <button type="button" id="mc-command-submit" class="is-active">Submit objective</button>
    </div>
    <p class="mc-note" id="mc-command-status">Objectives post to <code>POST /api/missions</code>. The runtime allocates them.</p>
    <h3>Natural language</h3>
    <p class="mc-note" id="mc-command-context">Select a region, drone, or map point to ground words like “here” and “that drone.”</p>
    <label class="mc-field"><span>Instruction</span>
      <textarea id="mc-command-text" placeholder="Search this region with two."></textarea>
    </label>
    <div class="mc-actions">
      <button type="button" id="mc-command-send-text">Compile preview</button>
      <button type="button" id="mc-command-amend">Amend active plan</button>
    </div>
    <p class="mc-note" id="mc-command-text-status"></p>
    <div class="mc-plan-preview" id="mc-plan-preview"></div>
    <button type="button" id="mc-command-confirm" class="is-active" disabled>Confirm and start plan</button>
    <h3>Mission Q&amp;A</h3>
    <label class="mc-field"><span>Read-only question</span>
      <textarea id="mc-question" placeholder="Why did the relay move?"></textarea>
    </label>
    <button type="button" id="mc-question-send">Ask runtime</button>
    <p class="mc-note" id="mc-question-result"></p>
    <h3>Gesture link</h3>
    <p class="mc-note" id="mc-gesture-status">Gesture input is owned by the runtime bridge. This console reports its status and forwards the structured target it selects.</p>
  </section>

  <section class="mc-panel mc-hidden" role="tabpanel" data-panel="disturb">
    <h2>Disturbance injection</h2>
    <div class="mc-warning-block">These controls change the running simulation. They are deliberately separated from camera and layer controls.</div>
    <label class="mc-field"><span>Scenario preset</span>
      <select id="mc-preset">
        <option value="NORMAL">NORMAL</option>
        <option value="DEGRADED">DEGRADED</option>
        <option value="CONTESTED">CONTESTED</option>
        <option value="CHAOS">CHAOS</option>
      </select>
    </label>
    <div id="mc-sliders"></div>
    <h3>Failures</h3>
    <div class="mc-actions">
      <button type="button" id="mc-fail-drone" class="mc-danger">Fail a drone</button>
      <button type="button" id="mc-fail-control" class="mc-danger">Fail control</button>
      <button type="button" id="mc-recover-drone">Recover drone</button>
      <button type="button" id="mc-recover-control">Restore control</button>
    </div>
    <h3>Runtime</h3>
    <div class="mc-actions">
      <button type="button" id="mc-pause">Pause</button>
      <button type="button" id="mc-reset" class="mc-danger">Reset runtime</button>
    </div>
    <p class="mc-note" id="mc-disturb-status"></p>
  </section>
</aside>

<div class="mc-toasts" id="mc-toasts" role="status" aria-live="polite"></div>
`;

export type Shell = {
  root: HTMLElement;
  query: <T extends HTMLElement>(selector: string) => T;
  all: <T extends HTMLElement>(selector: string) => T[];
};

export function buildShell(): Shell {
  const root = document.createElement("div");
  root.className = "mc";
  root.id = "mission-control";
  root.innerHTML = TEMPLATE;
  document.body.append(root);
  return {
    root,
    query: <T extends HTMLElement>(selector: string): T => {
      const element = root.querySelector<T>(selector);
      if (!element) throw new Error(`mission control shell is missing ${selector}`);
      return element;
    },
    all: <T extends HTMLElement>(selector: string): T[] => [...root.querySelectorAll<T>(selector)],
  };
}

export const escapeHtml = (value: unknown): string =>
  String(value).replace(/[&<>'"]/g, (char) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]!);

export const percent = (value: number): string => `${Math.round(value * 100)}%`;
