/**
 * Warm the frontend before the suite runs.
 *
 * A cold Home Assistant — especially the image running under emulation — serves
 * /manifest.json (what the compose healthcheck waits on) well before the SPA
 * bundle and service worker are ready. Without this, the first test pays that
 * cost and times out. Load the app and a Lovelace view once here so every test
 * starts warm.
 */

import { chromium } from "@playwright/test";

const BASE = `http://localhost:${process.env.HA_PORT || "8123"}`;

export default async function globalSetup() {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  try {
    for (let attempt = 0; attempt < 30; attempt++) {
      try {
        await page.goto(BASE, { timeout: 30_000 });
        await page.locator("body").waitFor({ timeout: 10_000 });
        break;
      } catch {
        await page.waitForTimeout(3_000);
      }
    }
    await page.goto(`${BASE}/lovelace/0`, { timeout: 60_000 }).catch(() => {});
    await page.waitForTimeout(5_000);
  } finally {
    await browser.close();
  }
}
