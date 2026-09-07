/**
 * Does ha-date-range-picker actually load inside a custom panel?
 *
 * The panel reaches it through window.loadCardHelpers(), which depends on HA
 * frontend internals: the energy date selection card's module registering
 * ha-date-range-picker as an import side effect. jsdom cannot answer whether
 * that still holds — only a real HA frontend can. That is the entire reason
 * this suite exists, so keep it to that question.
 */

import { expect, test } from "@playwright/test";
import { PANEL_TAG, installDeepQuery, openPanel } from "./_helpers.mjs";

const PICKER_TAG = "ha-date-range-picker";

test.beforeEach(async ({ page }) => {
  await installDeepQuery(page);
  await openPanel(page);
});

test("the panel registers ha-date-range-picker via loadCardHelpers", async ({
  page,
}) => {
  // Not defined by HA for a custom panel, so if it is defined the panel's
  // _ensureDateRangePicker() is what defined it.
  await expect
    .poll(
      () => page.evaluate((tag) => Boolean(customElements.get(tag)), PICKER_TAG),
      { timeout: 20_000, message: `${PICKER_TAG} was never registered` }
    )
    .toBe(true);
});

test("the picker replaces the native date inputs in the panel", async ({
  page,
}) => {
  const state = async () =>
    page.evaluate(
      ({ panelTag, pickerTag }) => {
        const root = window.__deepQuery(panelTag)?.shadowRoot;
        if (!root) return null;
        return {
          picker: Boolean(root.querySelector(pickerTag)),
          nativeInputs: root.querySelectorAll('input[type="date"]').length,
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

  // The fallback must actually be gone, not merely hidden behind the picker.
  expect((await state()).nativeInputs).toBe(0);
});

test("choosing a preset changes the range sent to fetch_outliers", async ({
  page,
}) => {
  // Read the range off the outgoing websocket frame rather than the DOM: the
  // frame is what the backend acts on, so it is the thing worth asserting.
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
    await page.evaluate(
      ({ panelTag }) => {
        const el = window.__deepQuery(panelTag);
        // A statistic must be set for _scan() to proceed; the id need not exist,
        // because the range is decided before the backend is consulted.
        el._statId = "outlier_test:does_not_exist";
        el._scan();
      },
      { panelTag: PANEL_TAG }
    );
    await expect
      .poll(() => page.evaluate(() => window.__scanFrames.length), {
        timeout: 15_000,
      })
      .toBeGreaterThan(0);
    return page.evaluate(() => window.__scanFrames.pop());
  };

  const before = await scan();
  expect(before.start_ts).toBeDefined();
  expect(before.end_ts).toBeDefined();

  // Drive the picker the way the element itself does, so this does not depend
  // on HA's dialog markup — which is what churns between releases.
  const applied = await page.evaluate(
    ({ panelTag, pickerTag }) => {
      const root = window.__deepQuery(panelTag).shadowRoot;
      const picker = root.querySelector(pickerTag);
      if (!picker) return null;

      const startDate = new Date();
      startDate.setDate(startDate.getDate() - 1);
      startDate.setHours(0, 0, 0, 0);
      const endDate = new Date(startDate);
      endDate.setHours(23, 59, 59, 999);

      picker.dispatchEvent(
        new CustomEvent("value-changed", {
          detail: { value: { startDate, endDate } },
        })
      );
      return { start_ts: startDate.getTime() / 1000, end_ts: endDate.getTime() / 1000 };
    },
    { panelTag: PANEL_TAG, pickerTag: PICKER_TAG }
  );

  expect(applied, "picker should be mounted by now").not.toBeNull();

  const after = await scan();

  expect(after.start_ts).toBeCloseTo(applied.start_ts, 0);
  expect(after.end_ts).toBeCloseTo(applied.end_ts, 0);
  expect(after.start_ts).not.toBeCloseTo(before.start_ts, 0);
});

test("the picker offers the preset rows the panel asks for", async ({ page }) => {
  // extendedPresets is what produces "Last 7 days" and friends. Asserting the
  // property took effect catches the element renaming or dropping it.
  await expect
    .poll(
      () =>
        page.evaluate(
          ({ panelTag, pickerTag }) => {
            const picker = window
              .__deepQuery(panelTag)
              ?.shadowRoot?.querySelector(pickerTag);
            if (!picker) return null;
            return {
              extendedPresets: picker.extendedPresets,
              // Left unset on purpose: that is what makes the element build its
              // own Today / Yesterday / This week set.
              rangesUnset: picker.ranges === undefined,
            };
          },
          { panelTag: PANEL_TAG, pickerTag: PICKER_TAG }
        ),
      { timeout: 20_000 }
    )
    .toEqual({ extendedPresets: true, rangesUnset: true });
});
