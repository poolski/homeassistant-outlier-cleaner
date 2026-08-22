"""
Tests for the raw-SQL fallback in metadata_id resolution.

Both `websocket._resolve_metadata_id` and `__init__._resolve_metadata_id` try
the recorder ORM first and fall back to raw SQL when it raises. The fallback in
websocket.py was unreachable: the handler called `_LOGGER.debug()` in a module
that never defined `_LOGGER`, so any ORM failure raised NameError instead of
falling back. Nothing caught it because the ORM path succeeds in normal use.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.statistics_outlier_cleaner import (
    _resolve_metadata_id as init_resolve_metadata_id,
)
from custom_components.statistics_outlier_cleaner.websocket import (
    _resolve_metadata_id as ws_resolve_metadata_id,
)

DOMAIN = "statistics_outlier_cleaner"
STATISTIC_ID = "outlier_test:fallback_probe"


@pytest.fixture
async def seeded_hass(hass: HomeAssistant, recorder_mock, recorder_db_path):
    """A hass with one external statistic recorded.

    Deliberately does not call async_setup_component: metadata resolution needs
    only the recorder, and setting the component up registers the panel, which
    drags in the http server and a real socket bind.
    """
    async_add_external_statistics(
        hass,
        {
            "has_mean": False,
            "has_sum": True,
            "name": "Fallback probe",
            "source": "outlier_test",
            "statistic_id": STATISTIC_ID,
            "unit_of_measurement": "kWh",
        },
        [
            {
                "start": dt_util.parse_datetime("2026-01-01T00:00:00+00:00"),
                "state": 1.0,
                "sum": 1.0,
            }
        ],
    )
    await async_wait_recording_done(hass)
    return hass


@pytest.mark.parametrize(
    ("resolver", "label"),
    [
        (ws_resolve_metadata_id, "websocket"),
        (init_resolve_metadata_id, "__init__"),
    ],
    ids=["websocket", "init"],
)
async def test_falls_back_to_raw_sql_when_the_orm_raises(
    seeded_hass: HomeAssistant, resolver, label: str
) -> None:
    """An ORM failure must fall back to raw SQL, not raise."""
    hass = seeded_hass

    # Establish the expected answer via the working ORM path first.
    expected = await resolver(hass, STATISTIC_ID)
    assert expected is not None, "ORM path should resolve the seeded statistic"

    # Break the session the ORM branch depends on. Before the fix this raised
    # NameError from the except handler instead of falling back.
    recorder = get_instance(hass)
    with patch.object(
        type(recorder), "get_session", side_effect=RuntimeError("ORM moved")
    ):
        resolved = await resolver(hass, STATISTIC_ID)

    assert resolved == expected, (
        f"{label}: raw-SQL fallback should return the same metadata_id as the ORM"
    )


async def test_returns_none_for_an_unknown_statistic(
    seeded_hass: HomeAssistant,
) -> None:
    """A statistic the recorder has never seen resolves to None, not an error."""
    assert await ws_resolve_metadata_id(seeded_hass, "outlier_test:nope") is None
