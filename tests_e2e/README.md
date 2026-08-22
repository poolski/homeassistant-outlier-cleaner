# Browser tests

Verifies the one thing no other test in this repo can: that
`ha-date-range-picker` really loads inside a custom panel.

The panel reaches it via `window.loadCardHelpers()`, relying on the energy date
selection card's module registering the element as an import side effect. That
depends on Home Assistant frontend internals, so it can break through an HA
release rather than through a change here. jsdom has no HA frontend, so only a
real instance can answer the question.

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

The image pins its own Home Assistant. Since HA frontend drift is exactly what
this suite guards against, override it:

```bash
HA_VERSION=2026.8.0 npm run e2e
```

The picker contract the panel depends on — `hass`, `startDate`, `endDate`,
`ranges`, `extendedPresets`, and a `value-changed` event carrying
`{value: {startDate, endDate}}` — has held since at least HA 2025.3. If a newer
release breaks it, these tests fail. There is no fallback control — the panel
retries the load across HA state updates, and a scan still runs on the default
30-day range in the meantime.

## What is deliberately not covered

Scan, apply and restore. Those run in-process against a real recorder in
`tests_ha/test_data_path.py`, which is faster, needs no Docker, and can assert
exact numbers against the database. Keeping this suite to the frontend question
keeps it small enough to trust.

## Container notes

The image (`thomasloven/hass-custom-devcontainer`) is used only for its
bootstrap: it generates a config, creates an admin user, and skips onboarding.
Its Lovelace plugin support is irrelevant here, since the integration serves its
own panel over a static path.

`bootstrap.sh` splits the image's one-shot entrypoint into `setup` -> edit
`configuration.yaml` -> `launch`, because the integration is YAML-configured and
that file does not exist until setup has run.
