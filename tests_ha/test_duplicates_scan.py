"""Integration tests for correlate_duplicate_group against a real in-process recorder."""

from __future__ import annotations

import pytest
from homeassistant.components.recorder.statistics import async_import_statistics
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.recorder_toolkit.duplicates import correlate_duplicate_group

KITCHEN_A = "sensor.kitchen_power_a"
KITCHEN_B = "sensor.kitchen_power_b"
ATTIC_TEMP = "sensor.attic_temperature"


def _hours(start: str, count: int, values: list[float]) -> list[dict]:
    base = dt_util.parse_datetime(start)
    return [
        {"start": base + dt_util.dt.timedelta(hours=i), "mean": values[i]}
        for i in range(count)
    ]


@pytest.fixture
async def seeded_hass(hass: HomeAssistant, recorder_mock, recorder_db_path):
    """Two correlated 'kitchen power' sensors and one unrelated (anti-
    correlated) sensor, all fed in as one fuzzy group's members to
    correlate_duplicate_group — it must confirm A+B and drop the unrelated
    one, without ever having been told they were name-similar beforehand.
    """
    values = [float(i) for i in range(24)]

    async_import_statistics(
        hass,
        {
            "has_mean": True,
            "has_sum": False,
            "name": "Kitchen Power A",
            "source": "recorder",
            "statistic_id": KITCHEN_A,
            "unit_of_measurement": "W",
        },
        _hours("2026-01-01T00:00:00+00:00", 24, values),
    )
    async_import_statistics(
        hass,
        {
            "has_mean": True,
            "has_sum": False,
            "name": "Kitchen Power B",
            "source": "recorder",
            "statistic_id": KITCHEN_B,
            "unit_of_measurement": "W",
        },
        # Same shape as A (perfectly correlated), fewer rows => A ranks first.
        _hours("2026-01-01T00:00:00+00:00", 12, values[:12]),
    )
    async_import_statistics(
        hass,
        {
            "has_mean": True,
            "has_sum": False,
            "name": "Attic Temperature",
            "source": "recorder",
            "statistic_id": ATTIC_TEMP,
            "unit_of_measurement": "W",
        },
        _hours("2026-01-01T00:00:00+00:00", 24, [v * -1 for v in values]),
    )
    from pytest_homeassistant_custom_component.components.recorder.common import (
        async_wait_recording_done,
    )

    await async_wait_recording_done(hass)
    return hass


async def test_correlate_duplicate_group_confirms_correlated_members(seeded_hass):
    # B has fewer rows than A (12 vs 24), below the default 20-point overlap
    # floor, so min_overlap is lowered here to isolate this test to the
    # ranking-by-completeness behavior rather than the overlap cutoff.
    groups = await correlate_duplicate_group(
        seeded_hass,
        [KITCHEN_A, KITCHEN_B, ATTIC_TEMP],
        lookback_days=3650,
        min_overlap=10,
    )
    assert len(groups) == 1
    group = groups[0]
    assert [m.entity_id for m in group] == [KITCHEN_A, KITCHEN_B]
    assert group[0].row_count == 24
    assert group[1].row_count == 12


async def test_correlate_duplicate_group_drops_anti_correlated_member(seeded_hass):
    groups = await correlate_duplicate_group(
        seeded_hass,
        [KITCHEN_A, KITCHEN_B, ATTIC_TEMP],
        lookback_days=3650,
        min_overlap=10,
    )
    grouped_ids = {m.entity_id for group in groups for m in group}
    assert ATTIC_TEMP not in grouped_ids


FRIDGE = "sensor.garage_fridge_energy"
CHARGER = "sensor.garage_charger_energy"


@pytest.fixture
async def sum_class_hass(hass: HomeAssistant, recorder_mock, recorder_db_path):
    """Two unrelated sum-class (cumulative) meters whose raw readings both
    trend upward and would misleadingly correlate if compared directly.
    """
    base = dt_util.parse_datetime("2026-01-01T00:00:00+00:00")
    fridge_increments = [1.0, 1.0, 1.0, 1.0] * 8
    charger_increments = [0.0, 0.0, 5.0, 12.0] * 8

    def _cumulative_rows(increments: list[float]) -> list[dict]:
        rows = []
        total = 0.0
        for i, delta in enumerate(increments):
            total += delta
            rows.append({"start": base + dt_util.dt.timedelta(hours=i), "state": total})
        return rows

    async_import_statistics(
        hass,
        {
            "has_mean": False,
            "has_sum": True,
            "name": "Garage Fridge Energy",
            "source": "recorder",
            "statistic_id": FRIDGE,
            "unit_of_measurement": "kWh",
        },
        _cumulative_rows(fridge_increments),
    )
    async_import_statistics(
        hass,
        {
            "has_mean": False,
            "has_sum": True,
            "name": "Garage Charger Energy",
            "source": "recorder",
            "statistic_id": CHARGER,
            "unit_of_measurement": "kWh",
        },
        _cumulative_rows(charger_increments),
    )
    from pytest_homeassistant_custom_component.components.recorder.common import (
        async_wait_recording_done,
    )

    await async_wait_recording_done(hass)
    return hass


async def test_correlate_duplicate_group_does_not_confirm_unrelated_sum_class_meters(
    sum_class_hass,
):
    groups = await correlate_duplicate_group(
        sum_class_hass, [FRIDGE, CHARGER], lookback_days=3650, min_overlap=10
    )
    assert groups == []
