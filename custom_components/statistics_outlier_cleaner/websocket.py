"""WebSocket API handlers for Statistics Outlier Cleaner."""

from __future__ import annotations

import logging
import sqlite3
import time
import uuid
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.components.recorder import get_instance
from homeassistant.core import HomeAssistant, callback

from .const import (
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_MAD_FACTOR,
    DEFAULT_METHOD,
    DEFAULT_PERIOD,
    DEFAULT_REPLACEMENT,
    DEFAULT_TOP_N,
    DOMAIN,
    WS_APPLY_FIX,
    WS_FETCH_OUTLIERS,
    WS_LIST_FIXES,
    WS_LIST_SUM_STATISTICS,
    WS_RESTORE_FIX,
)
from .db import (
    apply_fix_sync,
    ensure_backup_table,
    list_fixes_sync,
    resolve_metadata_id_sync,
    restore_fix_sync,
)
from .outlier import get_sum_statistic_ids, scan_outliers
from .paths import DatabaseNotSupportedError, resolve_sqlite_path

_LOGGER = logging.getLogger(__name__)


@callback
def async_register_commands(hass: HomeAssistant) -> None:
    """Register all WebSocket commands."""
    websocket_api.async_register_command(hass, ws_list_sum_statistics)
    websocket_api.async_register_command(hass, ws_fetch_outliers)
    websocket_api.async_register_command(hass, ws_apply_fix)
    websocket_api.async_register_command(hass, ws_list_fixes)
    websocket_api.async_register_command(hass, ws_restore_fix)


# ---------------------------------------------------------------------------
# list_sum_statistics
# ---------------------------------------------------------------------------


@websocket_api.websocket_command({vol.Required("type"): WS_LIST_SUM_STATISTICS})
@websocket_api.async_response
async def ws_list_sum_statistics(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return all statistic IDs that have a sum column."""
    stats = await get_sum_statistic_ids(hass)
    connection.send_result(msg["id"], {"statistics": stats})


# ---------------------------------------------------------------------------
# fetch_outliers
# ---------------------------------------------------------------------------


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_FETCH_OUTLIERS,
        vol.Required("statistic_id"): str,
        vol.Optional("period", default=DEFAULT_PERIOD): vol.In(
            ["hour", "5minute", "hybrid"]
        ),
        vol.Optional("method", default=DEFAULT_METHOD): vol.In(
            ["top_n", "absolute", "mad"]
        ),
        vol.Optional("top_n", default=DEFAULT_TOP_N): vol.All(int, vol.Range(min=1)),
        vol.Optional("threshold", default=0.0): vol.Coerce(float),
        vol.Optional("mad_factor", default=DEFAULT_MAD_FACTOR): vol.All(
            vol.Coerce(float), vol.Range(min=0.1)
        ),
        vol.Optional("lookback_days", default=DEFAULT_LOOKBACK_DAYS): vol.All(
            int, vol.Range(min=0)
        ),
        vol.Optional("start_ts"): vol.Any(None, vol.Coerce(float)),
        vol.Optional("end_ts"): vol.Any(None, vol.Coerce(float)),
    }
)
@websocket_api.async_response
async def ws_fetch_outliers(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Scan a statistic for outliers and return the report (read-only)."""
    try:
        report = await scan_outliers(
            hass,
            msg["statistic_id"],
            period=msg["period"],
            method=msg["method"],
            top_n=msg["top_n"],
            threshold=msg["threshold"],
            mad_factor=msg["mad_factor"],
            lookback_days=msg["lookback_days"],
            start_ts=msg.get("start_ts"),
            end_ts=msg.get("end_ts"),
        )
    except ValueError as exc:
        connection.send_error(msg["id"], websocket_api.ERR_INVALID_FORMAT, str(exc))
        return

    connection.send_result(msg["id"], report.to_dict())


# ---------------------------------------------------------------------------
# apply_fix
# ---------------------------------------------------------------------------


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_APPLY_FIX,
        vol.Required("statistic_id"): str,
        vol.Required("candidates"): [
            {
                vol.Required("start_ts"): vol.Coerce(float),
                vol.Required("period"): vol.In(["hour", "5minute"]),
            }
        ],
        vol.Optional("replacement", default=DEFAULT_REPLACEMENT): vol.Coerce(float),
        vol.Optional("dry_run", default=False): bool,
    }
)
@websocket_api.async_response
async def ws_apply_fix(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Apply a fix to one or more outlier candidates."""
    statistic_id: str = msg["statistic_id"]

    # Resolve metadata_id from the recorder
    metadata_id = await _resolve_metadata_id(hass, statistic_id)
    if metadata_id is None:
        connection.send_error(
            msg["id"],
            websocket_api.ERR_NOT_FOUND,
            f"No metadata for statistic_id {statistic_id!r}",
        )
        return

    try:
        db_path = _get_db_path(hass)
    except DatabaseNotSupportedError as exc:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_SUPPORTED, str(exc))
        return

    fix_id = str(uuid.uuid4())
    fix_ts = time.time()

    def _run_sync() -> dict[str, Any]:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            ensure_backup_table(conn)
            return apply_fix_sync(
                conn,
                statistic_id=statistic_id,
                metadata_id=metadata_id,
                candidates=msg["candidates"],
                replacement=msg["replacement"],
                fix_id=fix_id,
                fix_ts=fix_ts,
                dry_run=msg["dry_run"],
            )
        finally:
            conn.close()

    result = await get_instance(hass).async_add_executor_job(_run_sync)
    connection.send_result(msg["id"], result)


# ---------------------------------------------------------------------------
# list_fixes
# ---------------------------------------------------------------------------


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_LIST_FIXES,
        vol.Optional("limit", default=20): vol.All(int, vol.Range(min=1, max=200)),
    }
)
@websocket_api.async_response
async def ws_list_fixes(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return a summary of recent fixes."""
    try:
        db_path = _get_db_path(hass)
    except DatabaseNotSupportedError as exc:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_SUPPORTED, str(exc))
        return

    def _run_sync() -> list[dict[str, Any]]:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            ensure_backup_table(conn)
            return list_fixes_sync(conn, limit=msg["limit"])
        finally:
            conn.close()

    fixes = await get_instance(hass).async_add_executor_job(_run_sync)
    connection.send_result(msg["id"], {"fixes": fixes})


