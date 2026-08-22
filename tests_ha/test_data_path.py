"""
End-to-end tests for the scan -> apply -> restore data path.

These drive the real WebSocket commands against a real recorder backed by a
real SQLite file, which is the only configuration where this integration does
anything at all: it fixes statistics over its own sqlite3 connection, so an
in-memory recorder (the PHACC default) is not a usable substitute. See the
`recorder_db_url` override in conftest.

The fixture is the bounded-spike pattern from the README — a jump up followed by
a compensating drop — because that is the case where cascading a `sum`
correction forward is easy to get subtly wrong.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
)
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

DOMAIN = "statistics_outlier_cleaner"
STATISTIC_ID = "outlier_test:spiky_energy"

BASE = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
HOURS = 48
SPIKE_HOUR = 24
NORMAL_CHANGE = 1.0
SPIKE_CHANGE = 500.0
# Chosen so the two anomalous hours together contribute exactly what two normal
# hours would (500 - 498 == 2), which is what makes this a *bounded* spike: the
# running sum is back on trend afterwards even though both rows are wrong.
DROP_CHANGE = -498.0


def hourly_changes() -> list[float]:
    """Per-hour `change` values: a clean ramp with one bounded spike in it."""
    changes = [NORMAL_CHANGE] * HOURS
    changes[SPIKE_HOUR] = SPIKE_CHANGE
    changes[SPIKE_HOUR + 1] = DROP_CHANGE
    return changes


def cumulative(changes: list[float]) -> list[float]:
    """Running total, which is what the `sum` column holds."""
    out: list[float] = []
    total = 0.0
    for change in changes:
        total += change
        out.append(total)
    return out


def clean_sums() -> list[float]:
    """The sums the series would have had if the spike had never happened."""
    return cumulative([NORMAL_CHANGE] * HOURS)


def spiked_sums() -> list[float]:
    """The sums as seeded."""
    return cumulative(hourly_changes())


def read_sums(db_path: str, statistic_id: str) -> list[float]:
    """Read the `sum` column straight from the recorder database, in time order."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT s.sum FROM statistics s
               JOIN statistics_meta m ON m.id = s.metadata_id
               WHERE m.statistic_id = ?
               ORDER BY s.start_ts""",
            (statistic_id,),
        ).fetchall()
    finally:
        conn.close()
    return [row["sum"] for row in rows]


def read_backup_rows(db_path: str, fix_id: str) -> list[dict[str, Any]]:
    """Read the integration's own backup table, which HA knows nothing about."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT * FROM statistics_outlier_cleaner_backup
               WHERE fix_id = ? ORDER BY start_ts""",
            (fix_id,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


@pytest.fixture
async def spiky(
    hass: HomeAssistant,
    recorder_mock,
    recorder_db_path: str,
    socket_enabled,
):
    """Component set up, with a 48-hour series containing a bounded spike."""
    assert await async_setup_component(hass, DOMAIN, {DOMAIN: {}})
    await hass.async_block_till_done()

    async_add_external_statistics(
        hass,
        {
            "has_mean": False,
            "has_sum": True,
            "name": "Spiky energy",
            "source": "outlier_test",
            "statistic_id": STATISTIC_ID,
            "unit_of_measurement": "kWh",
        },
        [
            {"start": BASE + timedelta(hours=hour), "state": total, "sum": total}
            for hour, total in enumerate(spiked_sums())
        ],
    )
    await async_wait_recording_done(hass)

    # Guard the fixture itself: if seeding silently did nothing, every assertion
    # below would pass for the wrong reason.
    assert read_sums(recorder_db_path, STATISTIC_ID) == pytest.approx(spiked_sums())
    return hass


async def scan(ws, threshold: float = 100.0) -> dict[str, Any]:
    """Run fetch_outliers over the seeded statistic."""
    await ws.send_json(
        {
            "id": 1,
            "type": f"{DOMAIN}/fetch_outliers",
            "statistic_id": STATISTIC_ID,
            "period": "hour",
            "method": "absolute",
            "threshold": threshold,
        }
    )
    result = await ws.receive_json()
    assert result["success"], result
    return result["result"]


class TestScan:
    """fetch_outliers is read-only and must flag exactly the bad rows."""

    async def test_flags_both_rows_of_the_bounded_spike(
        self, spiky: HomeAssistant, hass_ws_client
    ) -> None:
        ws = await hass_ws_client(spiky)
        report = await scan(ws)

        flagged = sorted(c["change"] for c in report["candidates"])

        assert flagged == pytest.approx([DROP_CHANGE, SPIKE_CHANGE])
        assert report["scanned_rows"] == HOURS

    async def test_scanning_does_not_modify_anything(
        self, spiky: HomeAssistant, hass_ws_client, recorder_db_path: str
    ) -> None:
        ws = await hass_ws_client(spiky)
        await scan(ws)

        assert read_sums(recorder_db_path, STATISTIC_ID) == pytest.approx(
            spiked_sums()
        )


class TestApplyAndRestore:
    """The destructive path: cascade a correction forward, then undo it."""

    async def test_dry_run_changes_nothing(
        self, spiky: HomeAssistant, hass_ws_client, recorder_db_path: str
    ) -> None:
        ws = await hass_ws_client(spiky)
        report = await scan(ws)

        await ws.send_json(
            {
                "id": 2,
                "type": f"{DOMAIN}/apply_fix",
                "statistic_id": STATISTIC_ID,
                "candidates": [
                    {"start_ts": c["start"] / 1000.0, "period": c["period"]}
                    for c in report["candidates"]
                ],
                "replacement": NORMAL_CHANGE,
                "dry_run": True,
            }
        )
        result = (await ws.receive_json())["result"]

        assert result["planned"] == 2
        assert result["applied"] == 0
        assert result["queries"], "a dry run should report the SQL it would run"
        assert read_sums(recorder_db_path, STATISTIC_ID) == pytest.approx(
            spiked_sums()
        )
        assert read_backup_rows(recorder_db_path, result["fix_id"]) == []

    async def test_apply_cascades_the_correction_then_restore_undoes_it(
        self, spiky: HomeAssistant, hass_ws_client, recorder_db_path: str
    ) -> None:
        ws = await hass_ws_client(spiky)
        report = await scan(ws)
        candidates = [
            {"start_ts": c["start"] / 1000.0, "period": c["period"]}
            for c in report["candidates"]
        ]

        await ws.send_json(
            {
                "id": 2,
                "type": f"{DOMAIN}/apply_fix",
                "statistic_id": STATISTIC_ID,
                "candidates": candidates,
                "replacement": NORMAL_CHANGE,
            }
        )
        applied = (await ws.receive_json())["result"]
        fix_id = applied["fix_id"]

        assert applied["applied"] == 2
        assert applied["errors"] == []

        # The whole point of the integration: not just the spike row, but every
        # subsequent row's running total. Replacing both anomalous changes with
        # a normal one should leave the series indistinguishable from clean.
        assert read_sums(recorder_db_path, STATISTIC_ID) == pytest.approx(
            clean_sums()
        )

        # Every mutated row was backed up first.
        backup = read_backup_rows(recorder_db_path, fix_id)
        assert backup, "apply must back up before mutating"
        assert {row["statistic_id"] for row in backup} == {STATISTIC_ID}

        # list_fixes surfaces it to the panel's history card.
        await ws.send_json({"id": 3, "type": f"{DOMAIN}/list_fixes"})
        fixes = (await ws.receive_json())["result"]["fixes"]
        assert fix_id in {fix["fix_id"] for fix in fixes}

        # And restore puts the original values back, spike included.
        await ws.send_json(
            {"id": 4, "type": f"{DOMAIN}/restore_fix", "fix_id": fix_id}
        )
        restored = (await ws.receive_json())["result"]

        assert restored["restored"] > 0
        assert restored["errors"] == []
        assert read_sums(recorder_db_path, STATISTIC_ID) == pytest.approx(
            spiked_sums()
        )

    async def test_apply_writes_to_the_recorders_database_not_the_config_dir(
        self, spiky: HomeAssistant, hass_ws_client, recorder_db_path: str
    ) -> None:
        """Regression test for the hardcoded database path.

        recorder_db_url points somewhere hass.config.path() could never produce,
        so if apply ever goes back to guessing the path, the recorder's database
        stays untouched and this fails.
        """
        ws = await hass_ws_client(spiky)
        report = await scan(ws)

        await ws.send_json(
            {
                "id": 2,
                "type": f"{DOMAIN}/apply_fix",
                "statistic_id": STATISTIC_ID,
                "candidates": [
                    {"start_ts": c["start"] / 1000.0, "period": c["period"]}
                    for c in report["candidates"]
                ],
                "replacement": NORMAL_CHANGE,
            }
        )
        assert (await ws.receive_json())["result"]["applied"] == 2

        assert read_sums(recorder_db_path, STATISTIC_ID) == pytest.approx(
            clean_sums()
        )
