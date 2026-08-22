# Browser tests

Verifies the one thing no other test in this repo can: which HA frontend elements
a custom panel can actually reach. jsdom has no HA frontend, so only a real
instance can answer that, and the answer changes between HA releases rather than
through any change here.

| Spec | Question |
| ---- | -------- |
| `date-range-picker.spec.mjs` | Can `ha-date-range-picker` be loaded and driven? |
| `native-date-inputs.spec.mjs` | Is there always *some* working date control? |
| `recent-fixed-chips.spec.mjs` | Is `ha-assist-chip` available without any loading dance? |

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

## Which HA version

The suite runs against the current release by default, because that is what
users are on and frontend drift is what this suite exists to catch.

The version comes from the image tag, not from a pip install into a fixed image.
Each HA release requires a specific minimum Python - 2026.8.3 needs 3.14.2 - so
installing a newer HA into an older image just fails to resolve. Pin a version by
tag instead:

```bash
HA_VERSION=2026.8.0 npm run e2e
HA_VERSION=stable npm run e2e     # the default
```

The picker contract the panel depends on - `hass`, `startDate`, `endDate`,
`ranges`, `extendedPresets`, and a `value-changed` event carrying
`{value: {startDate, endDate}}` - has held since at least HA 2025.3.

Whether the picker can be loaded at all is a different question, and the answer
got worse. `window.loadCardHelpers` is what registers it, and HA only defines
that as a side effect of loading the Lovelace panel. A session that never opens a
dashboard never gets it, and on HA 2026.8 the default landing page is
`/home/overview` rather than a dashboard - so it is normally absent and the picker
cannot load. That is why the panel keeps native date inputs as its real control.

So the picker specs skip on an HA that does not expose `loadCardHelpers`. A skip
is a result, not an absence of one: it means users on that version get the native
inputs. Expect this on 2026.8 and the picker specs to run on 2025.3.

`ha-assist-chip`, used for the recently-fixed chips, needs no loading dance - it
ships in the main frontend bundle. `recent-fixed-chips.spec.mjs` is what tells us
if that stops being true.

## What is deliberately not covered

Scan, apply and restore. Those run in-process against a real recorder in
`tests_ha/test_data_path.py`, which is faster, needs no Docker, and can assert
exact numbers against the database. Keeping this suite to the frontend question
keeps it small enough to trust.

## Container notes

The official `ghcr.io/home-assistant/home-assistant` image is used so the HA
version and its Python stay in step. It does no onboarding of its own, so two
scripts stand in for that:

| Script | What it does |
| ------ | ------------ |
| `bootstrap.sh` | Writes `configuration.yaml` with the integration enabled, then hands off to the image's entrypoint. Needed because the integration is YAML-configured and HA only writes a default config on first start, which is too late. |
| `onboard.sh` | Creates the admin user and completes onboarding over HA's API, after the container reports healthy. `npm run up` runs it. |

Onboarding goes through the API rather than pre-seeded `.storage` files on
purpose: those files change shape between HA versions, which would defeat the
point of running against the current release. `onboard.sh` reads
`/api/onboarding` and completes whichever steps that HA reports outstanding, so a
new or removed step does not break it.
