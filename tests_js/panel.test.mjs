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
  "statistics_outlier_cleaner",
  "frontend",
  "statistics-outlier-cleaner-panel.js"
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
  PanelElement = window.customElements.get("statistics-outlier-cleaner-panel");
  assert.ok(PanelElement, "panel custom element should be registered on load");
});

const CANDIDATES = [
  { start: 1_700_000_000_000, end: 1_700_003_600_000, change: 500, state: 1, period: "hour" },
  { start: 1_700_086_400_000, end: 1_700_090_000_000, change: 400, state: 2, period: "hour" },
  { start: 1_700_172_800_000, end: 1_700_176_400_000, change: 300, state: 3, period: "hour" },
];

/** Mount a panel. `narrow` is left unset unless passed, mirroring HA. */
function mount({ narrow } = {}) {
  const el = new PanelElement();
  const queued = [];
  el._send = (msg) => {
    if (queued.length) return Promise.resolve(queued.shift());
    if (msg.type.endsWith("list_fixes")) return Promise.resolve({ fixes: [] });
    if (msg.type.endsWith("list_sum_statistics")) return Promise.resolve({ statistics: [] });
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

describe("sidebar stays reachable when the sidebar is hidden", () => {
  // A custom panel with embed_iframe: false owns the whole view and HA renders
  // no header, so without this the panel is a dead end on mobile.

  test("narrow renders a labelled menu button and the title", () => {
    const el = mount({ narrow: true });
    const bar = $(el, "app-toolbar");
    const btn = bar.querySelector(".menu-btn");

    assert.ok(btn, "a menu button should be present when narrow");
    assert.equal(btn.getAttribute("aria-label"), "Open sidebar");
    assert.match(bar.textContent, /Statistics Outlier Cleaner/);
  });

  test("clicking it fires hass-toggle-menu out of the shadow root", () => {
    const el = mount({ narrow: true });
    let event = null;
    window.document.addEventListener("hass-toggle-menu", (e) => { event = e; }, { once: true });

    $(el, "app-toolbar").querySelector(".menu-btn").click();

    // bubbles + composed match fireEvent's defaults in the HA frontend, which
    // is what lets the event escape our shadow root and reach the app shell.
    assert.ok(event, "event should reach the document");
    assert.equal(event.bubbles, true);
    assert.equal(event.composed, true);
  });

  test("no button when the sidebar is already showing", () => {
    const el = mount({ narrow: false });
    assert.equal($(el, "app-toolbar").querySelector(".menu-btn"), null);
  });

  test("flipping narrow at runtime re-renders the toolbar", () => {
    const el = mount({ narrow: false });

    el.narrow = true;
    assert.ok($(el, "app-toolbar").querySelector(".menu-btn"), "rotating to narrow should add the button");

    el.narrow = false;
    assert.equal($(el, "app-toolbar").querySelector(".menu-btn"), null);
  });

  test("an unset narrow still gets a way out", () => {
    // If HA never tells us, fail toward reachable rather than trapping the user.
    const el = mount();
    assert.ok($(el, "app-toolbar").querySelector(".menu-btn"));
  });
});

describe("toolbar is pinned by layout, not by positioning", () => {
  // jsdom does no layout, so these pin the structure that produces it. The
  // toolbar floated mid-screen on a real phone because ha-panel-custom gives us
  // no height: `height: 100%` collapsed to auto, the document scrolled instead
  // of us, and `position: sticky` pinned to that scrollport, not the viewport.

  test("toolbar is a sibling above the scrolling pane, not inside it", () => {
    const el = mount({ narrow: true });
    const bar = $(el, "app-toolbar");
    const content = $(el, "panel-content");

    assert.ok(content, "a dedicated scrolling pane should exist");
    assert.equal(bar.parentElement, content.parentElement, "both should be top-level children");
    assert.equal(bar.nextElementSibling, content, "toolbar should come immediately before the pane");
    assert.equal(content.contains(bar), false, "toolbar must not scroll with the content");
  });

  test("the cards live inside the scrolling pane", () => {
    const el = mount({ narrow: true });
    const content = $(el, "panel-content");

    for (const id of ["results-card", "apply-area", "history-table", "btn-scan"]) {
      assert.ok(content.contains($(el, id)), `${id} should be inside the scrolling pane`);
    }
  });

  test("host takes a definite height and does not itself scroll", () => {
    const el = mount({ narrow: true });
    const css = el.shadowRoot.querySelector("style").textContent;
    const host = css.slice(css.indexOf(":host {"), css.indexOf("}", css.indexOf(":host {")));

    assert.match(host, /flex-direction:\s*column/, "host should lay out as a column");
    assert.match(host, /100dvh/, "host needs a viewport-derived height, not a percentage");
    assert.match(host, /overflow:\s*hidden/, "host must not be the scroller");
    assert.doesNotMatch(host, /height:\s*100%/, "percentage height collapses against ha-panel-custom");
  });

  test("toolbar does not rely on sticky positioning", () => {
    const el = mount({ narrow: true });
    const css = el.shadowRoot.querySelector("style").textContent;
    const bar = css.slice(css.indexOf(".app-toolbar {"), css.indexOf("}", css.indexOf(".app-toolbar {")));

    assert.doesNotMatch(bar, /position:\s*sticky/, "sticky pins to the wrong scrollport here");
    assert.match(bar, /flex:\s*0 0 auto/, "toolbar should be a fixed-size flex row");
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

  test("the picker is given hass and the current range", () => {
    const picker = el.shadowRoot.querySelector("ha-date-range-picker");

    assert.ok(picker.hass, "hass must be forwarded or the picker cannot localise");
    assert.ok(picker.startDate instanceof window.Date || picker.startDate instanceof Date);
    assert.ok(picker.endDate instanceof window.Date || picker.endDate instanceof Date);
  });

  test("hass updates are forwarded to the picker", () => {
    const picker = el.shadowRoot.querySelector("ha-date-range-picker");
    const next = { states: {}, marker: "second" };
    el.hass = next;

    assert.equal(picker.hass.marker, "second");
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

  // ha-date-range-picker is a controlled component: picking a range fires
  // value-changed but does NOT update the element's own startDate/endDate. HA's
  // own panels re-feed the value through a Lit binding. We have no binding, so
  // unless we write it back the element keeps displaying — and keeps handing its
  // inner picker — the range it was mounted with, and the prev/next arrows shift
  // from that stale range instead of the selected one.
  test("the selected range is written back to the picker", () => {
    const picker = el.shadowRoot.querySelector("ha-date-range-picker");
    const startDate = new Date(2026, 1, 3, 0, 0, 0, 0);
    const endDate = new Date(2026, 1, 4, 23, 59, 59, 999);

    picker.dispatchEvent(
      new window.CustomEvent("value-changed", {
        detail: { value: { startDate, endDate } },
      })
    );

    assert.equal(
      picker.startDate.getTime(),
      startDate.getTime(),
      "picker.startDate must reflect the selection or the label shows the old range"
    );
    assert.equal(
      picker.endDate.getTime(),
      endDate.getTime(),
      "picker.endDate must reflect the selection or the label shows the old range"
    );
  });

  test("a later hass update does not revert the selected range", () => {
    const picker = el.shadowRoot.querySelector("ha-date-range-picker");
    const startDate = new Date(2026, 1, 3, 0, 0, 0, 0);
    const endDate = new Date(2026, 1, 4, 23, 59, 59, 999);

    picker.dispatchEvent(
      new window.CustomEvent("value-changed", {
        detail: { value: { startDate, endDate } },
      })
    );

    // Every HA state change assigns picker.hass, which re-renders the element
    // from its own properties.
    el.hass = { states: {}, marker: "after-selection" };

    assert.equal(picker.startDate.getTime(), startDate.getTime());
    assert.equal(picker.endDate.getTime(), endDate.getTime());
  });
});

