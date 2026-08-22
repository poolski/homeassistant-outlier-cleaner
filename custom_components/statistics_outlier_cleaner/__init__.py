"""Statistics Outlier Cleaner integration."""

from __future__ import annotations

import logging
import os
import sqlite3
import time
import uuid
from typing import Any

import voluptuous as vol

from homeassistant.components.recorder import get_instance
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.typing import ConfigType

from .const import (
    ATTR_DRY_RUN,
    ATTR_FIX_ID,
    ATTR_LOOKBACK_DAYS,
    ATTR_MAD_FACTOR,
    ATTR_METHOD,
    ATTR_PERIOD,
    ATTR_REPLACEMENT,
    ATTR_STATISTIC_ID,
    ATTR_THRESHOLD,
    ATTR_TOP_N,
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_MAD_FACTOR,
    DEFAULT_METHOD,
    DEFAULT_PERIOD,
    DEFAULT_REPLACEMENT,
    DEFAULT_TOP_N,
    DOMAIN,
    PANEL_ICON,
    PANEL_STATIC_PATH,
    PANEL_TITLE,
    PANEL_URL_PATH,
    PANEL_WEBCOMPONENT,
    SERVICE_CLEAN_OUTLIERS,
    SERVICE_RESTORE_FIX,
)
from .db import apply_fix_sync, ensure_backup_table, resolve_metadata_id_sync, restore_fix_sync
from .outlier import scan_outliers
from .paths import DatabaseNotSupportedError, resolve_sqlite_path
from .websocket import async_register_commands

_LOGGER = logging.getLogger(__name__)

