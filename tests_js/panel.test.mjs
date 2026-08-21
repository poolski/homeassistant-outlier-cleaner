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
