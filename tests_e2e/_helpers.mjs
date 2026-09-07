/** Shared fixture for the browser specs. Not a spec itself (no *.spec.mjs). */

import { expect } from "@playwright/test";

const USERNAME = process.env.HASS_USERNAME || "dev";
const PASSWORD = process.env.HASS_PASSWORD || "dev";

export const PANEL_PATH = "/statistics-outlier-cleaner";
export const PANEL_TAG = "statistics-outlier-cleaner-panel";

/**
 * Give page.evaluate a way to reach the panel, several shadow roots down inside
 * HA's shell. Playwright locators pierce shadow DOM; raw DOM calls do not.
 */
export async function installDeepQuery(page) {
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

/**
 * Log in, then reach the panel the way a person does — from inside the app.
 *
 * A classic Lovelace view defines `window.loadCardHelpers`, which the panel uses
 * to lazy-load `ha-date-range-picker`. The current default dashboard does not
 * define it, so visit `/lovelace/0` first, then navigate to the panel in-app
 * (as a sidebar click would) so `window` — and the helper — survive.
 */
export async function openPanel(page) {
  // Ask for /lovelace/0 up front so the post-login redirect lands there — one
  // auth flow, no second navigation to race. A classic Lovelace view is what
  // defines window.loadCardHelpers.
  await page.goto("/lovelace/0");

  const username = page.locator('input[name="username"]');
  await expect(
    username.or(page.locator("home-assistant")).first()
  ).toBeAttached({ timeout: 90_000 });

  if (await username.count()) {
    await username.fill(USERNAME);
    await page.locator('input[name="password"]').fill(PASSWORD);
    await page.keyboard.press("Enter");
  }

  // Resolves once the redirect back to /lovelace/0 has finished loading and the
  // panel module has run.
  await page.waitForFunction(
    () => typeof window.loadCardHelpers === "function",
    null,
    { timeout: 90_000 }
  );

  // Reach the panel by clicking its sidebar entry — a real in-app navigation, so
  // window (and window.loadCardHelpers) carry over.
  await page.locator(`a[href="${PANEL_PATH}"]`).first().click();

  const handle = page.locator(PANEL_TAG);
  await handle.waitFor({ state: "attached", timeout: 90_000 });
  return handle;
}
