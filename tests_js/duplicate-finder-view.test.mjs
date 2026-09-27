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

// Phase 1 (fuzzy match) result: bare member ids, no stats yet.
const FUZZY_GROUPS = [
  { members: ["sensor.kitchen_power_a", "sensor.kitchen_power_b"] },
];

// Phase 2 (correlate) result for that same group: confirmed, with stats.
const CORRELATED_GROUPS = [
  {
    members: [
      { entity_id: "sensor.kitchen_power_a", row_count: 24, earliest_start_ms: 0 },
      { entity_id: "sensor.kitchen_power_b", row_count: 12, earliest_start_ms: 0 },
    ],
  },
];

function mount({ scanResult = { groups: [] }, correlateResult, yamlResult = { yaml: "" } } = {}) {
  const el = new ViewElement();
  el._send = (msg) => {
    if (msg.type.endsWith("list_duplicate_candidates")) return Promise.resolve(scanResult);
    if (msg.type.endsWith("correlate_duplicate_group")) {
      return Promise.resolve(correlateResult || { groups: [] });
    }
    if (msg.type.endsWith("generate_exclude_yaml")) return Promise.resolve(yamlResult);
    return Promise.resolve({});
  };
  window.document.body.appendChild(el);
  return el;
}

async function scan(el) {
  el.shadowRoot.getElementById("scan-button").click();
  await new Promise((resolve) => setTimeout(resolve, 0));
}

async function correlate(el, groupIndex = 0) {
  el.shadowRoot.getElementById(`correlate-button-${groupIndex}`).click();
  await new Promise((resolve) => setTimeout(resolve, 0));
}

