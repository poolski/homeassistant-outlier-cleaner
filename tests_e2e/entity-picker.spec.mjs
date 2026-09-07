/**
 * Does ha-entity-picker actually work inside a custom panel on a real frontend?
 *
 * The panel creates ha-entity-picker directly (registering it first via
 * loadCardHelpers if the bundle has not already), and the element reads its data
 * from HA context rather than a hass property. jsdom cannot confirm either still
 * holds — only a real HA frontend can. Keep this suite to that question.
 */

import { expect, test } from "@playwright/test";
import { PANEL_TAG, installDeepQuery, openPanel } from "./_helpers.mjs";

const PICKER_TAG = "ha-entity-picker";

test.beforeEach(async ({ page }) => {
  await installDeepQuery(page);
  await openPanel(page);
});

test("ha-entity-picker is registered by the time the panel needs it", async ({
  page,
}) => {
  // Current HA ships it in the base bundle; on an older one the panel's
  // _ensureEntityPicker() registers it via loadCardHelpers. Either way it must
  // be defined.
  await expect
    .poll(
      () => page.evaluate((tag) => Boolean(customElements.get(tag)), PICKER_TAG),
      { timeout: 20_000, message: `${PICKER_TAG} was never registered` }
    )
    .toBe(true);
});

test("the picker replaces the plain text field", async ({ page }) => {
  const state = async () =>
    page.evaluate(
      ({ panelTag, pickerTag }) => {
        const root = window.__deepQuery(panelTag)?.shadowRoot;
        if (!root) return null;
        return {
          picker: Boolean(root.querySelector(pickerTag)),
          fallback: Boolean(root.getElementById("stat-input")),
        };
      },
      { panelTag: PANEL_TAG, pickerTag: PICKER_TAG }
    );

  await expect
    .poll(async () => (await state())?.picker, {
      timeout: 20_000,
      message: "picker was never mounted into the panel",
    })
    .toBe(true);

  expect((await state()).fallback).toBe(false);
});

test("a value-changed selection changes the statistic sent to fetch_outliers", async ({
  page,
}) => {
  await page.evaluate(() => {
    window.__scanFrames = [];
    const send = WebSocket.prototype.send;
    WebSocket.prototype.send = function (data) {
      if (typeof data === "string" && data.includes("fetch_outliers")) {
        try {
          window.__scanFrames.push(JSON.parse(data));
        } catch {
          /* not our frame */
        }
      }
      return send.call(this, data);
    };
  });

  const scan = async () => {
    await page.evaluate((panelTag) => {
      window.__deepQuery(panelTag)._scan();
    }, PANEL_TAG);
    await expect
      .poll(() => page.evaluate(() => window.__scanFrames.length), {
        timeout: 15_000,
      })
      .toBeGreaterThan(0);
    return page.evaluate(() => window.__scanFrames.pop());
  };

  // Drive the picker the way the element itself does, so this does not depend on
  // HA's combobox markup — which is what churns between releases.
  const applied = await page.evaluate(
    ({ panelTag, pickerTag }) => {
      const picker = window
        .__deepQuery(panelTag)
        ?.shadowRoot?.querySelector(pickerTag);
      if (!picker) return null;
      const id = "outlier_test.does_not_exist";
      picker.dispatchEvent(
        new CustomEvent("value-changed", { detail: { value: id } })
      );
      return id;
    },
    { panelTag: PANEL_TAG, pickerTag: PICKER_TAG }
  );

  expect(applied, "picker should be mounted by now").not.toBeNull();

  const frame = await scan();
  expect(frame.statistic_id).toBe(applied);
});
