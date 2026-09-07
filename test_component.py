"""
Smoke tests for the Statistics Outlier Cleaner component.

Validates that all required files are present, manifests and service
schemas are well-formed, and the JavaScript panel contains the expected
identifiers.  No Home Assistant instance required.

Can be run directly:
    python test_component.py

Or via pytest (runs as part of the normal test suite):
    pytest test_component.py
"""

from __future__ import annotations

import json
import logging
import os
import sys

import pytest
import yaml

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE = os.path.join(os.path.dirname(__file__), "custom_components", "statistics_outlier_cleaner")


# ---------------------------------------------------------------------------
# File structure
# ---------------------------------------------------------------------------


REQUIRED_FILES = [
    "__init__.py",
    "const.py",
    "db.py",
    "manifest.json",
    "outlier.py",
    "services.yaml",
    "websocket.py",
    "frontend/statistics-outlier-cleaner-panel.js",
]


def test_required_files_exist():
    missing = [f for f in REQUIRED_FILES if not os.path.exists(os.path.join(BASE, f))]
    assert not missing, f"Missing files: {missing}"


# ---------------------------------------------------------------------------
# manifest.json
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def manifest() -> dict:
    with open(os.path.join(BASE, "manifest.json")) as fh:
        return json.load(fh)


def test_manifest_required_keys(manifest):
    for key in ("domain", "name", "version", "documentation", "dependencies"):
        assert key in manifest, f"manifest.json missing '{key}'"


def test_manifest_domain(manifest):
    assert manifest["domain"] == "statistics_outlier_cleaner"


def test_manifest_no_config_flow(manifest):
    assert manifest.get("config_flow") is False, (
        "config_flow should be false — component loads via configuration.yaml"
    )


def test_manifest_required_dependencies(manifest):
    deps = manifest.get("dependencies", [])
    for required in ("recorder", "frontend", "http", "websocket_api"):
        assert required in deps, f"manifest.json missing dependency '{required}'"


def test_manifest_version_format(manifest):
    parts = manifest["version"].split(".")
    assert len(parts) == 3 and all(p.isdigit() for p in parts), (
        f"version should be semver (x.y.z), got {manifest['version']!r}"
    )


# ---------------------------------------------------------------------------
# services.yaml
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def services() -> dict:
    with open(os.path.join(BASE, "services.yaml")) as fh:
        return yaml.safe_load(fh)


def test_services_defines_clean_outliers(services):
    assert "clean_outliers" in services, "services.yaml missing 'clean_outliers'"


def test_services_defines_restore_fix(services):
    assert "restore_fix" in services, "services.yaml missing 'restore_fix'"


def test_clean_outliers_has_statistic_id_field(services):
    fields = services["clean_outliers"].get("fields", {})
    assert "statistic_id" in fields


def test_restore_fix_has_fix_id_field(services):
    fields = services["restore_fix"].get("fields", {})
    assert "fix_id" in fields


# ---------------------------------------------------------------------------
# Frontend JS panel
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def panel_js() -> str:
    with open(os.path.join(BASE, "frontend", "statistics-outlier-cleaner-panel.js")) as fh:
        return fh.read()


def test_panel_defines_custom_element(panel_js):
    assert "customElements.define" in panel_js
    assert "statistics-outlier-cleaner-panel" in panel_js


def test_panel_class_name(panel_js):
    assert "StatisticsOutlierCleanerPanel" in panel_js


def test_panel_uses_ha_entity_picker(panel_js):
    # HA's own entity picker, with a plain text fallback. list_sum_statistics
    # still feeds the picker's allow-list.
    assert "list_sum_statistics" in panel_js
    assert "ha-entity-picker" in panel_js
    assert "stat-input" in panel_js
    assert "stat-dropdown" not in panel_js


def test_panel_uses_ha_date_range_picker(panel_js):
    # HA's own picker is the only date control; there is no fallback field of
    # ours to drift from it.
    assert "ha-date-range-picker" in panel_js
    assert 'type="date"' not in panel_js
    assert "date-range-wrap" in panel_js


def test_panel_references_ws_commands(panel_js):
    for cmd in ("fetch_outliers", "apply_fix", "list_fixes", "restore_fix"):
        assert cmd in panel_js, f"panel JS missing WS command reference '{cmd}'"



# ---------------------------------------------------------------------------
# Python module imports
# ---------------------------------------------------------------------------


def test_db_module_exports():
    sys.path.insert(0, os.path.dirname(__file__))
    from custom_components.statistics_outlier_cleaner.db import (
        apply_fix_sync,
        ensure_backup_table,
        fetch_stats_rows,
        list_fixes_sync,
        resolve_metadata_id_sync,
        restore_fix_sync,
    )
    for fn in (apply_fix_sync, ensure_backup_table, fetch_stats_rows,
               list_fixes_sync, resolve_metadata_id_sync, restore_fix_sync):
        assert callable(fn)


def test_outlier_module_exports():
    from custom_components.statistics_outlier_cleaner.outlier import (
        OutlierCandidate,
        OutlierReport,
        _algo_absolute,
        _algo_mad,
        _algo_top_n,
        _hybrid_rows,
        _median_sorted,
        _normalise_rows,
        _to_ms_epoch,
    )
    for obj in (OutlierCandidate, OutlierReport, _algo_absolute, _algo_mad,
                _algo_top_n, _hybrid_rows, _median_sorted, _normalise_rows, _to_ms_epoch):
        assert obj is not None


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------


def _run_standalone():
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "pytest", __file__, "-v"],
        cwd=os.path.dirname(__file__) or ".",
    )
    sys.exit(result.returncode)


if __name__ == "__main__":
    _run_standalone()
