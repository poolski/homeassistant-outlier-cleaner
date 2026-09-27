/**
 * Tests for the Duplicate Finder view.
 *
 *   cd tests_js && npm install && npm test
 */

import { test, describe, before } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const VIEW = join(
  dirname(fileURLToPath(import.meta.url)),
  "..",
  "custom_components",
  "recorder_toolkit",
  "frontend",
  "duplicate-finder-view.js"
);

let window;
let ViewElement;

before(() => {
  const dom = new JSDOM("<!doctype html><html><body></body></html>", {
    runScripts: "outside-only",
    url: "http://localhost:8123/",
    pretendToBeVisual: true,
  });
  window = dom.window;
  window.eval(readFileSync(VIEW, "utf8"));
  ViewElement = window.customElements.get("duplicate-finder-view");
  assert.ok(ViewElement, "duplicate-finder-view should be registered on load");
});

const GROUPS = [
  {
    members: [
      { entity_id: "sensor.kitchen_power_a", row_count: 24, earliest_start_ms: 0 },
      { entity_id: "sensor.kitchen_power_b", row_count: 12, earliest_start_ms: 0 },
    ],
  },
];

function mount({ scanResult = { groups: [] }, yamlResult = { yaml: "" } } = {}) {
  const el = new ViewElement();
  el._send = (msg) => {
    if (msg.type.endsWith("list_duplicate_candidates")) return Promise.resolve(scanResult);
    if (msg.type.endsWith("generate_exclude_yaml")) return Promise.resolve(yamlResult);
    return Promise.resolve({});
  };
  window.document.body.appendChild(el);
  return el;
}

describe("duplicate-finder-view", () => {
  test("scan button triggers list_duplicate_candidates and renders groups", async () => {
    const el = mount({ scanResult: { groups: GROUPS } });
    el.shadowRoot.getElementById("scan-button").click();
    await new Promise((resolve) => setTimeout(resolve, 0));
    const rows = el.shadowRoot.querySelectorAll("[data-entity-id]");
    const ids = Array.from(rows).map((r) => r.dataset.entityId);
    assert.deepEqual(ids, ["sensor.kitchen_power_a", "sensor.kitchen_power_b"]);
    window.document.body.removeChild(el);
  });

  test("suggested keep (most complete entity) is pre-selected", async () => {
    const el = mount({ scanResult: { groups: GROUPS } });
    el.shadowRoot.getElementById("scan-button").click();
    await new Promise((resolve) => setTimeout(resolve, 0));
    const checkedRadio = el.shadowRoot.querySelector("input[type='radio']:checked");
    assert.equal(checkedRadio.value, "sensor.kitchen_power_a");
    window.document.body.removeChild(el);
  });

  test("generate YAML button sends the current keep selection and renders the result", async () => {
    const el = mount({
      scanResult: { groups: GROUPS },
      yamlResult: { yaml: "recorder:\n  exclude:\n    entities:\n      - sensor.kitchen_power_b\n" },
    });
    el.shadowRoot.getElementById("scan-button").click();
    await new Promise((resolve) => setTimeout(resolve, 0));
    el.shadowRoot.getElementById("generate-yaml-button").click();
    await new Promise((resolve) => setTimeout(resolve, 0));
    const output = el.shadowRoot.getElementById("yaml-output");
    assert.match(output.textContent, /sensor\.kitchen_power_b/);
    window.document.body.removeChild(el);
  });

  test("overriding the keep selection changes what generate_exclude_yaml is sent", async () => {
    const el = mount({ scanResult: { groups: GROUPS } });
    let sentSelections = null;
    el._send = (msg) => {
      if (msg.type.endsWith("list_duplicate_candidates")) return Promise.resolve({ groups: GROUPS });
      if (msg.type.endsWith("generate_exclude_yaml")) {
        sentSelections = msg.group_selections;
        return Promise.resolve({ yaml: "" });
      }
      return Promise.resolve({});
    };
    el.shadowRoot.getElementById("scan-button").click();
    await new Promise((resolve) => setTimeout(resolve, 0));
    // Override: keep B instead of the suggested A.
    const radioB = el.shadowRoot.querySelector("input[value='sensor.kitchen_power_b']");
    radioB.checked = true;
    radioB.dispatchEvent(new window.Event("change", { bubbles: true }));
    el.shadowRoot.getElementById("generate-yaml-button").click();
    await new Promise((resolve) => setTimeout(resolve, 0));
    // sentSelections' objects were constructed inside the jsdom window
    // realm; round-trip through JSON to compare plain data rather than
    // tripping assert's cross-realm prototype check.
    assert.deepEqual(JSON.parse(JSON.stringify(sentSelections)), [
      {
        members: ["sensor.kitchen_power_a", "sensor.kitchen_power_b"],
        keep: "sensor.kitchen_power_b",
      },
    ]);
    window.document.body.removeChild(el);
  });
});
