/**
 * Do the recently-fixed chips render as real HA chips inside a custom panel?
 *
 * The jsdom tests cover the derivation — dedup, ordering, the cap, click
 * handling — against a stub element. What they cannot answer is whether
 * ha-assist-chip is actually defined for a custom panel. Unlike
 * ha-date-range-picker it ships in HA's main bundle rather than a lazily loaded
 * card chunk, so the panel uses it directly with no loading dance. That is an
 * assumption about frontend internals, and it can break through an HA release
 * rather than through a change here — which is what this file guards.
 */

import { expect, test } from "@playwright/test";

const PANEL_PATH = "/statistics-outlier-cleaner";
const PANEL_TAG = "statistics-outlier-cleaner-panel";
const CHIP_TAG = "ha-assist-chip";

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

test("ha-assist-chip is available to a custom panel without any loading dance", async ({
  page,
}) => {
  // No lazy-load path is involved: if this is ever false, the panel silently
  // falls back to plain buttons and this suite is how we find out.
  await expect
    .poll(() => page.evaluate((t) => Boolean(customElements.get(t)), CHIP_TAG), {
      timeout: 20_000,
      message: `${CHIP_TAG} is not defined for a custom panel`,
    })
    .toBe(true);
});

test("fixed sensors render as real chips and select the statistic when clicked", async ({
  page,
}) => {
  // Drive the panel from its own fix history so the test does not depend on the
  // container having any statistics of its own.
  const rendered = await page.evaluate(
    ({ panelTag }) => {
      const panel = window.__deepQuery(panelTag);
      panel._allStats = [
        { statistic_id: "sensor.solar", name: "Solar Production" },
        { statistic_id: "sensor.gas", name: "Gas Meter" },
      ];
      panel._fixes = [
        { fix_id: "1", statistic_id: "sensor.solar", fix_ts: 300, row_count: 1 },
        { fix_id: "2", statistic_id: "sensor.gas", fix_ts: 200, row_count: 1 },
        // A repeat must not take a second slot.
        { fix_id: "3", statistic_id: "sensor.solar", fix_ts: 100, row_count: 1 },
      ];
      panel._renderRecentChips();

      const chips = [
        ...panel.shadowRoot.querySelectorAll("#recent-chips [data-statistic-id]"),
      ];
      return {
        rowHidden: panel.shadowRoot
          .getElementById("recent-chips-row")
          .classList.contains("hidden"),
        tags: chips.map((c) => c.tagName.toLowerCase()),
        ids: chips.map((c) => c.dataset.statisticId),
        labels: chips.map((c) => c.getAttribute("label")),
      };
    },
    { panelTag: PANEL_TAG }
  );

  expect(rendered.rowHidden).toBe(false);
  expect(rendered.tags).toEqual([CHIP_TAG, CHIP_TAG]);
  expect(rendered.ids).toEqual(["sensor.solar", "sensor.gas"]);
  expect(rendered.labels).toEqual(["Solar Production", "Gas Meter"]);

  // The chip must be a real, laid-out control — a custom element that failed to
  // upgrade would still be in the DOM but have no size and show no label.
  const firstChip = page.locator(`${PANEL_TAG} #recent-chips ${CHIP_TAG}`).first();
  await expect(firstChip).toBeVisible();
  const box = await firstChip.boundingBox();
  expect(box.height).toBeGreaterThan(20);
  expect(box.width).toBeGreaterThan(40);
  await expect(firstChip).toHaveText(/Solar Production/);

  // Clicking hands the statistic to the scan form.
  await firstChip.click();

  const selected = await page.evaluate(
    ({ panelTag }) => {
      const panel = window.__deepQuery(panelTag);
      const root = panel.shadowRoot;
      return {
        statId: panel._statId,
        name: root.getElementById("stat-selected-name").textContent,
        displayHidden: root
          .getElementById("stat-selected")
          .classList.contains("hidden"),
      };
    },
    { panelTag: PANEL_TAG }
  );

  expect(selected.statId).toBe("sensor.solar");
  expect(selected.name).toBe("Solar Production");
  expect(selected.displayHidden).toBe(false);
});
