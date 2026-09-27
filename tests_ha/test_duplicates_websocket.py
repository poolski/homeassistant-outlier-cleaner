"""Integration tests for the list_duplicate_candidates websocket command."""

from __future__ import annotations

import pytest
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.recorder_toolkit.const import (
    WS_GENERATE_EXCLUDE_YAML,
    WS_LIST_DUPLICATE_CANDIDATES,
)
from custom_components.recorder_toolkit.websocket_duplicates import (
    async_register_duplicate_commands,
)

STAT_A = "wstest:kitchen_power_a"
STAT_B = "wstest:kitchen_power_b"


@pytest.fixture
async def seeded_hass(hass: HomeAssistant, recorder_mock, recorder_db_path):
    values = [float(i) for i in range(24)]
    rows = [
        {"start": dt_util.parse_datetime("2026-01-01T00:00:00+00:00") + dt_util.dt.timedelta(hours=i), "mean": values[i]}
        for i in range(24)
    ]
    for statistic_id, name in ((STAT_A, "Kitchen Power A"), (STAT_B, "Kitchen Power B")):
        async_add_external_statistics(
            hass,
            {
                "has_mean": True,
                "has_sum": False,
                "name": name,
                "source": "wstest",
                "statistic_id": statistic_id,
                "unit_of_measurement": "W",
            },
            rows,
        )
    await async_wait_recording_done(hass)
    async_register_duplicate_commands(hass)
    return hass


async def test_list_duplicate_candidates_returns_grouped_result(
    seeded_hass, hass_ws_client
):
    client = await hass_ws_client(seeded_hass)
    await client.send_json(
        {"id": 1, "type": WS_LIST_DUPLICATE_CANDIDATES, "lookback_days": 3650}
    )
    response = await client.receive_json()
    assert response["success"]
    groups = response["result"]["groups"]
    assert len(groups) == 1
    member_ids = [m["entity_id"] for m in groups[0]["members"]]
    assert member_ids == [STAT_A, STAT_B]


async def test_generate_exclude_yaml_excludes_non_kept_members(
    seeded_hass, hass_ws_client
):
    client = await hass_ws_client(seeded_hass)
    await client.send_json(
        {
            "id": 2,
            "type": WS_GENERATE_EXCLUDE_YAML,
            "group_selections": [
                {"members": [STAT_A, STAT_B], "keep": STAT_A},
            ],
        }
    )
    response = await client.receive_json()
    assert response["success"]
    assert STAT_B in response["result"]["yaml"]
    assert STAT_A not in response["result"]["yaml"]


async def test_generate_exclude_yaml_honours_user_override_of_suggested_keep(
    seeded_hass, hass_ws_client
):
    client = await hass_ws_client(seeded_hass)
    # User picked STAT_B to keep instead of the (unspecified-here) default.
    await client.send_json(
        {
            "id": 3,
            "type": WS_GENERATE_EXCLUDE_YAML,
            "group_selections": [
                {"members": [STAT_A, STAT_B], "keep": STAT_B},
            ],
        }
    )
    response = await client.receive_json()
    assert response["success"]
    assert STAT_A in response["result"]["yaml"]
    assert STAT_B not in response["result"]["yaml"]
