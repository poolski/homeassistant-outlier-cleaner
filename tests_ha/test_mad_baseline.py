"""
MAD scans over a short range compare against trailing history.

MAD judges each row against rows at the same time of day. A scan range shorter
than a day holds each slot once, so without history from before the range there
is nothing to compare against and no spike can be flagged.
"""

from __future__ import annotations

import math
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

DOMAIN = "recorder_toolkit"
STATISTIC_ID = "outlier_test:baseline_energy"

BASE = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
DAYS = 15
SPIKE_HOUR = (DAYS - 1) * 24 + 20
SPIKE_CHANGE = 500.0
SCAN_START = BASE + timedelta(days=DAYS - 1, hours=18)
SCAN_END = BASE + timedelta(days=DAYS)


def hourly_changes() -> list[float]:
    changes = [1.0 + 0.1 * math.sin(i) for i in range(DAYS * 24)]
    changes[SPIKE_HOUR] = SPIKE_CHANGE
    return changes


@pytest.fixture
async def seeded(hass: HomeAssistant, recorder_mock, socket_enabled):
    assert await async_setup_component(hass, DOMAIN, {DOMAIN: {}})
    await hass.async_block_till_done()

    total = 0.0
    rows = []
    for hour, change in enumerate(hourly_changes()):
        total += change
        rows.append({"start": BASE + timedelta(hours=hour), "state": total, "sum": total})

    async_add_external_statistics(
        hass,
        {
            "has_mean": False,
            "has_sum": True,
            "name": "Baseline energy",
            "source": "outlier_test",
            "statistic_id": STATISTIC_ID,
            "unit_of_measurement": "kWh",
        },
        rows,
    )
    await async_wait_recording_done(hass)
    return hass


async def scan(ws, **extra: Any) -> dict[str, Any]:
    await ws.send_json(
        {
            "id": 1,
            "type": f"{DOMAIN}/fetch_outliers",
            "statistic_id": STATISTIC_ID,
            "period": "hour",
            "method": "mad",
            "mad_factor": 6.0,
            "start_ts": SCAN_START.timestamp(),
            "end_ts": SCAN_END.timestamp(),
            "suggest_lookback_days": 0,
            **extra,
        }
    )
    result = await ws.receive_json()
    assert result["success"], result
    return result["result"]


async def test_short_range_flags_spike_using_baseline(
    seeded: HomeAssistant, hass_ws_client
) -> None:
    report = await scan(await hass_ws_client(seeded))

    assert [c["change"] for c in report["candidates"]] == pytest.approx([SPIKE_CHANGE])
    # Only rows inside the requested range are counted as scanned.
    assert report["scanned_rows"] == 6


async def test_zero_baseline_days_keeps_scan_range_only(
    seeded: HomeAssistant, hass_ws_client
) -> None:
    report = await scan(await hass_ws_client(seeded), baseline_days=0)

    assert report["candidates"] == []
