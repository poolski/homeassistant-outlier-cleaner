/**
 * Does ha-date-range-picker actually load inside a custom panel?
 *
 * The panel reaches it through window.loadCardHelpers(), which depends on HA
 * frontend internals: the energy date selection card's module registering
 * ha-date-range-picker as an import side effect. jsdom cannot answer whether
 * that still holds — only a real HA frontend can. That is the entire reason
 * this suite exists, so keep it to that question.
 *
 * The answer is version-dependent. window.loadCardHelpers is only defined as a
 * side effect of loading the Lovelace panel, so a session that never opens a
 * dashboard never gets it. On HA 2026.8 the default landing page is
 * /home/overview rather than a dashboard, so it is normally absent and the
 * picker cannot be loaded at all.
 *
 * The upgrade tests therefore skip when the frontend does not offer
 * loadCardHelpers, and `native-date-inputs.spec.mjs` covers the fallback that
 * every version gets. A skip here is a real result: it means users on this HA
 * are seeing the native inputs.
 */

import { expect, test } from "@playwright/test";

const PANEL_PATH = "/statistics-outlier-cleaner";
const PANEL_TAG = "statistics-outlier-cleaner-panel";
const PICKER_TAG = "ha-date-range-picker";

const USERNAME = process.env.HASS_USERNAME || "dev";
const PASSWORD = process.env.HASS_PASSWORD || "dev";

/** Log in through HA's own form and land on the panel. */
async function login(page) {
  await page.goto(PANEL_PATH);

  const username = page.locator('input[name="username"]');
  const panelEl = page.locator(PANEL_TAG);

  // Requesting the panel redirects to /auth/authorize, and that redirect plus
  // the frontend bundle take a while. Wait for whichever arrives: the login
  // form, or the panel itself if this context is already authenticated.
  await expect(username.or(panelEl).first()).toBeAttached({ timeout: 60_000 });

  if (!(await username.count())) return;

  await username.fill(USERNAME);
  await page.locator('input[name="password"]').fill(PASSWORD);
  await page.keyboard.press("Enter");
  await page.waitForURL(`**${PANEL_PATH}**`, { timeout: 60_000 });
}

/** The panel element, once HA has instantiated the custom panel. */
async function panel(page) {
  const handle = page.locator(PANEL_TAG);
  await handle.waitFor({ state: "attached", timeout: 60_000 });
  return handle;
}

/**
 * Give page.evaluate a way to reach the panel.
 *
 * The panel sits several shadow roots down inside HA's shell
 * (home-assistant > home-assistant-main > partial-panel-resolver >
 * ha-panel-custom), so a plain document.querySelector never finds it. Playwright
 * locators pierce shadow DOM; raw DOM calls inside evaluate do not.
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

/**
 * Wait until the app stops re-navigating.
 *
 * Logging in lands on `?auth_callback=1` and HA then navigates again to clean
 * the URL up. Each navigation builds a brand-new panel element, so anything
 * that asserts state survives over time has to start after the last one — or it
 * measures the login redirect rather than the panel.
 */
async function settle(page, quietMs = 2000) {
  let last = Date.now();
  const bump = (frame) => {
    if (frame === page.mainFrame()) last = Date.now();
  };
  page.on("framenavigated", bump);
  try {
    while (Date.now() - last < quietMs) {
      await page.waitForTimeout(250);
    }
  } finally {
    page.off("framenavigated", bump);
  }
  await panel(page);
}

test.beforeEach(async ({ page }) => {
  await installDeepQuery(page);
  await login(page);
  await panel(page);

  // Without loadCardHelpers the panel cannot register the picker, so there is
  // nothing here to test. Skipping says so out loud rather than failing as if
  // this were a regression in the panel.
  const available = await page.evaluate(
    () => typeof window.loadCardHelpers === "function"
  );
  test.skip(
    !available,
    "this HA does not expose window.loadCardHelpers, so the picker cannot load and the native inputs are what users get"
  );
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

test("picking a preset through the real UI updates the picker's own range", async ({
  page,
}) => {
  // The synthetic test above proves our listener works; it cannot prove the
  // element ever fires the event, nor that the selection sticks. The element is
  // controlled — it does not update its own startDate/endDate — so without the
  // panel writing the value back, the field keeps displaying the mounted range
  // and every `picker.hass` assignment re-renders that stale range.
  const read = () =>
    page.evaluate(
      ({ panelTag, pickerTag }) => {
        const picker = window
          .__deepQuery(panelTag)
          ?.shadowRoot?.querySelector(pickerTag);
        if (!picker) return null;
        return {
          start: picker.startDate?.toISOString?.(),
          end: picker.endDate?.toISOString?.(),
          label: picker.shadowRoot
            ?.querySelector("ha-textarea")
            ?.value?.replace(/\n/g, " "),
        };
      },
      { panelTag: PANEL_TAG, pickerTag: PICKER_TAG }
    );

  await settle(page);
  await expect.poll(async () => Boolean(await read()), { timeout: 20_000 }).toBe(true);
  const before = await read();

  // Open the dropdown via the field the element renders for that purpose.
  await page.locator(PICKER_TAG).locator("ha-textarea").first().click();

  const today = page.locator("mwc-list-item", { hasText: "Today" }).first();
  await expect(today).toBeVisible({ timeout: 20_000 });

  // Click by coordinate: Playwright's actionability retries race with the
  // dropdown's own open/close handling and can miss the item entirely.
  const box = await today.boundingBox();
  expect(box, "the Today preset should have a layout box").not.toBeNull();
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);

  await expect
    .poll(async () => (await read())?.start, {
      timeout: 20_000,
      message: "the selected range was never written back to the picker",
    })
    .not.toBe(before.start);

  const after = await read();

  // "Today" is a single local day.
  const expected = new Date();
  expected.setHours(0, 0, 0, 0);
  expect(new Date(after.start).getTime()).toBe(expected.getTime());

  // The field is what the user reads, so assert it moved too.
  expect(after.label).not.toBe(before.label);

  // The panel assigns picker.hass on every HA state update, which re-renders the
  // element from its own properties — the selection has to survive that.
  await page.waitForTimeout(2000);
  expect((await read()).start).toBe(after.start);
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
