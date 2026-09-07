# Browser tests

Verifies the one thing no other test in this repo can: that HA's own
`ha-date-range-picker` and `ha-entity-picker` really load inside a custom panel.

`ha-date-range-picker` in particular is lazy: the panel pulls it in through
`window.loadCardHelpers()`, which the frontend only defines once a classic
Lovelace view has loaded. So the fixture logs in, lands on `/lovelace/0`, then
opens the panel from the sidebar — an in-app navigation, the way a person
reaches it. That whole path depends on HA frontend internals and can break
through an HA release rather than through a change here. jsdom has no HA
frontend, so only a real instance can answer the question.

## Running

Needs Docker and Node.

```bash
npm install
npx playwright install chromium
npm run e2e
```

`npm run e2e` brings the container up, runs the tests, and tears it down again —
including the config volume, so each run starts clean. To keep the instance
around and iterate:

```bash
npm run up      # http://localhost:8123, logs in as dev/dev
npm test
npm run down
```

`npm run logs` tails Home Assistant if startup fails.

## Not in CI

Deliberate. Nothing in `.github/workflows` runs these, and nothing else in this
repo runs `pytest` or the `tests_js` suite either. Run this before a release, or
after an HA upgrade.

## Testing against a specific HA version

`HA_VERSION` is the official image tag and defaults to `stable` (the latest
release). Since HA frontend drift is exactly what this suite guards against, pin
it to reproduce a report:

```bash
HA_VERSION=2026.8.3 npm run e2e
```

The contracts the panel depends on:

- `ha-date-range-picker` — `startDate`, `endDate`, `ranges`, `extendedPresets`,
  and a `value-changed` event carrying `{value: {startDate, endDate}}`.
- `ha-entity-picker` — `includeEntities`, `allowCustomEntity`, `value`, and a
  `value-changed` event carrying `{value: "<entity_id>"}`.

Both have held since at least HA 2025.3. If a newer release breaks either, these
tests fail. The panel retries the load across HA state updates. In the meantime
the statistic field is a plain text input the user can type an id into, and a
scan runs on the default 30-day range until the date picker appears.

## What is deliberately not covered

Scan, apply and restore. Those run in-process against a real recorder in
`tests_ha/test_data_path.py`, which is faster, needs no Docker, and can assert
exact numbers against the database. Keeping this suite to the frontend question
keeps it small enough to trust.

## Container notes

Runs the official `ghcr.io/home-assistant/home-assistant` image. It has no
onboarding bypass, so `bootstrap.sh` stands in for one: `ensure_config`, then
`--script auth add` for the login user, then a `.storage/onboarding` file
marking the wizard done, then the `statistics_outlier_cleaner:` line in
`configuration.yaml`. Everything is idempotent, so `npm run up` can be re-run
against a kept config volume.

`global-setup.mjs` loads the frontend once before the suite so the first test
does not eat the cold-start cost — which matters most when the image is running
under emulation.
