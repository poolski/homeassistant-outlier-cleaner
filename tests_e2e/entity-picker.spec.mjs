/**
 * Does ha-entity-picker actually load inside a custom panel?
 *
 * The panel reaches it through window.loadCardHelpers(): building the entities
 * card and asking it for its config element imports ha-entity-picker as a side
 * effect. That depends on HA frontend internals, so only a real HA frontend can
 * confirm it still holds. jsdom cannot, which is why this suite exists — keep it
 * to that question.
 */

import { expect, test } from "@playwright/test";

const PANEL_PATH = "/statistics-outlier-cleaner";
const PANEL_TAG = "statistics-outlier-cleaner-panel";
const PICKER_TAG = "ha-entity-picker";

const USERNAME = process.env.HASS_USERNAME || "dev";
const PASSWORD = process.env.HASS_PASSWORD || "dev";

/** Log in through HA's own form and land on the panel. */
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

async function panel(page) {
  const handle = page.locator(PANEL_TAG);
  await handle.waitFor({ state: "attached", timeout: 60_000 });
  return handle;
}

/**
 * Give page.evaluate a way to reach the panel, several shadow roots down inside
 * HA's shell. Playwright locators pierce shadow DOM; raw DOM calls do not.
 */
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
  await panel(page);
});

test("the panel registers ha-entity-picker via loadCardHelpers", async ({
  page,
}) => {
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