describe("duplicate-finder-view", () => {
  test("scan button triggers list_duplicate_candidates and renders unconfirmed groups", async () => {
    const el = mount({ scanResult: { groups: FUZZY_GROUPS } });
    await scan(el);
    const rows = el.shadowRoot.querySelectorAll("[data-entity-id]");
    const ids = Array.from(rows).map((r) => r.dataset.entityId);
    assert.deepEqual(ids, ["sensor.kitchen_power_a", "sensor.kitchen_power_b"]);
    // Nothing has been correlated yet, so no keep-selection radios exist.
    assert.equal(el.shadowRoot.querySelectorAll("input[type='radio']").length, 0);
    assert.ok(el.shadowRoot.getElementById("correlate-button-0"));
    window.document.body.removeChild(el);
  });

  test("scan does not call correlate_duplicate_group on its own", async () => {
    let correlateCalled = false;
    const el = new ViewElement();
    el._send = (msg) => {
      if (msg.type.endsWith("list_duplicate_candidates")) return Promise.resolve({ groups: FUZZY_GROUPS });
      if (msg.type.endsWith("correlate_duplicate_group")) correlateCalled = true;
      return Promise.resolve({});
    };
    window.document.body.appendChild(el);
    await scan(el);
    assert.equal(correlateCalled, false);
    window.document.body.removeChild(el);
  });

  test("check correlation sends only that group's members and renders the confirmed result", async () => {
    let sentMembers = null;
    const el = new ViewElement();
    el._send = (msg) => {
      if (msg.type.endsWith("list_duplicate_candidates")) return Promise.resolve({ groups: FUZZY_GROUPS });
      if (msg.type.endsWith("correlate_duplicate_group")) {
        sentMembers = msg.members;
        return Promise.resolve({ groups: CORRELATED_GROUPS });
      }
      return Promise.resolve({});
    };
    window.document.body.appendChild(el);
    await scan(el);
    await correlate(el, 0);
    assert.deepEqual(sentMembers, ["sensor.kitchen_power_a", "sensor.kitchen_power_b"]);
    const checkedRadio = el.shadowRoot.querySelector("input[type='radio']:checked");
    assert.equal(checkedRadio.value, "sensor.kitchen_power_a");
    window.document.body.removeChild(el);
  });

  test("a fuzzy group that fails to correlate into any pair shows 'no duplicates confirmed'", async () => {
    const el = mount({ scanResult: { groups: FUZZY_GROUPS }, correlateResult: { groups: [] } });
    await scan(el);
    await correlate(el, 0);
    const group = el.shadowRoot.querySelector('[data-group-index="0"]');
    assert.match(group.textContent, /no duplicates confirmed/i);
    assert.equal(el.shadowRoot.querySelectorAll("input[type='radio']").length, 0);
    window.document.body.removeChild(el);
  });

  test("a rejected correlation check shows an error for that group", async () => {
    const el = new ViewElement();
    el._send = (msg) => {
      if (msg.type.endsWith("list_duplicate_candidates")) return Promise.resolve({ groups: FUZZY_GROUPS });
      if (msg.type.endsWith("correlate_duplicate_group")) return Promise.reject(new Error("boom"));
      return Promise.resolve({});
    };
    window.document.body.appendChild(el);
    await scan(el);
    await correlate(el, 0);
    const group = el.shadowRoot.querySelector('[data-group-index="0"]');
    assert.match(group.textContent, /boom/);
    window.document.body.removeChild(el);
  });

  test("generate YAML button sends the current keep selection and renders the result", async () => {
    const el = mount({
      scanResult: { groups: FUZZY_GROUPS },
      correlateResult: { groups: CORRELATED_GROUPS },
      yamlResult: { yaml: "recorder:\n  exclude:\n    entities:\n      - sensor.kitchen_power_b\n" },
    });
    await scan(el);
    await correlate(el, 0);
    el.shadowRoot.getElementById("generate-yaml-button").click();
    await new Promise((resolve) => setTimeout(resolve, 0));
    const output = el.shadowRoot.getElementById("yaml-output");
    assert.match(output.textContent, /sensor\.kitchen_power_b/);
    window.document.body.removeChild(el);
  });

  test("overriding the keep selection changes what generate_exclude_yaml is sent", async () => {
    let sentSelections = null;
    const el = new ViewElement();
    el._send = (msg) => {
      if (msg.type.endsWith("list_duplicate_candidates")) return Promise.resolve({ groups: FUZZY_GROUPS });
      if (msg.type.endsWith("correlate_duplicate_group")) return Promise.resolve({ groups: CORRELATED_GROUPS });
      if (msg.type.endsWith("generate_exclude_yaml")) {
        sentSelections = msg.group_selections;
        return Promise.resolve({ yaml: "" });
      }
      return Promise.resolve({});
    };
    window.document.body.appendChild(el);
    await scan(el);
    await correlate(el, 0);
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

  test("a rejected scan shows an error instead of an unhandled rejection", async () => {
    const el = new ViewElement();
    el._send = () => Promise.reject(new Error("boom"));
    window.document.body.appendChild(el);
    el.shadowRoot.getElementById("scan-button").click();
    await new Promise((resolve) => setTimeout(resolve, 0));
    const error = el.shadowRoot.getElementById("scan-error");
    assert.ok(error, "an error element should be rendered");
    assert.match(error.textContent, /boom/);
    window.document.body.removeChild(el);
  });

  test("clicking scan again while one is in flight does not send a second request", async () => {
    let sendCount = 0;
    let resolveFirst;
    const el = new ViewElement();
    el._send = (msg) => {
      if (msg.type.endsWith("list_duplicate_candidates")) {
        sendCount += 1;
        return new Promise((resolve) => {
          resolveFirst = resolve;
        });
      }
      return Promise.resolve({});
    };
    window.document.body.appendChild(el);
    el.shadowRoot.getElementById("scan-button").click();
    el.shadowRoot.getElementById("scan-button").click();
    el.shadowRoot.getElementById("scan-button").click();
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(sendCount, 1);
    resolveFirst({ groups: [] });
    await new Promise((resolve) => setTimeout(resolve, 0));
    window.document.body.removeChild(el);
  });
});