# ---------------------------------------------------------------------------
# restore_fix
# ---------------------------------------------------------------------------


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_RESTORE_FIX,
        vol.Required("fix_id"): str,
    }
)
@websocket_api.async_response
async def ws_restore_fix(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Restore the database rows backed up under fix_id."""
    try:
        db_path = _get_db_path(hass)
    except DatabaseNotSupportedError as exc:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_SUPPORTED, str(exc))
        return

    def _run_sync() -> dict[str, Any]:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            return restore_fix_sync(conn, msg["fix_id"])
        finally:
            conn.close()

    result = await get_instance(hass).async_add_executor_job(_run_sync)
    connection.send_result(msg["id"], result)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_db_path(hass: HomeAssistant) -> str:
    """Return the path to the recorder's SQLite database.

    Raises DatabaseNotSupportedError if the recorder is not on file-backed
    SQLite.
    """
    return resolve_sqlite_path(hass)


async def _resolve_metadata_id(
    hass: HomeAssistant, statistic_id: str
) -> int | None:
    """Return the integer metadata_id for a statistic_id.

    Tries the recorder ORM first (preferred — goes through the same session
    the recorder uses).  If the ORM API has moved in a newer HA release,
    falls back to a raw SQL query against statistics_meta.
    """
    recorder = get_instance(hass)

    def _fetch() -> int | None:
        try:
            from homeassistant.components.recorder.db_schema import (  # noqa: PLC0415
                StatisticsMeta,
            )

            with recorder.get_session() as session:
                row = (
                    session.query(StatisticsMeta.id)
                    .filter(StatisticsMeta.statistic_id == statistic_id)
                    .first()
                )
                return row[0] if row else None
        except Exception:  # noqa: BLE001
            _LOGGER.debug(
                "ORM metadata lookup failed for %r, falling back to raw SQL",
                statistic_id,
            )
            # Resolved here rather than up front so the normal ORM path never
            # depends on the database being file-backed SQLite.
            conn = sqlite3.connect(_get_db_path(hass))
            conn.row_factory = sqlite3.Row
            try:
                return resolve_metadata_id_sync(conn, statistic_id)
            finally:
                conn.close()

    return await recorder.async_add_executor_job(_fetch)
