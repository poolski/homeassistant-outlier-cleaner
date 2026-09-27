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
