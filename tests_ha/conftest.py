"""
pytest configuration for HA integration tests.

These tests run against a real (test) Home Assistant instance provided by
pytest-homeassistant-custom-component.  They live in tests_ha/ (not tests/)
so they do NOT inherit the HA module stubs in tests/conftest.py.
"""

import os
import sys
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant

# hass_frontend is not installed in CI/test environments; provide a stub so
# the HA frontend component can be imported without crashing.
if "hass_frontend" not in sys.modules:
    sys.modules["hass_frontend"] = MagicMock()
    sys.modules["hass_frontend.manifest"] = MagicMock()

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Make this repo's integration discoverable by HA's loader.
#
# HA finds custom integrations with a bare `import custom_components`, then walks
# that package's __path__ (see homeassistant.loader._get_custom_components) — it
# does NOT look at hass.config.config_dir. pytest-homeassistant-custom-component
# imports its own testing_config/custom_components first, which claims the
# top-level `custom_components` name in sys.modules, so sys.path ordering cannot
# put ours ahead of it.
#
# Appending to __path__ makes it a multi-location package, so the plugin's test
# integrations AND this repo's both resolve.
import custom_components  # noqa: E402

_PROJECT_CUSTOM_COMPONENTS = os.path.join(PROJECT_ROOT, "custom_components")
if _PROJECT_CUSTOM_COMPONENTS not in custom_components.__path__:
    custom_components.__path__.append(_PROJECT_CUSTOM_COMPONENTS)


@pytest.fixture(scope="session", autouse=True)
def _prewarm_pycares_shutdown_thread() -> None:
    """Start pycares' shutdown thread before any test observes the thread list.

    pytest-homeassistant-custom-component's `verify_cleanup` fixture asserts that
    a test leaves behind no new threads. pycares (the DNS resolver behind aiodns,
    pulled in by aiohttp) lazily starts a single long-lived daemon thread the
    first time a channel is created, which happens when the first test spins up
    an HTTP/WebSocket client. That thread is never reaped, so whichever test
    happens to run first gets blamed for "leaking" it.

    Starting it here — once, at session scope — means it is already present in
    every test's baseline snapshot. Best-effort: if pycares' internals move, the
    tests still run and simply report the leak as before.
    """
    try:
        from pycares import _shutdown_manager  # noqa: PLC0415

        _shutdown_manager.start()
    except Exception:  # noqa: BLE001 - purely an optimisation for test hygiene
        pass


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(hass: HomeAssistant) -> None:
    """Allow loading custom integrations from the test config dir."""
    from homeassistant import loader  # noqa: PLC0415

    hass.data.pop(loader.DATA_CUSTOM_COMPONENTS, None)


@pytest.fixture
def mock_recorder_before_hass(recorder_db_url: str) -> None:
    """Ensure recorder_db_url is resolved before hass starts.

    The default implementation is a no-op, which allows hass to start before
    recorder_db_url runs, causing the 'assert not hass_fixture_setup' guard in
    recorder_db_url to fail.  By depending on recorder_db_url here, we force
    the correct setup order.
    """
