import { defineConfig, devices } from "@playwright/test";

const PORT = process.env.HA_PORT || "8123";

export default defineConfig({
  testDir: ".",
  testMatch: "*.spec.mjs",
  // Loads the frontend once so the first test does not pay the cold-start cost.
  globalSetup: "./global-setup.mjs",
  // Home Assistant is slow to boot (slower still under emulation) and the panel
  // loads its picker asynchronously; individual assertions poll with their own
  // timeouts.
  timeout: 180_000,
  expect: { timeout: 20_000 },
  // Run on demand, not in CI, so fail fast and don't paper over flakes.
  retries: 0,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    ...devices["Desktop Chrome"],
  },
});
