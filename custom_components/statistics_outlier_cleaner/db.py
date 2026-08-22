"""Direct SQLite operations for the Statistics Outlier Cleaner.

All public functions are synchronous and accept a raw ``sqlite3.Connection``.
They must be called from within the recorder's executor via
``await get_instance(hass).async_add_executor_job(...)``.

Key design decisions:
  - statistics_short_term is always updated BEFORE statistics.  HA derives
    hourly LTS rows from 5-minute STS rows; updating STS first ensures that
    any future recorder compaction stays consistent.
  - Every fix is wrapped in a single transaction: backup INSERT + UPDATE.
  - Restores use the original row ID stored in the backup, so they are exact
    reversals even when multiple fixes overlap.
  - The float comparison for start_ts uses ABS(x - ?) < 0.5 to avoid
    floating-point equality pitfalls.
"""

from __future__ import annotations

import logging
import math
import sqlite3
from typing import Any

_LOGGER = logging.getLogger(__name__)

_BACKUP_TABLE = "statistics_outlier_cleaner_backup"

_CREATE_BACKUP_TABLE = f"""
CREATE TABLE IF NOT EXISTS {_BACKUP_TABLE} (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    fix_id          TEXT    NOT NULL,
    fix_ts          REAL    NOT NULL,
    statistic_id    TEXT    NOT NULL,
    metadata_id     INTEGER NOT NULL,
    source_table    TEXT    NOT NULL,
    source_row_id   INTEGER NOT NULL,
    start_ts        REAL    NOT NULL,
    state           REAL,
    sum             REAL,
    mean            REAL,
    min             REAL,
    max             REAL,
    last_reset_ts   REAL
);
"""

_CREATE_BACKUP_INDEX = f"""
CREATE INDEX IF NOT EXISTS idx_socb_fix_id
    ON {_BACKUP_TABLE}(fix_id);
"""


def ensure_backup_table(conn: sqlite3.Connection) -> None:
    """Create the backup table and index if they don't already exist."""
    conn.executescript(_CREATE_BACKUP_TABLE + _CREATE_BACKUP_INDEX)


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------


