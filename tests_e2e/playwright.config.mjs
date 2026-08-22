import { defineConfig, devices } from "@playwright/test";

const PORT = process.env.HA_PORT || "8123";

export default defineConfig({
  testDir: ".",
  testMatch: "*.spec.mjs",
  // Home Assistant is slow to boot and the panel loads its picker
  // asynchronously; individual assertions poll with their own timeouts.
  timeout: 120_000,
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
