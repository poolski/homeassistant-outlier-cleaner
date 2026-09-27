"""Integration tests for the duplicate-finder websocket commands."""

from __future__ import annotations

import pytest
from homeassistant.components.recorder.statistics import async_import_statistics
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.recorder_toolkit.const import (
    WS_CORRELATE_DUPLICATE_GROUP,
    WS_GENERATE_EXCLUDE_YAML,
    WS_LIST_DUPLICATE_CANDIDATES,
)
from custom_components.recorder_toolkit.websocket_duplicates import (
    async_register_duplicate_commands,
)

STAT_A = "sensor.kitchen_power_a"
STAT_B = "sensor.kitchen_power_b"


@pytest.fixture
async def seeded_hass(hass: HomeAssistant, recorder_mock, recorder_db_path):
    values = [float(i) for i in range(24)]
    rows = [
        {"start": dt_util.parse_datetime("2026-01-01T00:00:00+00:00") + dt_util.dt.timedelta(hours=i), "mean": values[i]}
        for i in range(24)
    ]
    for statistic_id, name in ((STAT_A, "Kitchen Power A"), (STAT_B, "Kitchen Power B")):
        hass.states.async_set(
            statistic_id,
            "1.0",
            {"unit_of_measurement": "W", "state_class": "measurement", "friendly_name": name},
        )
        async_import_statistics(
            hass,
            {
                "has_mean": True,
                "has_sum": False,
                "name": name,
                "source": "recorder",
                "statistic_id": statistic_id,
                "unit_of_measurement": "W",
            },
            rows,
        )
    await async_wait_recording_done(hass)
    async_register_duplicate_commands(hass)
    return hass


async def test_list_duplicate_candidates_returns_fuzzy_grouped_result(
    seeded_hass, hass_ws_client
):
    client = await hass_ws_client(seeded_hass)
    await client.send_json({"id": 1, "type": WS_LIST_DUPLICATE_CANDIDATES})
    response = await client.receive_json()
    assert response["success"]
    groups = response["result"]["groups"]
    assert len(groups) == 1
    assert groups[0]["members"] == [STAT_A, STAT_B]


async def test_correlate_duplicate_group_confirms_and_returns_stats(
    seeded_hass, hass_ws_client
):
    client = await hass_ws_client(seeded_hass)
    await client.send_json(
        {
            "id": 2,
            "type": WS_CORRELATE_DUPLICATE_GROUP,
            "members": [STAT_A, STAT_B],
            "lookback_days": 3650,
        }
    )
    response = await client.receive_json()
    assert response["success"]
    groups = response["result"]["groups"]
    assert len(groups) == 1
    member_ids = [m["entity_id"] for m in groups[0]["members"]]
    assert member_ids == [STAT_A, STAT_B]
    assert groups[0]["members"][0]["row_count"] == 24


async def test_generate_exclude_yaml_excludes_non_kept_members(
    seeded_hass, hass_ws_client
):
    client = await hass_ws_client(seeded_hass)
    await client.send_json(
        {
            "id": 3,
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
            "id": 4,
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


async def test_generate_exclude_yaml_glob_never_matches_a_kept_entity_missing_from_states(
    seeded_hass, hass_ws_client
):
    # sensor.power_1 (kept) has no current state — e.g. temporarily
    # unavailable or not yet loaded — so it's invisible to a safety check
    # that only looks at hass.states. The glob that covers power_2/power_3
    # ("sensor.power_*") also matches "sensor.power_1" as a string pattern,
    # so accepting it would silently stop recording the entity the user
    # chose to keep.
    seeded_hass.states.async_set("sensor.power_2", "1", {"unit_of_measurement": "W"})
    seeded_hass.states.async_set("sensor.power_3", "2", {"unit_of_measurement": "W"})

    client = await hass_ws_client(seeded_hass)
    await client.send_json(
        {
            "id": 5,
            "type": WS_GENERATE_EXCLUDE_YAML,
            "group_selections": [
                {
                    "members": ["sensor.power_1", "sensor.power_2", "sensor.power_3"],
                    "keep": "sensor.power_1",
                },
            ],
        }
    )
    response = await client.receive_json()
    assert response["success"]
    yaml_text = response["result"]["yaml"]
    assert "entity_globs" not in yaml_text
    assert "sensor.power_2" in yaml_text
    assert "sensor.power_3" in yaml_text
    assert "sensor.power_1" not in yaml_text


async def test_generate_exclude_yaml_rejects_keep_not_in_members(
    seeded_hass, hass_ws_client
):
    client = await hass_ws_client(seeded_hass)
    await client.send_json(
        {
            "id": 6,
            "type": WS_GENERATE_EXCLUDE_YAML,
            "group_selections": [
                # "keep" is a typo/stale id, not one of this group's members —
                # excluding every member (including the one the user meant to
                # keep) with no error would be worse than rejecting outright.
                {"members": [STAT_A, STAT_B], "keep": "sensor.does_not_exist"},
            ],
        }
    )
    response = await client.receive_json()
    assert not response["success"]
