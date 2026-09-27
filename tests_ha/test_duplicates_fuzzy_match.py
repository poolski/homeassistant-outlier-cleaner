"""Integration tests for find_fuzzy_duplicate_groups.

Deliberately does NOT use the `recorder_mock`/`recorder_db_path` fixtures —
the whole point of this phase is that it never touches the recorder or
statistics tables, only live entity state.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from custom_components.recorder_toolkit.duplicates import find_fuzzy_duplicate_groups

KITCHEN_A = "sensor.kitchen_power_a"
KITCHEN_B = "sensor.kitchen_power_b"
ATTIC_TEMP = "sensor.attic_temperature"


async def test_find_fuzzy_duplicate_groups_groups_same_unit_similar_name(
    hass: HomeAssistant,
):
    hass.states.async_set(
        KITCHEN_A, "1.0", {"unit_of_measurement": "W", "state_class": "measurement", "friendly_name": "Kitchen Power A"}
    )
    hass.states.async_set(
        KITCHEN_B, "2.0", {"unit_of_measurement": "W", "state_class": "measurement", "friendly_name": "Kitchen Power B"}
    )
    hass.states.async_set(
        ATTIC_TEMP, "20.0", {"unit_of_measurement": "W", "state_class": "measurement", "friendly_name": "Attic Temperature"}
    )

    groups = find_fuzzy_duplicate_groups(hass)

    assert groups == [[KITCHEN_A, KITCHEN_B]]


async def test_find_fuzzy_duplicate_groups_skips_entities_without_state_class(
    hass: HomeAssistant,
):
    hass.states.async_set(
        KITCHEN_A, "1.0", {"unit_of_measurement": "W", "state_class": "measurement", "friendly_name": "Kitchen Power A"}
    )
    # No state_class at all — not a statistics-eligible sensor.
    hass.states.async_set(
        KITCHEN_B, "2.0", {"unit_of_measurement": "W", "friendly_name": "Kitchen Power B"}
    )

    groups = find_fuzzy_duplicate_groups(hass)

    assert groups == []


async def test_find_fuzzy_duplicate_groups_ignores_non_sensor_domains(
    hass: HomeAssistant,
):
    hass.states.async_set(
        KITCHEN_A, "1.0", {"unit_of_measurement": "W", "state_class": "measurement", "friendly_name": "Kitchen Power A"}
    )
    hass.states.async_set(
        "switch.kitchen_power_a", "on", {"unit_of_measurement": "W", "state_class": "measurement", "friendly_name": "Kitchen Power A"}
    )

    groups = find_fuzzy_duplicate_groups(hass)

    assert groups == []
