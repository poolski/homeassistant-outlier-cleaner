/**
 * Is there always a usable date control?
 *
 * The panel prefers HA's ha-date-range-picker, but it can only load that when the
 * frontend exposes window.loadCardHelpers - which it often does not, because that
 * is defined as a side effect of loading the Lovelace panel. On HA 2026.8 the
 * default landing page is not a dashboard, so a normal session never has it.
 *
 * Whatever the version, the native inputs have to be there and have to drive the
 * scan. `date-range-picker.spec.mjs` covers the upgrade path when it is possible.
 */

import { expect, test } from "@playwright/test";

const PANEL_PATH = "/statistics-outlier-cleaner";
const PANEL_TAG = "statistics-outlier-cleaner-panel";
const PICKER_TAG = "ha-date-range-picker";

const USERNAME = process.env.HASS_USERNAME || "dev";
const PASSWORD = process.env.HASS_PASSWORD || "dev";

async function login(page) {
  await page.goto(PANEL_PATH);
  const username = page.locator('input[name="username"]');
  const panelEl = page.locator(PANEL_TAG);
  await expect(username.or(panelEl).first()).toBeAttached({ timeout: 60_000 });
  if (!(await username.count())) return;
  await username.fill(USERNAME);
  await page.locator('input[name="password"]').fill(PASSWORD);
  await page.keyboard.press("Enter");
  await page.waitForURL(`**${PANEL_PATH}**`, { timeout: 60_000 });
}

async function installDeepQuery(page) {
  await page.addInitScript(() => {
    window.__deepQuery = (tag, root = document) => {
      const direct = root.querySelector(tag);
      if (direct) return direct;
      for (const el of root.querySelectorAll("*")) {
        if (el.shadowRoot) {
          const found = window.__deepQuery(tag, el.shadowRoot);
          if (found) return found;
        }
      }
      return null;
    };
  });
}

test.beforeEach(async ({ page }) => {
  await installDeepQuery(page);
  await login(page);
  await page.locator(PANEL_TAG).waitFor({ state: "attached", timeout: 60_000 });
});

test("the panel always has a date control", async ({ page }) => {
  // Either HA's picker got mounted, or the native inputs are still there. What
  // must never happen is neither, which is what a removed fallback gave us on
  // any HA without loadCardHelpers.
  await expect
    .poll(
      () =>
        page.evaluate(
          ({ panelTag, pickerTag }) => {
            const root = window.__deepQuery(panelTag)?.shadowRoot;
            if (!root) return null;
            return (
              Boolean(root.querySelector(pickerTag)) ||
              root.querySelectorAll('input[type="date"]').length === 2
            );
          },
          { panelTag: PANEL_TAG, pickerTag: PICKER_TAG }
        ),
      { timeout: 30_000, message: "the panel ended up with no date control at all" }
    )
    .toBe(true);
});

test("the native inputs drive the scanned range when the picker is absent", async ({
  page,
}) => {
  const hasPicker = await page.evaluate(
    ({ panelTag, pickerTag }) =>
      Boolean(window.__deepQuery(panelTag)?.shadowRoot?.querySelector(pickerTag)),
    { panelTag: PANEL_TAG, pickerTag: PICKER_TAG }
  );
  test.skip(hasPicker, "HA's picker loaded here, so it owns the range");

  // Read the range off the outgoing frame: that is what the backend acts on.
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

  const start = page.locator(`${PANEL_TAG} #date-start`);
  const end = page.locator(`${PANEL_TAG} #date-end`);
  await expect(start).toBeVisible();
  await expect(end).toBeVisible();

  await start.fill("2026-02-03");
  await start.dispatchEvent("change");
  await end.fill("2026-02-04");
  await end.dispatchEvent("change");

  await page.evaluate(
    ({ panelTag }) => {
      const el = window.__deepQuery(panelTag);
      // The id need not exist; the range is decided before the backend is asked.
      el._statId = "outlier_test:does_not_exist";
      el._scan();
    },
    { panelTag: PANEL_TAG }
  );

  await expect
    .poll(() => page.evaluate(() => window.__scanFrames.length), { timeout: 15_000 })
    .toBeGreaterThan(0);

  const frame = await page.evaluate(() => window.__scanFrames.pop());

  // Local midnight through to the last instant of the chosen end day. Computed in
  // the browser so the expectation uses the same timezone the panel ran in.
  const expected = await page.evaluate(() => ({
    start: new Date(2026, 1, 3, 0, 0, 0, 0).getTime() / 1000,
    end: new Date(2026, 1, 4, 23, 59, 59, 999).getTime() / 1000,
  }));

  expect(frame.start_ts).toBeCloseTo(expected.start, 0);
  expect(frame.end_ts).toBeCloseTo(expected.end, 0);
});
