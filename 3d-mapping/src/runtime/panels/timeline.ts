import { escapeHtml, type Shell } from "../shell";
import { explainEvent, focusTargets, passesFilter, styleFor, type EventFilter } from "../events";
import type { RuntimeEvent } from "../types";

/**
 * Event timeline.
 *
 * Events are kept in a bounded local history keyed by sequence number, so the
 * story survives between snapshots without growing without limit.
 */

const HISTORY_LIMIT = 220;
const RENDER_LIMIT = 40;

export class Timeline {
  private readonly host: HTMLElement;
  private readonly history: RuntimeEvent[] = [];
  private readonly seen = new Set<number>();
  private filter: EventFilter = "notable";

  constructor(
    shell: Shell,
    private readonly onFocus: (drones: string[], tasks: string[]) => void,
  ) {
    this.host = shell.query("#mc-timeline");
    const filters = shell.query("#mc-event-filter");
    filters.addEventListener("click", (event) => {
      const button = (event.target as HTMLElement).closest<HTMLButtonElement>("[data-filter]");
      if (!button) return;
      this.filter = button.dataset.filter as EventFilter;
      for (const item of filters.querySelectorAll("button")) {
        item.classList.toggle("is-active", item === button);
      }
      this.render();
    });
    this.host.addEventListener("click", (event) => {
      const card = (event.target as HTMLElement).closest<HTMLElement>("[data-sequence]");
      if (!card) return;
      const entry = this.history.find((item) => item.sequence === Number(card.dataset.sequence));
      if (!entry) return;
      const targets = focusTargets(entry);
      this.onFocus(targets.drones, targets.tasks);
    });
  }

  /** Adds any events not seen before and returns them in arrival order. */
  ingest(events: RuntimeEvent[]): RuntimeEvent[] {
    const fresh: RuntimeEvent[] = [];
    for (const event of events) {
      if (this.seen.has(event.sequence)) continue;
      this.seen.add(event.sequence);
      this.history.push(event);
      fresh.push(event);
    }
    if (this.history.length > HISTORY_LIMIT) {
      for (const dropped of this.history.splice(0, this.history.length - HISTORY_LIMIT)) {
        this.seen.delete(dropped.sequence);
      }
    }
    if (fresh.length) this.render();
    return fresh;
  }

  clear(): void {
    this.history.length = 0;
    this.seen.clear();
    this.render();
  }

  render(): void {
    const visible = this.history
      .filter((event) => passesFilter(event.event_type, this.filter))
      .slice(-RENDER_LIMIT)
      .reverse();

    if (!visible.length) {
      this.host.innerHTML = `<p class="mc-empty">No events at this level yet.</p>`;
      return;
    }

    this.host.innerHTML = visible.map((event) => {
      const style = styleFor(event.event_type);
      const why = explainEvent(event);
      const targets = focusTargets(event);
      const clickable = targets.drones.length || targets.tasks.length;
      return `
        <button type="button" class="mc-event" data-tone="${style.tone}" data-sequence="${event.sequence}"${clickable ? "" : " disabled"}>
          <span class="mc-event-accent"></span>
          <span class="mc-event-body">
            <span class="mc-event-head">
              <span class="mc-event-title">${escapeHtml(style.title)}</span>
              <span class="mc-event-time">T+${event.timestamp.toFixed(1)}</span>
            </span>
            <span class="mc-event-summary">${escapeHtml(event.human_readable_summary)}</span>
            ${why ? `<span class="mc-event-why"><b>Why</b>${escapeHtml(why)}</span>` : ""}
          </span>
        </button>`;
    }).join("");
  }
}
