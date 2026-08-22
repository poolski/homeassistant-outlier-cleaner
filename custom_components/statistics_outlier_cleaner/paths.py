"""Resolution of the recorder's SQLite database path.

This integration fixes statistics by writing to the recorder database over its
own sqlite3 connection, which only works if it opens the same file the recorder
opened. That file is whatever `recorder: db_url:` points at — it is *not*
necessarily `<config>/home-assistant_v2.db`.

Parsing is delegated to SQLAlchemy's own URL parser rather than string
surgery, because the recorder hands the identical URL to `create_engine()`.
Whatever SQLAlchemy thinks the path is, is the path.
"""

from __future__ import annotations

from homeassistant.components.recorder import get_instance
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError


class DatabaseNotSupportedError(HomeAssistantError):
    """Raised when the recorder database cannot be modified directly.

    Covers both non-SQLite dialects and in-memory SQLite. Surfaced to the user
    rather than swallowed: the alternative is creating a stray database file,
    writing to it, and reporting success while their real statistics are
    untouched.
    """


def sqlite_path_from_url(db_url: str) -> str:
    """Return the filesystem path for a SQLite recorder URL.

    Raises DatabaseNotSupportedError for non-SQLite dialects and for in-memory
    SQLite, neither of which a second connection can reach.
    """
    from sqlalchemy.engine import make_url  # noqa: PLC0415
    from sqlalchemy.exc import ArgumentError  # noqa: PLC0415

    try:
        url = make_url(db_url)
    except ArgumentError as exc:
        raise DatabaseNotSupportedError(
            f"Could not parse the recorder db_url: {exc}"
        ) from exc

    backend = url.get_backend_name()
    if backend != "sqlite":
        # Render the dialect only. `url` itself may carry a password, and this
        # message reaches the frontend and the log.
        raise DatabaseNotSupportedError(
            f"The recorder is using {backend}, but Statistics Outlier Cleaner "
            "can only modify a SQLite database. See the integration README."
        )

    # make_url("sqlite://").database is None; ":memory:" is the explicit form.
    if not url.database or url.database == ":memory:":
        raise DatabaseNotSupportedError(
            "The recorder is using an in-memory SQLite database, which cannot "
            "be modified from a separate connection."
        )

    return url.database


def resolve_sqlite_path(hass: HomeAssistant) -> str:
    """Return the path to the recorder's SQLite file.

    Raises DatabaseNotSupportedError if the recorder is not on file-backed
    SQLite.
    """
    return sqlite_path_from_url(get_instance(hass).db_url)