# Accept `statistics_outlier_cleaner:` in configuration.yaml with no options.
CONFIG_SCHEMA = vol.Schema({DOMAIN: vol.Schema({})}, extra=vol.ALLOW_EXTRA)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up Statistics Outlier Cleaner."""
    _check_sqlite_dialect(hass)
    async_register_commands(hass)
    await _register_panel(hass)
    _register_services(hass)
    return True


# ---------------------------------------------------------------------------
# Panel registration
# ---------------------------------------------------------------------------


async def _register_panel(hass: HomeAssistant) -> None:
    from homeassistant.components.frontend import async_register_built_in_panel  # noqa: PLC0415
    from homeassistant.components.http import StaticPathConfig  # noqa: PLC0415

    frontend_dir = os.path.join(os.path.dirname(__file__), "frontend")
    await hass.http.async_register_static_paths(
        [StaticPathConfig(PANEL_STATIC_PATH, frontend_dir, cache_headers=False)]
    )
    async_register_built_in_panel(
        hass,
        component_name="custom",
        sidebar_title=PANEL_TITLE,
        sidebar_icon=PANEL_ICON,
        frontend_url_path=PANEL_URL_PATH,
        config={
            "_panel_custom": {
                "name": PANEL_WEBCOMPONENT,
                "js_url": f"{PANEL_STATIC_PATH}/{PANEL_WEBCOMPONENT}.js",
                "embed_iframe": False,
                "trust_external_script": False,
            }
        },
        require_admin=True,
    )


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------


def _register_services(hass: HomeAssistant) -> None:
    async def handle_clean_outliers(call: ServiceCall) -> None:
        statistic_id: str = call.data[ATTR_STATISTIC_ID]
        period: str = call.data.get(ATTR_PERIOD, DEFAULT_PERIOD)
        method: str = call.data.get(ATTR_METHOD, DEFAULT_METHOD)
        top_n: int = call.data.get(ATTR_TOP_N, DEFAULT_TOP_N)
        threshold: float = call.data.get(ATTR_THRESHOLD, 0.0)
        mad_factor: float = call.data.get(ATTR_MAD_FACTOR, DEFAULT_MAD_FACTOR)
        lookback_days: int = call.data.get(ATTR_LOOKBACK_DAYS, DEFAULT_LOOKBACK_DAYS)
        replacement: float = call.data.get(ATTR_REPLACEMENT, DEFAULT_REPLACEMENT)
        dry_run: bool = call.data.get(ATTR_DRY_RUN, False)

        try:
            report = await scan_outliers(
                hass,
                statistic_id,
                period=period,
                method=method,
                top_n=top_n,
                threshold=threshold,
                mad_factor=mad_factor,
                lookback_days=lookback_days,
            )
        except ValueError as exc:
            raise HomeAssistantError(str(exc)) from exc

        if not report.candidates:
            _LOGGER.info(
                "clean_outliers: no outliers found for %s (scanned %d rows)",
                statistic_id,
                report.scanned_rows,
            )
            return

        # OutlierCandidate.start is ms-epoch; apply_fix_sync wants seconds float
        candidates = [
            {"start_ts": c.start / 1000.0, "period": c.period}
            for c in report.candidates
        ]

        metadata_id = await _resolve_metadata_id(hass, statistic_id)
        if metadata_id is None:
            raise HomeAssistantError(
                f"No recorder metadata found for {statistic_id!r}"
            )

        try:
            db_path = resolve_sqlite_path(hass)
        except DatabaseNotSupportedError as exc:
            raise HomeAssistantError(str(exc)) from exc

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
                    candidates=candidates,
                    replacement=replacement,
                    fix_id=fix_id,
                    fix_ts=fix_ts,
                    dry_run=dry_run,
                )
            finally:
                conn.close()

        result = await get_instance(hass).async_add_executor_job(_run_sync)
        _LOGGER.info(
            "clean_outliers: %s — fix_id=%s planned=%d applied=%d dry_run=%s errors=%s",
            statistic_id,
            fix_id,
            result["planned"],
            result["applied"],
            dry_run,
            result["errors"],
        )

    async def handle_restore_fix(call: ServiceCall) -> None:
        fix_id: str = call.data[ATTR_FIX_ID]
        try:
            db_path = resolve_sqlite_path(hass)
        except DatabaseNotSupportedError as exc:
            raise HomeAssistantError(str(exc)) from exc

        def _run_sync() -> dict[str, Any]:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                return restore_fix_sync(conn, fix_id)
            finally:
                conn.close()

        result = await get_instance(hass).async_add_executor_job(_run_sync)
        if result["restored"] == 0 and not result["errors"]:
            raise HomeAssistantError(f"No backup found for fix_id {fix_id!r}")
        _LOGGER.info(
            "restore_fix: fix_id=%s restored=%d errors=%s",
            fix_id,
            result["restored"],
            result["errors"],
        )

    hass.services.async_register(DOMAIN, SERVICE_CLEAN_OUTLIERS, handle_clean_outliers)
    hass.services.async_register(DOMAIN, SERVICE_RESTORE_FIX, handle_restore_fix)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _check_sqlite_dialect(hass: HomeAssistant) -> None:
    """Warn at startup if the recorder database cannot be modified.

    Best-effort only: this runs during setup, which may be before the recorder
    is ready. Failing to determine the database here is not an error — every
    operation that actually writes resolves the path again and reports properly.
    """
    try:
        db_path = resolve_sqlite_path(hass)
    except DatabaseNotSupportedError as exc:
        _LOGGER.warning("Statistics Outlier Cleaner: %s", exc)
        return
    except Exception:  # noqa: BLE001 - recorder not up yet; not our problem here
        _LOGGER.debug(
            "Statistics Outlier Cleaner: recorder not available at setup, "
            "deferring database checks"
        )
        return

    if not os.path.isfile(db_path):
        _LOGGER.warning(
            "Statistics Outlier Cleaner: SQLite database not found at %s. "
            "Direct database fixes require a SQLite recorder.",
            db_path,
        )


async def _resolve_metadata_id(
    hass: HomeAssistant, statistic_id: str
) -> int | None:
    """Return the integer metadata_id for a statistic_id.

    Tries the recorder ORM first; falls back to a raw SQL query if the ORM
    API has moved in a newer HA release.
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
            conn = sqlite3.connect(resolve_sqlite_path(hass))
            conn.row_factory = sqlite3.Row
            try:
                return resolve_metadata_id_sync(conn, statistic_id)
            finally:
                conn.close()

    return await recorder.async_add_executor_job(_fetch)
