"""Integration tests for scan_duplicates against a real in-process recorder."""

from __future__ import annotations

import pytest
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.recorder_toolkit.duplicates import scan_duplicates

KITCHEN_A = "duptest:kitchen_power_a"
KITCHEN_B = "duptest:kitchen_power_b"
ATTIC_TEMP = "duptest:attic_temperature"


def _hours(start: str, count: int, values: list[float]) -> list[dict]:
    base = dt_util.parse_datetime(start)
    return [
        {"start": base + dt_util.dt.timedelta(hours=i), "mean": values[i]}
        for i in range(count)
    ]


@pytest.fixture
async def seeded_hass(hass: HomeAssistant, recorder_mock, recorder_db_path):
    """Two correlated 'kitchen power' sensors (same unit, similar name) and
    one unrelated sensor sharing the unit but a dissimilar name/value shape.
    """
    values = [float(i) for i in range(24)]

    async_add_external_statistics(
        hass,
        {
            "has_mean": True,
            "has_sum": False,
            "name": "Kitchen Power A",
            "source": "duptest",
            "statistic_id": KITCHEN_A,
            "unit_of_measurement": "W",
        },
        _hours("2026-01-01T00:00:00+00:00", 24, values),
    )
    async_add_external_statistics(
        hass,
        {
            "has_mean": True,
            "has_sum": False,
            "name": "Kitchen Power B",
            "source": "duptest",
            "statistic_id": KITCHEN_B,
            "unit_of_measurement": "W",
        },
        # Same shape as A (perfectly correlated), fewer rows => A ranks first.
        _hours("2026-01-01T00:00:00+00:00", 12, values[:12]),
    )
    async_add_external_statistics(
        hass,
        {
            "has_mean": True,
            "has_sum": False,
            "name": "Attic Temperature",
            "source": "duptest",
            "statistic_id": ATTIC_TEMP,
            "unit_of_measurement": "W",  # same unit on purpose: name must reject this
        },
        _hours("2026-01-01T00:00:00+00:00", 24, [v * -1 for v in values]),
    )
    from pytest_homeassistant_custom_component.components.recorder.common import (
        async_wait_recording_done,
    )

    await async_wait_recording_done(hass)
    return hass


async def test_scan_duplicates_groups_correlated_same_named_sensors(seeded_hass):
    # B has fewer rows than A (12 vs 24), below the default 20-point overlap
    # floor, so min_overlap is lowered here to isolate this test to the
    # ranking-by-completeness behavior rather than the overlap cutoff
    # (covered separately in tests/test_duplicates.py).
    groups = await scan_duplicates(seeded_hass, lookback_days=3650, min_overlap=10)
    assert len(groups) == 1
    group = groups[0]
    assert [m.entity_id for m in group] == [KITCHEN_A, KITCHEN_B]
    assert group[0].row_count == 24
    assert group[1].row_count == 12


async def test_scan_duplicates_does_not_group_dissimilar_names(seeded_hass):
    groups = await scan_duplicates(seeded_hass, lookback_days=3650, min_overlap=10)
    grouped_ids = {m.entity_id for group in groups for m in group}
    assert ATTIC_TEMP not in grouped_ids
