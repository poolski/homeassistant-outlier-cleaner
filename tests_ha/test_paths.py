"""
Tests for SQLite path resolution.

The integration writes to the recorder database directly, so it must open the
*same* file the recorder itself opened. Deriving that from the recorder's
configured `db_url` is the only way to get it right: a hardcoded
`hass.config.path("home-assistant_v2.db")` silently diverges from reality as
soon as a user sets `recorder: db_url:`.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant

from custom_components.statistics_outlier_cleaner.paths import (
    DatabaseNotSupportedError,
    resolve_sqlite_path,
    sqlite_path_from_url,
)


class TestSqlitePathFromUrl:
    """The pure URL -> filesystem path parser."""

    def test_absolute_path(self) -> None:
        # What the recorder builds by default: DEFAULT_URL is
        # "sqlite:///{path}", and the path is itself absolute, so the result
        # carries four slashes.
        assert (
            sqlite_path_from_url("sqlite:////config/home-assistant_v2.db")
            == "/config/home-assistant_v2.db"
        )

    def test_relative_path(self) -> None:
        assert sqlite_path_from_url("sqlite:///home-assistant_v2.db") == (
            "home-assistant_v2.db"
        )

    def test_query_parameters_are_not_part_of_the_path(self) -> None:
        assert (
            sqlite_path_from_url("sqlite:////config/ha.db?cache=shared")
            == "/config/ha.db"
        )

    def test_driver_qualified_sqlite_url(self) -> None:
        assert (
            sqlite_path_from_url("sqlite+pysqlite:////config/ha.db")
            == "/config/ha.db"
        )

    @pytest.mark.parametrize(
        "db_url",
        [
            "sqlite://",
            "sqlite:///:memory:",
            "sqlite+pysqlite:///:memory:",
        ],
    )
    def test_in_memory_is_rejected(self, db_url: str) -> None:
        # There is no file to open, so an in-memory recorder cannot be fixed by
        # a separate sqlite3 connection. Failing loudly beats creating a stray
        # database and reporting success.
        with pytest.raises(DatabaseNotSupportedError):
            sqlite_path_from_url(db_url)

    @pytest.mark.parametrize(
        ("db_url", "dialect"),
        [
            ("mysql://user:pw@host/ha", "mysql"),
            ("mysql+pymysql://user:pw@host/ha", "mysql"),
            ("postgresql://user:pw@host/ha", "postgresql"),
        ],
    )
    def test_non_sqlite_is_rejected_and_names_the_dialect(
        self, db_url: str, dialect: str
    ) -> None:
        with pytest.raises(DatabaseNotSupportedError) as excinfo:
            sqlite_path_from_url(db_url)
        assert dialect in str(excinfo.value)

    def test_rejection_message_does_not_leak_credentials(self) -> None:
        # The error surfaces to the frontend and the log, so the password in a
        # db_url must not ride along with it.
        with pytest.raises(DatabaseNotSupportedError) as excinfo:
            sqlite_path_from_url("mysql://user:sup3rsecret@host/ha")
        assert "sup3rsecret" not in str(excinfo.value)


class TestResolveSqlitePath:
    """Resolution against a live recorder."""

    async def test_returns_the_path_the_recorder_actually_opened(
        self,
        hass: HomeAssistant,
        recorder_mock,
        recorder_db_path,
    ) -> None:
        # recorder_db_url is overridden in conftest to a tmp file, which is a
        # path the old hardcoded hass.config.path() could never have produced.
        resolved = resolve_sqlite_path(hass)

        assert resolved == str(recorder_db_path)
        assert resolved != hass.config.path("home-assistant_v2.db")
