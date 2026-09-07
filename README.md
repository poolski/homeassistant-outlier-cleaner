# Home Assistant Statistics Outlier Cleanup

[![HACS Badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/release/poolski/homeassistant-outlier-cleaner.svg)](https://github.com/poolski/homeassistant-outlier-cleaner/releases)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=poolski&repository=homeassistant-outlier-cleaner&category=integration)

A Home Assistant custom integration for detecting and fixing outlier spikes in long-term statistics — the kind caused by HA restarts, meter replacements, or recorder compaction bugs.

Unlike the built-in Developer Tools > Statistics dialog, this integration:

- Cascades the `sum` correction forward across all subsequent rows, eliminating the spike without re-injection after the next HA restart
- Supports **scheduled automation** via a service action (safe detection methods: MAD, absolute threshold)
- **Backs up** every affected row before mutating, with a one-click restore
- Handles the **bounded spike** pattern (a jump up followed by a compensating drop) by letting you select and fix both rows in one operation

> [!WARNING]
> This integration writes directly to the Home Assistant SQLite recorder database. It only supports SQLite (the default). MariaDB / PostgreSQL are not supported.

---

## Installation

### Via HACS (recommended)

1. Open HACS → **Integrations** → three-dot menu → **Custom repositories**, add this repo URL with category **Integration**.
2. Search for **Statistics Outlier Cleaner** and click **Download**.
3. Add one line to your `configuration.yaml`:

   ```yaml
   statistics_outlier_cleaner:
   ```

4. Restart Home Assistant. The **Outlier Cleaner** entry will appear in the sidebar.

### Manual

1. Copy the `custom_components/statistics_outlier_cleaner` directory into your HA config folder:

   ```text
   <config>/custom_components/statistics_outlier_cleaner/
   ```

2. Add one line to `configuration.yaml`:

   ```yaml
   statistics_outlier_cleaner:
   ```

3. Restart Home Assistant.

---

## Usage

### Sidebar panel

After installation a new **Outlier Cleaner** entry appears in the sidebar (admin only).

1. Pick a statistic from the entity picker (only sum-capable statistics are offered; recent picks show as chips above it).
2. Select a date range.
3. Choose a detection method and click **Scan**.
4. Check the rows you want to fix. Nothing is selected for you — a scan returns suggestions, and `top_n` in particular always returns N rows whether or not the data is clean. Set a replacement change value (default `0` removes the spike entirely).
5. Click **Apply Fix to Selected**. The fix is recorded in the history table below with its fix ID.
6. To undo, click **Restore** next to the relevant history entry.

### Service action

For scheduled or automation use, call `statistics_outlier_cleaner.clean_outliers`:

```yaml
action: statistics_outlier_cleaner.clean_outliers
data:
  statistic_id: sensor.electricity_meter_energy
  method: mad          # mad | absolute | top_n
  mad_factor: 6        # higher = more conservative
  period: hybrid       # hybrid | hour | 5minute
  lookback_days: 30    # 0 = all time
  replacement: 0       # set flagged change to this value
  dry_run: false
```

To restore a previous fix:

```yaml
action: statistics_outlier_cleaner.restore_fix
data:
  fix_id: "3f2504e0-4f89-11d3-9a0c-0305e82c3301"
```

The fix ID is logged at INFO level after every `clean_outliers` call.

---

## Detection methods

Three methods are available. Two are safe to use in automations; one is manual-only.

| Method | When to use | Safe for automation? |
| --- | --- | --- |
| `mad` | General-purpose spike detection. Conservative — returns nothing on flat data. | ✅ Yes |
| `absolute` | You know the maximum plausible change for your sensor. | ✅ Yes |
| `top_n` | Matches the built-in dev tools behaviour. **Always** returns N rows, even if the data is clean. | ⚠️ Manual only |

---

### `mad` — Median Absolute Deviation

**How it works:** Computes the [modified z-score](https://www.itl.nist.gov/div898/handbook/eda/section3/eda35h.htm) for every row's `change` value. Rows whose score exceeds `mad_factor` are flagged.

Rather than comparing against one global median, each row is judged against **rows at the same time of day** (±5 min for hourly, ±30 s for 5-minute). This matters for sensors with a daily rhythm: 18:00 on a Tuesday is compared with 18:00 on other days, not with 03:00. The row being tested is excluded from its own baseline, so a large spike cannot drag the median toward itself and mask its own detection.

**Key property:** MAD does not flag normal variation — on clean data it returns nothing, which is what makes it safe for scheduled automations. When a sensor's baseline is genuinely flat (a solar panel's night hours are all exactly `0.0`), there is no spread to compute a z-score from; such rows are instead flagged only if they exceed the sensor's normal operating magnitude by more than 100×. That keeps flat-baseline sensors from producing false positives while still catching the impossible values a restart spike produces.

**Parameter:** `mad_factor` (default `6`). Higher values are more conservative — only extreme outliers are flagged. Lower values cast a wider net.

| `mad_factor` | Behaviour |
| --- | --- |
| `3` | Aggressive — flags moderate spikes, may catch normal variation |
| `6` | Balanced (recommended) — catches clear spikes, ignores noise |
| `10` | Conservative — only flags very large spikes |

**Example — electricity meter, automation use:**

```yaml
action: statistics_outlier_cleaner.clean_outliers
data:
  statistic_id: sensor.electricity_meter_energy
  method: mad
  mad_factor: 6
  lookback_days: 30
  replacement: 0
```

A typical electricity meter accumulates ~0.5–2 kWh/h. A restart spike of 500 kWh would have a modified z-score in the thousands — flagged immediately. A slightly noisy hour at 2.5 kWh when the median is 1.2 kWh would not be flagged.

---

### `absolute` — Fixed threshold

**How it works:** Flags any row where `|change| ≥ threshold`. Simple and predictable — if you know the physical maximum your sensor can legitimately register in one period, set `threshold` just above it.

**When to use it:** Sensors with a well-defined physical ceiling. For example:
- A solar inverter that can output at most 10 kW → max 10 kWh in an hour → `threshold: 10`
- A gas meter where 5 m³/h is physically impossible → `threshold: 5`
- A water meter where 200 litres in 5 minutes is implausible → `threshold: 200`

**Example — solar inverter, safe automation:**

```yaml
action: statistics_outlier_cleaner.clean_outliers
data:
  statistic_id: sensor.solar_inverter_energy
  method: absolute
  threshold: 15      # inverter is rated 8 kW; 15 kWh/h is impossible
  lookback_days: 90
  replacement: 0
```

Any hour showing more than 15 kWh of generation is flagged. Normal peaks (say 7 kWh on a sunny afternoon) are never touched.

---

### `top_n` — Largest N changes

**How it works:** Sorts all rows by `|change|` descending and returns the top N — regardless of whether they are actually unusual.

**Why this is dangerous in automations:** On a clean dataset with no real spikes, `top_n` will still return N rows and flag them for fixing. This is identical to the behaviour of the built-in Developer Tools → Statistics dialog. It is useful for manual inspection ("show me the 10 biggest changes so I can decide") but should never run unattended.

**The `clean_outliers` service intentionally rejects `top_n`** to prevent accidental data loss. Use it only via the sidebar panel where you can review results before applying.

**Example — manual inspection in the panel:**

1. Open the panel, select your sensor, set method to **Top N**, enter `10`.
2. Click **Scan**. The 10 largest changes are shown.
3. Review each row. Deselect any that look legitimate.
4. Set a replacement value and click **Apply Fix to Selected**.

---

## How it works

Home Assistant stores long-term statistics in two SQLite tables:

- `statistics` — one row per hour (LTS)
- `statistics_short_term` — one row per 5 minutes (STS)

Each row has a cumulative `sum` and an instantaneous `state`. A spike manifests as a large `change` (= `sum[i] − sum[i−1]`) on one row.

When a fix is applied:

1. **Backup** — the spike row and all subsequent rows in both tables are copied to a backup table keyed by a `fix_id` UUID.
2. **Cascade** — `sum` is adjusted by `delta = replacement − change` on every row from the spike onwards (STS first, then LTS, to stay consistent with HA's own compilation order).

To reverse: the backup rows are written back by row ID, then deleted from the backup table.

---

## Restart-hour spikes (bounded spikes)

The most common pattern is a jump **up** on the restart hour immediately followed by a compensating drop. Both rows should be selected together when applying a fix — the cascading deltas cancel out for all rows after the pair, leaving subsequent data unaffected.

---

## Requirements

- Home Assistant 2024.1 or later
- SQLite recorder (default)
- Admin access for the sidebar panel

---

## Limitations

- **SQLite only.** The direct database write path does not support MariaDB or PostgreSQL.
- **No chart.** The panel shows a table of flagged rows rather than a graph. Use the built-in Statistics developer tool to visualise before and after.
- The `top_n` method is intentionally excluded from the `clean_outliers` service to prevent accidental data loss in automations.
