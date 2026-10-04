/**
 * Tests for the tabbed panel shell.
 *
 *   cd tests_js && npm install && npm test
 */

import { test, describe, before } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const FRONTEND_DIR = join(
  dirname(fileURLToPath(import.meta.url)),
  "..",
  "custom_components",
  "recorder_toolkit",
  "frontend"
);

let window;
let ShellElement;

before(() => {
  const dom = new JSDOM("<!doctype html><html><body></body></html>", {
    runScripts: "outside-only",
    url: "http://localhost:8123/",
    pretendToBeVisual: true,
  });
  window = dom.window;
  window.eval(readFileSync(join(FRONTEND_DIR, "recorder-toolkit-panel.js"), "utf8"));
  ShellElement = window.customElements.get("recorder-toolkit-panel");
  assert.ok(ShellElement, "shell custom element should be registered on load");
});

describe("recorder-toolkit-panel shell", () => {
  test("renders two tab buttons on mount", () => {
    const el = new ShellElement();
    window.document.body.appendChild(el);
    const tabs = el.shadowRoot.querySelectorAll("[role='tab']");
    const labels = Array.from(tabs).map((t) => t.textContent.trim());
    assert.deepEqual(labels, ["Outlier Cleaner", "Duplicate Finder"]);
    window.document.body.removeChild(el);
  });

  test("defaults to the Outlier Cleaner tab active", () => {
    const el = new ShellElement();
    window.document.body.appendChild(el);
    const active = el.shadowRoot.querySelector("[role='tab'][aria-selected='true']");
    assert.equal(active.textContent.trim(), "Outlier Cleaner");
    window.document.body.removeChild(el);
  });
});

/** Mount the shell. `narrow` is left unset unless passed, mirroring HA. */
function mount({ narrow } = {}) {
  const el = new ShellElement();
  window.document.body.appendChild(el);
  if (narrow !== undefined) el.narrow = narrow;
  return el;
}

const bar = (el) => el.shadowRoot.getElementById("app-toolbar");

describe("sidebar stays reachable when the sidebar is hidden", () => {
  // A custom panel with embed_iframe: false owns the whole view and HA renders
  // no header, so without this the panel is a dead end on mobile.

  test("narrow renders a labelled menu button and the title", () => {
    const el = mount({ narrow: true });
    const btn = bar(el).querySelector(".menu-btn");

    assert.ok(btn, "a menu button should be present when narrow");
    assert.equal(btn.getAttribute("aria-label"), "Open sidebar");
    assert.match(bar(el).textContent, /Recorder Toolkit/);
  });

  test("clicking it fires hass-toggle-menu out of the shadow root", () => {
    const el = mount({ narrow: true });
    let event = null;
    window.document.addEventListener("hass-toggle-menu", (e) => { event = e; }, { once: true });

    bar(el).querySelector(".menu-btn").click();

    // bubbles + composed match fireEvent's defaults in the HA frontend, which
    // is what lets the event escape our shadow root and reach the app shell.
    assert.ok(event, "event should reach the document");
    assert.equal(event.bubbles, true);
    assert.equal(event.composed, true);
  });

  test("no button when the sidebar is already showing", () => {
    const el = mount({ narrow: false });
    assert.equal(bar(el).querySelector(".menu-btn"), null);
  });

  test("flipping narrow at runtime re-renders the toolbar", () => {
    const el = mount({ narrow: false });

    el.narrow = true;
    assert.ok(bar(el).querySelector(".menu-btn"), "rotating to narrow should add the button");

    el.narrow = false;
    assert.equal(bar(el).querySelector(".menu-btn"), null);
  });

  test("an unset narrow still gets a way out", () => {
    // If HA never tells us, fail toward reachable rather than trapping the user.
    const el = mount();
    assert.ok(bar(el).querySelector(".menu-btn"));
  });

  test("switching tabs keeps the toolbar", () => {
    const el = mount({ narrow: true });
    el.shadowRoot.querySelector("[data-tab-id='duplicates']").click();
    assert.ok(bar(el).querySelector(".menu-btn"));
  });
});

describe("toolbar is pinned by layout, not by positioning", () => {
  // jsdom does no layout, so these pin the structure that produces it. The
  // toolbar floated mid-screen on a real phone because ha-panel-custom gives us
  // no height: `height: 100%` collapsed to auto, the document scrolled instead
  // of us, and `position: sticky` pinned to that scrollport, not the viewport.

  test("toolbar, then tabs, then content, as siblings", () => {
    const el = mount({ narrow: true });
    const tabs = el.shadowRoot.querySelector("[role='tablist']");
    const content = el.shadowRoot.getElementById("content");

    assert.equal(bar(el).nextElementSibling, tabs, "tabs sit below the toolbar");
    assert.equal(tabs.nextElementSibling, content);
    assert.equal(content.contains(bar(el)), false, "toolbar must not scroll with the content");
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
    const rule = css.slice(css.indexOf(".app-toolbar {"), css.indexOf("}", css.indexOf(".app-toolbar {")));

    assert.doesNotMatch(rule, /position:\s*sticky/, "sticky pins to the wrong scrollport here");
    assert.match(rule, /flex:\s*0 0 auto/, "toolbar should be a fixed-size flex row");
  });
});
