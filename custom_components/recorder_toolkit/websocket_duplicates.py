"""WebSocket API handlers for Recorder Toolkit's duplicate-finder feature."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import (
    ATTR_CORRELATION_THRESHOLD,
    ATTR_LOOKBACK_DAYS,
    ATTR_MIN_OVERLAP,
    ATTR_NAME_THRESHOLD,
    DEFAULT_CORRELATION_THRESHOLD,
    DEFAULT_DUPLICATE_LOOKBACK_DAYS,
    DEFAULT_MIN_OVERLAP_POINTS,
    DEFAULT_NAME_SIMILARITY_THRESHOLD,
    WS_LIST_DUPLICATE_CANDIDATES,
)
from .duplicates import scan_duplicates

_LOGGER = logging.getLogger(__name__)


@callback
def async_register_duplicate_commands(hass: HomeAssistant) -> None:
    """Register duplicate-finder WebSocket commands."""
    websocket_api.async_register_command(hass, ws_list_duplicate_candidates)


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_LIST_DUPLICATE_CANDIDATES,
        vol.Optional(ATTR_LOOKBACK_DAYS, default=DEFAULT_DUPLICATE_LOOKBACK_DAYS): vol.All(
            int, vol.Range(min=1)
        ),
        vol.Optional(ATTR_NAME_THRESHOLD, default=DEFAULT_NAME_SIMILARITY_THRESHOLD): vol.All(
            vol.Coerce(float), vol.Range(min=0.0, max=1.0)
        ),
        vol.Optional(
            ATTR_CORRELATION_THRESHOLD, default=DEFAULT_CORRELATION_THRESHOLD
        ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=1.0)),
        vol.Optional(ATTR_MIN_OVERLAP, default=DEFAULT_MIN_OVERLAP_POINTS): vol.All(
            int, vol.Range(min=2)
        ),
    }
)
@websocket_api.async_response
async def ws_list_duplicate_candidates(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Scan for duplicate-recording entities and return grouped candidates."""
    groups = await scan_duplicates(
        hass,
        lookback_days=msg[ATTR_LOOKBACK_DAYS],
        name_threshold=msg[ATTR_NAME_THRESHOLD],
        correlation_threshold=msg[ATTR_CORRELATION_THRESHOLD],
        min_overlap=msg[ATTR_MIN_OVERLAP],
    )
    connection.send_result(
        msg["id"],
        {
            "groups": [
                {
                    "members": [
                        {
                            "entity_id": member.entity_id,
                            "row_count": member.row_count,
                            "earliest_start_ms": member.earliest_start_ms,
                        }
                        for member in group
                    ]
                }
                for group in groups
            ]
        },
    )
