"""Constants for the Statistics Outlier Cleaner integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "statistics_outlier_cleaner"

PANEL_URL_PATH: Final = "statistics-outlier-cleaner"
PANEL_TITLE: Final = "Outlier Cleaner"
PANEL_ICON: Final = "mdi:chart-bell-curve-cumulative"
PANEL_WEBCOMPONENT: Final = "statistics-outlier-cleaner-panel"
PANEL_STATIC_PATH: Final = "/statistics_outlier_cleaner_static"

# Service / action names
SERVICE_CLEAN_OUTLIERS: Final = "clean_outliers"
SERVICE_RESTORE_FIX: Final = "restore_fix"

# Service call parameters
ATTR_STATISTIC_ID: Final = "statistic_id"
ATTR_PERIOD: Final = "period"  # "hour" | "5minute" | "hybrid"
ATTR_METHOD: Final = "method"  # "top_n" | "absolute" | "mad"
ATTR_TOP_N: Final = "top_n"
ATTR_THRESHOLD: Final = "threshold"
ATTR_MAD_FACTOR: Final = "mad_factor"
ATTR_LOOKBACK_DAYS: Final = "lookback_days"
ATTR_SUGGEST_LOOKBACK_DAYS: Final = "suggest_lookback_days"
ATTR_REPLACEMENT: Final = "replacement"
ATTR_DRY_RUN: Final = "dry_run"
ATTR_FIX_ID: Final = "fix_id"

# WebSocket command types
WS_LIST_SUM_STATISTICS: Final = f"{DOMAIN}/list_sum_statistics"
WS_FETCH_OUTLIERS: Final = f"{DOMAIN}/fetch_outliers"
WS_APPLY_FIX: Final = f"{DOMAIN}/apply_fix"
WS_LIST_FIXES: Final = f"{DOMAIN}/list_fixes"
WS_RESTORE_FIX: Final = f"{DOMAIN}/restore_fix"

# Defaults
DEFAULT_TOP_N: Final = 10
DEFAULT_MAD_FACTOR: Final = 6.0
DEFAULT_LOOKBACK_DAYS: Final = 0  # 0 == no limit
DEFAULT_AUTO_FIX_LOOKBACK_DAYS: Final = 7
DEFAULT_REPLACEMENT: Final = 0.0
DEFAULT_PERIOD: Final = "hybrid"
DEFAULT_METHOD: Final = "top_n"
