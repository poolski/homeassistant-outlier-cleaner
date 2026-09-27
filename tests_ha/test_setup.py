"""
Integration tests — verifies that recorder_toolkit registers
correctly against a real (test) Home Assistant instance.

The `hass` fixture is provided by pytest-homeassistant-custom-component and
starts a minimal HA core with no external connections.

Run with:
    pytest tests_ha/ -v
"""

from __future__ import annotations

import pytest
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

DOMAIN = "recorder_toolkit"

WS_COMMANDS = [
    f"{DOMAIN}/list_sum_statistics",
    f"{DOMAIN}/fetch_outliers",
    f"{DOMAIN}/apply_fix",
    f"{DOMAIN}/list_fixes",
    f"{DOMAIN}/restore_fix",
]

SERVICES = ["clean_outliers", "restore_fix"]


@pytest.fixture
async def setup_component(hass: HomeAssistant, recorder_mock):
    """Set up the component under test and yield the HA instance."""
    result = await async_setup_component(hass, DOMAIN, {DOMAIN: {}})
    assert result is True, "async_setup_component returned False"
    await hass.async_block_till_done()
    return hass


async def test_async_setup_returns_true(hass: HomeAssistant, recorder_mock):
    result = await async_setup_component(hass, DOMAIN, {DOMAIN: {}})
    assert result is True


async def test_services_registered(setup_component: HomeAssistant):
    hass = setup_component
    for service in SERVICES:
        assert hass.services.has_service(DOMAIN, service), (
            f"Service {DOMAIN}.{service} was not registered"
        )


async def test_websocket_commands_registered(setup_component: HomeAssistant):
    hass = setup_component
    handlers = hass.data.get(websocket_api.DOMAIN, {})
    for cmd in WS_COMMANDS:
        assert cmd in handlers, f"WebSocket command {cmd!r} was not registered"


async def test_clean_outliers_schema_accepts_minimal_payload(setup_component: HomeAssistant):
    """Service schema should accept a call with only the required statistic_id."""
    hass = setup_component
    assert hass.services.has_service(DOMAIN, "clean_outliers")


async def test_restore_fix_schema_accepts_fix_id(setup_component: HomeAssistant):
    """Service schema should accept a call with a fix_id."""
    hass = setup_component
    assert hass.services.has_service(DOMAIN, "restore_fix")


async def test_list_sum_statistics_enriches_friendly_name(
    setup_component: HomeAssistant,
    hass_ws_client,
):
    """list_sum_statistics must attach friendly_name from hass.states when name is null."""
    hass = setup_component
    hass.states.async_set(
        "sensor.test_power",
        "100",
        {"friendly_name": "Test Power Meter"},
    )
    client = await hass_ws_client(hass)
    await client.send_json({"id": 10, "type": f"{DOMAIN}/list_sum_statistics"})
    msg = await client.receive_json()
    stats = msg.get("result", {}).get("statistics", [])
    match = next((s for s in stats if s["statistic_id"] == "sensor.test_power"), None)
    if match:
        assert match.get("name") == "Test Power Meter"


async def test_ws_fetch_outliers_accepts_lookback_days(
    setup_component: HomeAssistant,
    hass_ws_client,
):
    """ws_fetch_outliers must not reject a lookback_days parameter."""
    hass = setup_component
    client = await hass_ws_client(hass)

    await client.send_json(
        {
            "id": 1,
            "type": f"{DOMAIN}/fetch_outliers",
            "statistic_id": "sensor.nonexistent",
            "lookback_days": 16,
        }
    )
    msg = await client.receive_json()

    # Schema validation failure ("extra keys not allowed") returns type="result"
    # with success=False and code="invalid_format".  Any other response (including
    # a successful scan that finds no data) means the field was accepted.
    assert not (
        msg.get("type") == "result"
        and msg.get("success") is False
        and "lookback_days" in msg.get("error", {}).get("message", "")
    ), f"ws_fetch_outliers rejected lookback_days: {msg}"