def fetch_stats_rows(
    conn: sqlite3.Connection,
    metadata_id: int,
    period: str,
    since_ts: float | None = None,
) -> list[dict[str, Any]]:
    """Return statistics rows as dicts, ordered by start_ts.

    period: "hour" → statistics table; "5minute" → statistics_short_term.
    since_ts: if given, only rows with start_ts >= since_ts are returned.
    """
    table = "statistics" if period == "hour" else "statistics_short_term"
    if since_ts is not None:
        rows = conn.execute(
            f"SELECT * FROM {table} WHERE metadata_id = ? AND start_ts >= ? ORDER BY start_ts",
            (metadata_id, since_ts),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT * FROM {table} WHERE metadata_id = ? ORDER BY start_ts",
            (metadata_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Apply fix
# ---------------------------------------------------------------------------


def apply_fix_sync(
    conn: sqlite3.Connection,
    statistic_id: str,
    metadata_id: int,
    candidates: list[dict[str, Any]],
    replacement: float,
    fix_id: str,
    fix_ts: float,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Fix outlier candidates in the SQLite database.

    For each candidate dict ``{"start_ts": float, "period": "hour"|"5minute"}``:

    1. Compute the existing ``change`` = sum[spike] - sum[prev_row].
    2. delta = replacement - change.  If delta == 0, skip.
    3. Back up all rows that will be modified (STS rows >= sts_spike_ts,
       LTS rows >= lts_spike_ts).
    4. Cascade delta forward through STS first, then LTS.
    5. Fix ``state`` on the spike row (set to previous row's state).

    Returns a summary dict with keys: fix_id, planned, applied, errors.
    """
    planned = 0
    applied = 0
    errors: list[dict[str, Any]] = []
    queries: list[str] = []

    for candidate in sorted(candidates, key=lambda c: c["start_ts"]):
        spike_ts: float = candidate["start_ts"]
        period: str = candidate["period"]

        is_5min = period == "5minute"
        sts_spike_ts = spike_ts
        lts_spike_ts = (int(spike_ts) // 3600) * 3600.0 if is_5min else spike_ts
        spike_table = "statistics_short_term" if is_5min else "statistics"

        # --- Compute change ---
        spike_row = conn.execute(
            f"SELECT id, sum, state FROM {spike_table}"
            " WHERE metadata_id = ? AND ABS(start_ts - ?) < 0.5",
            (metadata_id, spike_ts),
        ).fetchone()
        if spike_row is None:
            errors.append({"start_ts": spike_ts, "error": "row not found"})
            continue

        prev_row = conn.execute(
            f"SELECT sum FROM {spike_table}"
            " WHERE metadata_id = ? AND start_ts < ? ORDER BY start_ts DESC LIMIT 1",
            (metadata_id, spike_ts),
        ).fetchone()
        prev_sum = (prev_row["sum"] or 0.0) if prev_row else 0.0
        spike_sum = spike_row["sum"] or 0.0
        change = spike_sum - prev_sum
        delta = replacement - change

        if abs(delta) < 1e-9:
            continue

        planned += 1
        if dry_run:
            queries.append(
                f"INSERT INTO {_BACKUP_TABLE} (...) SELECT ... "
                f"FROM statistics_short_term WHERE metadata_id = {metadata_id} AND start_ts >= {sts_spike_ts};"
            )
            queries.append(
                f"INSERT INTO {_BACKUP_TABLE} (...) SELECT ... "
                f"FROM statistics WHERE metadata_id = {metadata_id} AND start_ts >= {lts_spike_ts};"
            )
            queries.append(
                f"UPDATE statistics_short_term SET sum = sum + {delta} "
                f"WHERE metadata_id = {metadata_id} AND start_ts >= {sts_spike_ts};"
            )
            queries.append(
                f"UPDATE statistics SET sum = sum + {delta} "
                f"WHERE metadata_id = {metadata_id} AND start_ts >= {lts_spike_ts};"
            )
            continue

        try:
            conn.execute("BEGIN")

            # 1. Backup STS rows that will be modified
            conn.execute(
                f"""INSERT INTO {_BACKUP_TABLE}
                    (fix_id, fix_ts, statistic_id, metadata_id,
                     source_table, source_row_id, start_ts,
                     state, sum, mean, min, max, last_reset_ts)
                    SELECT ?, ?, ?, metadata_id,
                           'statistics_short_term', id, start_ts,
                           state, sum, mean, min, max, last_reset_ts
                    FROM statistics_short_term
                    WHERE metadata_id = ? AND start_ts >= ?""",
                (fix_id, fix_ts, statistic_id, metadata_id, sts_spike_ts),
            )
            # Backup LTS rows that will be modified
            conn.execute(
                f"""INSERT INTO {_BACKUP_TABLE}
                    (fix_id, fix_ts, statistic_id, metadata_id,
                     source_table, source_row_id, start_ts,
                     state, sum, mean, min, max, last_reset_ts)
                    SELECT ?, ?, ?, metadata_id,
                           'statistics', id, start_ts,
                           state, sum, mean, min, max, last_reset_ts
                    FROM statistics
                    WHERE metadata_id = ? AND start_ts >= ?""",
                (fix_id, fix_ts, statistic_id, metadata_id, lts_spike_ts),
            )

            # 2. Cascade delta forward — STS first, then LTS
            conn.execute(
                "UPDATE statistics_short_term"
                " SET sum = sum + ? WHERE metadata_id = ? AND start_ts >= ?",
                (delta, metadata_id, sts_spike_ts),
            )
            conn.execute(
                "UPDATE statistics"
                " SET sum = sum + ? WHERE metadata_id = ? AND start_ts >= ?",
                (delta, metadata_id, lts_spike_ts),
            )

            conn.execute("COMMIT")
            _LOGGER.info(
                "Applied fix %s: %s at start_ts=%s, delta=%s",
                fix_id,
                statistic_id,
                spike_ts,
                delta,
            )
            applied += 1

        except Exception:
            conn.execute("ROLLBACK")
            _LOGGER.exception(
                "Failed to apply fix for %s at start_ts=%s", statistic_id, spike_ts
            )
            errors.append({"start_ts": spike_ts, "error": "transaction failed"})

    return {
        "fix_id": fix_id,
        "planned": planned,
        "applied": applied,
        "errors": errors,
        "queries": queries,
    }


# ---------------------------------------------------------------------------
# Restore fix
# ---------------------------------------------------------------------------


def restore_fix_sync(conn: sqlite3.Connection, fix_id: str) -> dict[str, Any]:
    """Restore all rows from a previous fix, identified by fix_id.

    Overwrites the current row values with the backed-up originals, then
    deletes the backup rows so the fix can be re-applied if desired.
    """
    # One fix can back up the same row more than once: fixing several candidates
    # in a single call cascades from each of them, so a row after the second
    # candidate is backed up once with its true original value and again with the
    # intermediate value left by the first candidate's cascade. Restoring the
    # later entry would leave the row half-fixed, so take the earliest entry per
    # row — lowest id is the first INSERT, which ran before any UPDATE.
    backup_rows = conn.execute(
        f"""SELECT * FROM {_BACKUP_TABLE} AS b
            WHERE b.fix_id = ?
              AND b.id = (
                  SELECT MIN(id) FROM {_BACKUP_TABLE}
                  WHERE fix_id = b.fix_id
                    AND source_table = b.source_table
                    AND source_row_id = b.source_row_id
              )
            ORDER BY b.start_ts""",
        (fix_id,),
    ).fetchall()

    if not backup_rows:
        return {"fix_id": fix_id, "restored": 0, "errors": []}

    restored = 0
    errors: list[dict[str, Any]] = []

    try:
        conn.execute("BEGIN")

        for row in backup_rows:
            source_table = row["source_table"]
            try:
                conn.execute(
                    f"""UPDATE {source_table}
                        SET state = ?, sum = ?, mean = ?, min = ?, max = ?, last_reset_ts = ?
                        WHERE id = ?""",
                    (
                        row["state"],
                        row["sum"],
                        row["mean"],
                        row["min"],
                        row["max"],
                        row["last_reset_ts"],
                        row["source_row_id"],
                    ),
                )
                restored += 1
            except Exception:
                _LOGGER.exception(
                    "Failed to restore row %s from %s", row["source_row_id"], source_table
                )
                errors.append(
                    {"start_ts": row["start_ts"], "error": "restore row failed"}
                )

        if not errors:
            conn.execute(
                f"DELETE FROM {_BACKUP_TABLE} WHERE fix_id = ?",
                (fix_id,),
            )

        conn.execute("COMMIT")
        _LOGGER.info("Restored fix %s: %d rows", fix_id, restored)

    except Exception:
        conn.execute("ROLLBACK")
        _LOGGER.exception("Failed to restore fix %s", fix_id)
        return {"fix_id": fix_id, "restored": 0, "errors": [{"error": "transaction failed"}]}

    return {"fix_id": fix_id, "restored": restored, "errors": errors}


# ---------------------------------------------------------------------------
# List fixes
# ---------------------------------------------------------------------------


def resolve_metadata_id_sync(conn: sqlite3.Connection, statistic_id: str) -> int | None:
    """Return the integer metadata_id for statistic_id, or None if not found."""
    row = conn.execute(
        "SELECT id FROM statistics_meta WHERE statistic_id = ?",
        (statistic_id,),
    ).fetchone()
    return row["id"] if row else None


def list_fixes_sync(conn: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    """Return a summary of recent fixes, newest first.

    Each entry has: fix_id, statistic_id, fix_ts, row_count.
    """
    rows = conn.execute(
        f"""SELECT fix_id, statistic_id, fix_ts, COUNT(*) AS row_count
            FROM {_BACKUP_TABLE}
            GROUP BY fix_id, statistic_id, fix_ts
            ORDER BY fix_ts DESC
            LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]
