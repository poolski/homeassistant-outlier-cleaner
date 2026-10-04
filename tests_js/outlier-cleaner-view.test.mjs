/**
 * Tests for the sidebar panel web component.
 *
 *   cd tests_js && npm install && npm test
 *
 * The panel is plain DOM with no build step, so it's evaluated inside a jsdom
 * window and driven directly. `_send` is stubbed, so nothing here talks to a
 * recorder or a WebSocket.
 */

import { test, describe, before, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const PANEL = join(
  dirname(fileURLToPath(import.meta.url)),
  "..",
  "custom_components",
  "recorder_toolkit",
  "frontend",
  "outlier-cleaner-view.js"
);

let window;
let PanelElement;

before(() => {
  const dom = new JSDOM("<!doctype html><html><body></body></html>", {
    runScripts: "outside-only",
    url: "http://localhost:8123/",
    pretendToBeVisual: true,
  });
  window = dom.window;
  window.eval(readFileSync(PANEL, "utf8"));
  PanelElement = window.customElements.get("outlier-cleaner-view");
  assert.ok(PanelElement, "panel custom element should be registered on load");
});

const CANDIDATES = [
  { start: 1_700_000_000_000, end: 1_700_003_600_000, change: 500, state: 1, period: "hour" },
  { start: 1_700_086_400_000, end: 1_700_090_000_000, change: 400, state: 2, period: "hour" },
  { start: 1_700_172_800_000, end: 1_700_176_400_000, change: 300, state: 3, period: "hour" },
];

/**
 * Mount a panel. `narrow` is left unset unless passed, mirroring HA. `stats` is
 * what `list_sum_statistics` returns; defaults to an empty list.
 */
function mount({ narrow, stats = [] } = {}) {
  const el = new PanelElement();
  const queued = [];
  el._send = (msg) => {
    if (queued.length) return Promise.resolve(queued.shift());
    if (msg.type.endsWith("list_fixes")) return Promise.resolve({ fixes: [] });
    if (msg.type.endsWith("list_sum_statistics")) return Promise.resolve({ statistics: stats });
    return Promise.resolve({});
  };
  el._queue = queued;
  window.document.body.appendChild(el);
  if (narrow !== undefined) el.narrow = narrow;
  el.hass = { states: {} };
  return el;
}

const $ = (el, id) => el.shadowRoot.getElementById(id);
const settle = () => new Promise((r) => setTimeout(r, 10));

async function mountWithResults(opts) {
  const el = mount(opts);
  el._statId = "sensor.test";
  el._queue.push({ candidates: CANDIDATES, scanned_rows: 999, method: "top_n" });
  await el._scan();
  await settle();
  return el;
}

describe("websocket commands use the recorder_toolkit domain", () => {
  test("list_sum_statistics is sent under the current integration domain", async () => {
    const seenTypes = [];
    const el = new PanelElement();
    el._send = (msg) => {
      seenTypes.push(msg.type);
      return Promise.resolve({ statistics: [] });
    };
    window.document.body.appendChild(el);
    el.hass = { states: {} };
    await settle();
    assert.ok(
      seenTypes.includes("recorder_toolkit/list_sum_statistics"),
      `expected a recorder_toolkit/list_sum_statistics message, got: ${seenTypes.join(", ")}`
    );
    window.document.body.removeChild(el);
  });
});

describe("scan results are not pre-selected", () => {
  let el;
  beforeEach(async () => {
    el = await mountWithResults({ narrow: false });
  });

  test("no row is selected after a scan", () => {
    assert.equal(el._selected.size, 0);
    const boxes = [...el.shadowRoot.querySelectorAll(".row-check")];
    assert.equal(boxes.length, 3, "all candidates should still be listed");
    assert.ok(boxes.every((b) => !b.checked), "no checkbox should start ticked");
    assert.equal($(el, "check-all").checked, false);
  });

  test("apply is blocked until the user picks something", () => {
    assert.equal($(el, "btn-apply").disabled, true);
    assert.match($(el, "apply-summary").innerHTML, /Select rows above/);
    assert.equal($(el, "selection-count").textContent, "0 of 3 selected");
  });

  test("ticking one row selects only that row", () => {
    const boxes = [...el.shadowRoot.querySelectorAll(".row-check")];
    boxes[1].checked = true;
    boxes[1].dispatchEvent(new window.Event("change"));

    assert.deepEqual([...el._selected], [1]);
    assert.equal($(el, "btn-apply").disabled, false);
    assert.equal($(el, "check-all").indeterminate, true, "partial selection should be indeterminate");
  });

  test("select all and deselect all still work", () => {
    el._selectAll(true);
    assert.equal(el._selected.size, 3);
    assert.equal($(el, "check-all").checked, true);

    el._selectAll(false);
    assert.equal(el._selected.size, 0);
    assert.equal($(el, "check-all").checked, false);
  });

  test("a fix leaves the remaining rows unselected", async () => {
    el._selected = new Set([0]);
    el._queue.push({ applied: 1, planned: 1, fix_id: "abc", errors: [] });
    await el._applyFix();
    await settle();

    assert.equal(el._candidates.length, 2, "unfixed rows should remain listed");
    assert.equal(el._selected.size, 0, "remaining rows must not be auto-selected");
    assert.equal($(el, "btn-apply").disabled, true);
  });
});

describe("the view fills the shell's content area", () => {
  // The toolbar and the full-height layout belong to the shell, which stacks
  // toolbar -> tabs -> view. The view must not claim the viewport itself, or
  // it pushes the shell's toolbar off-screen.

  test("no toolbar of its own", () => {
    const el = mount({ narrow: true });
    assert.equal($(el, "app-toolbar"), null);
    assert.equal(el.shadowRoot.querySelector(".menu-btn"), null);
  });

  test("the cards live inside the scrolling pane", () => {
    const el = mount({ narrow: true });
    const content = $(el, "panel-content");

    for (const id of ["results-card", "apply-area", "history-table", "btn-scan"]) {
      assert.ok(content.contains($(el, id)), `${id} should be inside the scrolling pane`);
    }
  });

  test("host flexes into its container instead of taking the viewport", () => {
    const el = mount({ narrow: true });
    const css = el.shadowRoot.querySelector("style").textContent;
    const host = css.slice(css.indexOf(":host {"), css.indexOf("}", css.indexOf(":host {")));

    assert.match(host, /flex-direction:\s*column/);
    assert.match(host, /min-height:\s*0/, "must be able to shrink so the pane scrolls");
    assert.match(host, /overflow:\s*hidden/, "host must not be the scroller");
    assert.doesNotMatch(host, /100dvh|100vh/, "the shell owns the viewport height");
  });
});

describe("method description", () => {
  test("is collapsed by default", () => {
    const el = mount({ narrow: false });
    const details = $(el, "method-help").querySelector("details");
    assert.ok(details, "help should be a disclosure");
    assert.equal(details.open, false);
    assert.match(details.querySelector("summary").textContent, /MAD/);
  });

  test("stays open across method switches once opened", () => {
    const el = mount({ narrow: false });
    const details = $(el, "method-help").querySelector("details");
    details.open = true;
    details.dispatchEvent(new window.Event("toggle"));

    $(el, "method-seg").querySelector('[data-value="absolute"]').click();

    const next = $(el, "method-help").querySelector("details");
    assert.match(next.querySelector("summary").textContent, /Absolute/i);
    assert.equal(next.open, true);
  });
});

describe("baseline days", () => {
  test("MAD scans send baseline_days from the field", async () => {
    const el = mount({ narrow: false });
    $(el, "baseline-days").value = "21";
    const params = await scanParams(el);
    assert.equal(params.baseline_days, 21);
  });

  test("the field is only shown for MAD", () => {
    const el = mount({ narrow: false });
    const group = $(el, "opt-baseline");
    assert.equal(group.classList.contains("hidden"), false);
    $(el, "method-seg").querySelector('[data-value="top_n"]').click();
    assert.equal(group.classList.contains("hidden"), true);
  });
});

// ---------------------------------------------------------------------------
// Date range
// ---------------------------------------------------------------------------

/** Capture the params of the next fetch_outliers call. */
async function scanParams(el) {
  let captured;
  const previous = el._send;
  el._send = (msg) => {
    if (msg.type.endsWith("fetch_outliers")) {
      captured = msg;
      return Promise.resolve({ candidates: [], scanned_rows: 0, method: "absolute" });
    }
    return previous(msg);
  };
  el._statId = "sensor.test";
  await el._scan();
  await settle();
  return captured;
}

const startOfDay = (offsetDays) => {
  const d = new Date();
  d.setDate(d.getDate() + offsetDays);
  d.setHours(0, 0, 0, 0);
  return d;
};

describe("date range without HA's picker available", () => {
  // jsdom provides no window.loadCardHelpers, so mounting here is what a panel
  // looks like before (or without) HA's picker.
  let el;
  beforeEach(async () => {
    el = mount({ narrow: false });
    await settle();
  });

  test("no home-grown date inputs are rendered", () => {
    // The picker is the only date control; there is deliberately no fallback
    // field of our own to diverge from it.
    assert.equal(el.shadowRoot.querySelectorAll('input[type="date"]').length, 0);
    assert.equal($(el, "date-start"), null);
    assert.equal($(el, "date-end"), null);
  });

  test("the wrap is left empty for the picker to fill", () => {
    const wrap = $(el, "date-range-wrap");
    assert.ok(wrap, "the picker's mount point should exist");
    assert.equal(wrap.children.length, 0);
  });

  test("scanning still works, using the default 30-day range", async () => {
    const params = await scanParams(el);

    // Local midnight, not UTC midnight: parsing "YYYY-MM-DD" as UTC was off by
    // up to a day for anyone east or west of Greenwich.
    assert.equal(params.start_ts, startOfDay(-30).getTime() / 1000);

    const endOfToday = startOfDay(1).getTime() / 1000;
    assert.ok(
      params.end_ts < endOfToday && endOfToday - params.end_ts < 1,
      `end_ts ${params.end_ts} should sit just below next local midnight`
    );
  });

  test("it keeps retrying as hass updates arrive", async () => {
    assert.equal(el.shadowRoot.querySelector("ha-date-range-picker"), null);

    // The frontend finishes coming up only now — a cold deep-link into the
    // panel does exactly this.
    window.loadCardHelpers = async () => ({
      createCardElement: async () => {
        if (!window.customElements.get("ha-date-range-picker")) {
          window.customElements.define(
            "ha-date-range-picker",
            class extends window.HTMLElement {}
          );
        }
        throw new Error("no energy collection configured");
      },
    });

    el.hass = { states: {}, marker: "later" };
    await settle();

    assert.ok(
      el.shadowRoot.querySelector("ha-date-range-picker"),
      "a later hass update should mount the picker"
    );
    delete window.loadCardHelpers;
  });
});

describe("date range uses HA's picker when it can be loaded", () => {
  let el;

  beforeEach(async () => {
    // Stand in for HA's lazy-loading path: loadCardHelpers().createCardElement()
    // is called only for its import side effect, which registers the element.
    window.loadCardHelpers = async () => ({
      createCardElement: async () => {
        if (!window.customElements.get("ha-date-range-picker")) {
          window.customElements.define(
            "ha-date-range-picker",
            class extends window.HTMLElement {}
          );
        }
        // HA's real card throws here without an energy collection; the element
        // is registered regardless, which is what the panel relies on.
        throw new Error("no energy collection configured");
      },
    });
    el = mount({ narrow: false });
    await settle();
  });

  test("the picker replaces the native inputs", () => {
    assert.ok(
      el.shadowRoot.querySelector("ha-date-range-picker"),
      "picker should be mounted"
    );
    assert.equal($(el, "date-start"), null, "native From input should be gone");
    assert.equal($(el, "date-end"), null, "native To input should be gone");
  });

  test("the picker is given the current range and no hass", () => {
    const picker = el.shadowRoot.querySelector("ha-date-range-picker");

    // The element reads locale and config from HA context, not a hass property.
    assert.equal(picker.hass, undefined, "nothing should be forwarded as hass");
    assert.ok(picker.startDate instanceof window.Date || picker.startDate instanceof Date);
    assert.ok(picker.endDate instanceof window.Date || picker.endDate instanceof Date);
  });

  test("a later hass update leaves the mounted picker alone", () => {
    const picker = el.shadowRoot.querySelector("ha-date-range-picker");
    el.hass = { states: {}, marker: "second" };

    assert.equal(
      el.shadowRoot.querySelector("ha-date-range-picker"),
      picker,
      "the picker should not be re-created on a state change"
    );
    assert.equal(picker.hass, undefined, "still nothing forwarded as hass");
  });

  test("a value-changed event drives the scanned range", async () => {
    const picker = el.shadowRoot.querySelector("ha-date-range-picker");
    const startDate = new Date(2026, 1, 3, 0, 0, 0, 0);
    const endDate = new Date(2026, 1, 4, 23, 59, 59, 999);

    picker.dispatchEvent(
      new window.CustomEvent("value-changed", {
        detail: { value: { startDate, endDate } },
      })
    );

    const params = await scanParams(el);

    assert.equal(params.start_ts, startDate.getTime() / 1000);
    assert.equal(params.end_ts, endDate.getTime() / 1000);
  });

  test("the selection is written back so the picker's own field refreshes", () => {
    // HA's ha-date-range-picker fires value-changed but does not update its own
    // startDate/endDate — the consumer has to feed them back or the field keeps
    // showing the previous range.
    const picker = el.shadowRoot.querySelector("ha-date-range-picker");
    const startDate = new Date(2026, 1, 3, 0, 0, 0, 0);
    const endDate = new Date(2026, 1, 3, 23, 59, 59, 999);

    picker.dispatchEvent(
      new window.CustomEvent("value-changed", {
        detail: { value: { startDate, endDate } },
      })
    );

    assert.equal(picker.startDate, startDate);
    assert.equal(picker.endDate, endDate);
  });
});

// ---------------------------------------------------------------------------
// Statistic selection
// ---------------------------------------------------------------------------

const STATS = [
  { statistic_id: "sensor.grid_import", name: "Grid import" },
  { statistic_id: "sensor.gas_meter", name: null },
  { statistic_id: "tibber:home_energy", name: "Tibber home" }, // external, no entity
];

// Runs before the block that registers a stub ha-entity-picker, so here the
// element genuinely cannot load — mirrors "date range without HA's picker".
describe("statistic field without card helpers", () => {
  test("falls back to a plain text input that still drives a scan", async () => {
    delete window.loadCardHelpers;
    const el = mount({ narrow: false, stats: STATS });
    await settle();

    assert.ok($(el, "stat-input"), "fallback input should render");
    assert.equal(el.shadowRoot.querySelector("ha-entity-picker"), null);

    let captured;
    el._send = (m) => {
      if (m.type.endsWith("fetch_outliers")) {
        captured = m;
        return Promise.resolve({ candidates: [], scanned_rows: 0, method: "mad" });
      }
      if (m.type.endsWith("list_fixes")) return Promise.resolve({ fixes: [] });
      return Promise.resolve({});
    };
    $(el, "stat-input").value = "tibber:home_energy";
    await el._scan();
    await settle();

    assert.equal(captured.statistic_id, "tibber:home_energy");
  });
});

describe("statistic selection uses HA's entity picker", () => {
  let el;

  beforeEach(async () => {
    // Stand in for HA's lazy-loading path. createCardElement registers
    // ha-date-range-picker as an import side effect (as the energy card does);
    // the entities card's getConfigElement pulls in ha-entity-picker.
    window.loadCardHelpers = async () => ({
      createCardElement: async () => {
        if (!window.customElements.get("ha-date-range-picker")) {
          window.customElements.define(
            "ha-date-range-picker",
            class extends window.HTMLElement {}
          );
        }
        return {
          constructor: {
            getConfigElement: async () => {
              if (!window.customElements.get("ha-entity-picker")) {
                window.customElements.define(
                  "ha-entity-picker",
                  class extends window.HTMLElement {}
                );
              }
            },
          },
        };
      },
    });
    el = mount({ narrow: false, stats: STATS });
    await settle();
  });

  test("the picker replaces the plain text field", () => {
    assert.ok(
      el.shadowRoot.querySelector("ha-entity-picker"),
      "entity picker should be mounted"
    );
    assert.equal($(el, "stat-input"), null, "fallback text input should be gone");
  });

  test("only sum statistics that are real entities are offered", () => {
    const picker = el.shadowRoot.querySelector("ha-entity-picker");
    // tibber:home_energy is a long-term statistic with no backing entity, so it
    // cannot appear in an entity picker.
    assert.deepEqual(picker.includeEntities, [
      "sensor.grid_import",
      "sensor.gas_meter",
    ]);
  });

  test("a value-changed selection becomes the scanned statistic", async () => {
    const picker = el.shadowRoot.querySelector("ha-entity-picker");
    picker.dispatchEvent(
      new window.CustomEvent("value-changed", {
        detail: { value: "sensor.gas_meter" },
      })
    );

    // scanParams() forces its own _statId, so read the frame directly here.
    let captured;
    el._send = (m) => {
      if (m.type.endsWith("fetch_outliers")) {
        captured = m;
        return Promise.resolve({ candidates: [], scanned_rows: 0, method: "mad" });
      }
      if (m.type.endsWith("list_fixes")) return Promise.resolve({ fixes: [] });
      return Promise.resolve({});
    };
    await el._scan();
    await settle();

    assert.equal(captured.statistic_id, "sensor.gas_meter");
  });

  test("clearing the picker unsets the statistic", () => {
    el._statId = "sensor.gas_meter";
    const picker = el.shadowRoot.querySelector("ha-entity-picker");
    picker.dispatchEvent(
      new window.CustomEvent("value-changed", { detail: { value: "" } })
    );
    assert.equal(el._statId, null);
  });

  test("a recent chip fills the picker and selects it", () => {
    el._recentStats = [{ statistic_id: "sensor.grid_import", name: "Grid import" }];
    el._renderRecents();

    const chip = el.shadowRoot.querySelector("#stat-recents button[data-value]");
    assert.ok(chip, "a recent chip should render");
    chip.click();

    const picker = el.shadowRoot.querySelector("ha-entity-picker");
    assert.equal(picker.value, "sensor.grid_import");
    assert.equal(el._statId, "sensor.grid_import");
  });
});
