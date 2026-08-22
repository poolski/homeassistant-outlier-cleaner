"""Integration tests for db.py using a real temp SQLite database.

These tests exercise the full backup → apply → restore cycle without
requiring a running Home Assistant instance.  All functions under test are
the synchronous helpers in db.py that get called inside the recorder's
executor.
"""

from __future__ import annotations

import sqlite3
import sys
import time
import uuid
from contextlib import contextmanager

import pytest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from custom_components.statistics_outlier_cleaner.db import (
    apply_fix_sync,
    ensure_backup_table,
    fetch_stats_rows,
    list_fixes_sync,
    restore_fix_sync,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


SCHEMA = """
CREATE TABLE statistics_meta (
    id INTEGER PRIMARY KEY,
    statistic_id TEXT NOT NULL,
    unit_of_measurement TEXT,
    has_sum INTEGER,
    has_mean INTEGER
);

CREATE TABLE statistics (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    metadata_id INTEGER NOT NULL,
    start_ts    REAL    NOT NULL,
    created_ts  REAL,
    mean        REAL,
    min         REAL,
    max         REAL,
    last_reset_ts REAL,
    state       REAL,
    sum         REAL
);

CREATE TABLE statistics_short_term (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    metadata_id INTEGER NOT NULL,
    start_ts    REAL    NOT NULL,
    created_ts  REAL,
    mean        REAL,
    min         REAL,
    max         REAL,
    last_reset_ts REAL,
    state       REAL,
    sum         REAL
);
"""


@pytest.fixture
def conn():
    """In-memory SQLite connection with the HA statistics schema."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    yield c
    c.close()


def _insert_meta(conn, metadata_id: int, statistic_id: str = "sensor.test") -> None:
    conn.execute(
        "INSERT INTO statistics_meta (id, statistic_id, has_sum) VALUES (?, ?, 1)",
        (metadata_id, statistic_id),
    )
    conn.commit()


def _insert_lts(conn, metadata_id: int, rows: list[tuple]) -> None:
    """rows = [(start_ts, state, sum), ...]"""
    conn.executemany(
        "INSERT INTO statistics (metadata_id, start_ts, state, sum) VALUES (?, ?, ?, ?)",
        [(metadata_id, r[0], r[1], r[2]) for r in rows],
    )
    conn.commit()


def _insert_sts(conn, metadata_id: int, rows: list[tuple]) -> None:
    """rows = [(start_ts, state, sum), ...]"""
    conn.executemany(
        "INSERT INTO statistics_short_term (metadata_id, start_ts, state, sum) VALUES (?, ?, ?, ?)",
        [(metadata_id, r[0], r[1], r[2]) for r in rows],
    )
    conn.commit()


def _lts_rows(conn, metadata_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM statistics WHERE metadata_id = ? ORDER BY start_ts",
        (metadata_id,),
    ).fetchall()


def _sts_rows(conn, metadata_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM statistics_short_term WHERE metadata_id = ? ORDER BY start_ts",
        (metadata_id,),
    ).fetchall()


def _backup_rows(conn) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM statistics_outlier_cleaner_backup ORDER BY start_ts"
    ).fetchall()


def _fix_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# ensure_backup_table
# ---------------------------------------------------------------------------


class TestEnsureBackupTable:
    def test_creates_table(self, conn):
        ensure_backup_table(conn)
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "statistics_outlier_cleaner_backup" in tables

    def test_idempotent(self, conn):
        ensure_backup_table(conn)
        ensure_backup_table(conn)  # Must not raise


# ---------------------------------------------------------------------------
# fetch_stats_rows
# ---------------------------------------------------------------------------


class TestFetchStatsRows:
    def test_fetches_hourly_rows(self, conn):
        _insert_meta(conn, 1)
        ensure_backup_table(conn)
        _insert_lts(conn, 1, [(0.0, 10.0, 10.0), (3600.0, 11.0, 11.0)])

        rows = fetch_stats_rows(conn, metadata_id=1, period="hour")
        assert len(rows) == 2
        assert rows[0]["start_ts"] == 0.0
        assert rows[1]["start_ts"] == 3600.0

    def test_fetches_five_min_rows(self, conn):
        _insert_meta(conn, 1)
        ensure_backup_table(conn)
        _insert_sts(conn, 1, [(0.0, 10.0, 10.0), (300.0, 10.5, 10.5)])

        rows = fetch_stats_rows(conn, metadata_id=1, period="5minute")
        assert len(rows) == 2

    def test_since_ts_filters_older_rows(self, conn):
        _insert_meta(conn, 1)
        ensure_backup_table(conn)
        _insert_lts(conn, 1, [(0.0, 10.0, 10.0), (3600.0, 11.0, 11.0), (7200.0, 12.0, 12.0)])

        rows = fetch_stats_rows(conn, metadata_id=1, period="hour", since_ts=3600.0)
        assert len(rows) == 2
        assert all(r["start_ts"] >= 3600.0 for r in rows)

    def test_unknown_metadata_returns_empty(self, conn):
        ensure_backup_table(conn)
        rows = fetch_stats_rows(conn, metadata_id=999, period="hour")
        assert rows == []

    def test_rows_ordered_by_start_ts(self, conn):
        _insert_meta(conn, 1)
        ensure_backup_table(conn)
        # Insert out of order.
        _insert_lts(conn, 1, [(7200.0, 3.0, 3.0), (0.0, 1.0, 1.0), (3600.0, 2.0, 2.0)])

        rows = fetch_stats_rows(conn, metadata_id=1, period="hour")
        ts = [r["start_ts"] for r in rows]
        assert ts == sorted(ts)


# ---------------------------------------------------------------------------
# apply_fix_sync — hourly candidates
# ---------------------------------------------------------------------------


class TestApplyFixSyncHourly:
    """Spike in the statistics (LTS) table."""

    @pytest.fixture
    def preloaded_conn(self, conn):
        _insert_meta(conn, 1)
        ensure_backup_table(conn)
        #                   start_ts   state        sum
        _insert_lts(conn, 1, [
            (0.0,     100.0,     100.0),      # normal row
            (3600.0,  1000100.0, 1000100.0),  # SPIKE  change = 1 000 000
            (7200.0,  1000101.0, 1000101.0),  # subsequent
            (10800.0, 1000102.0, 1000102.0),  # subsequent
        ])
        return conn

    def test_sum_on_spike_row_becomes_replacement(self, preloaded_conn):
        conn = preloaded_conn
        result = apply_fix_sync(
            conn,
            statistic_id="sensor.test",
            metadata_id=1,
            candidates=[{"start_ts": 3600.0, "period": "hour"}],
            replacement=0.0,
            fix_id=_fix_id(),
            fix_ts=time.time(),
        )
        assert result["applied"] == 1

        rows = _lts_rows(conn, 1)
        # spike row: change was 1_000_000, replacement=0, so delta=-1_000_000
        # new sum = 1_000_100 + (-1_000_000) = 100
        spike = rows[1]
        assert spike["sum"] == pytest.approx(100.0)

    def test_state_not_modified_on_spike_row(self, preloaded_conn):
        """state must never be changed — HA recorder uses it as the meter-reading baseline."""
        conn = preloaded_conn
        apply_fix_sync(
            conn,
            statistic_id="sensor.test",
            metadata_id=1,
            candidates=[{"start_ts": 3600.0, "period": "hour"}],
            replacement=0.0,
            fix_id=_fix_id(),
            fix_ts=time.time(),
        )
        rows = _lts_rows(conn, 1)
        # Original state was 1_000_100.0 — must be untouched.
        assert rows[1]["state"] == pytest.approx(1_000_100.0)

    def test_subsequent_rows_sum_shifted_forward(self, preloaded_conn):
        conn = preloaded_conn
        apply_fix_sync(
            conn,
            statistic_id="sensor.test",
            metadata_id=1,
            candidates=[{"start_ts": 3600.0, "period": "hour"}],
            replacement=0.0,
            fix_id=_fix_id(),
            fix_ts=time.time(),
        )
        rows = _lts_rows(conn, 1)
        # Rows after spike also shifted by delta=-1_000_000
        assert rows[2]["sum"] == pytest.approx(101.0)
        assert rows[3]["sum"] == pytest.approx(102.0)

    def test_prior_rows_unaffected(self, preloaded_conn):
        conn = preloaded_conn
        apply_fix_sync(
            conn,
            statistic_id="sensor.test",
            metadata_id=1,
            candidates=[{"start_ts": 3600.0, "period": "hour"}],
            replacement=0.0,
            fix_id=_fix_id(),
            fix_ts=time.time(),
        )
        rows = _lts_rows(conn, 1)
        assert rows[0]["sum"] == pytest.approx(100.0)
        assert rows[0]["state"] == pytest.approx(100.0)

    def test_backup_contains_spike_and_subsequent_rows(self, preloaded_conn):
        conn = preloaded_conn
        fix_id = _fix_id()
        apply_fix_sync(
            conn,
            statistic_id="sensor.test",
            metadata_id=1,
            candidates=[{"start_ts": 3600.0, "period": "hour"}],
            replacement=0.0,
            fix_id=fix_id,
            fix_ts=time.time(),
        )
        backup = conn.execute(
            "SELECT * FROM statistics_outlier_cleaner_backup WHERE fix_id = ? ORDER BY start_ts",
            (fix_id,),
        ).fetchall()
        # Spike row + 2 subsequent rows = 3 rows backed up.
        assert len(backup) == 3
        starts = [r["start_ts"] for r in backup]
        assert 3600.0 in starts
        assert 7200.0 in starts
        assert 10800.0 in starts

    def test_backup_stores_original_values(self, preloaded_conn):
        conn = preloaded_conn
        fix_id = _fix_id()
        apply_fix_sync(
            conn,
            statistic_id="sensor.test",
            metadata_id=1,
            candidates=[{"start_ts": 3600.0, "period": "hour"}],
            replacement=0.0,
            fix_id=fix_id,
            fix_ts=time.time(),
        )
        spike_backup = conn.execute(
            "SELECT * FROM statistics_outlier_cleaner_backup WHERE fix_id = ? AND start_ts = 3600.0",
            (fix_id,),
        ).fetchone()
        # Original sum was 1_000_100.
        assert spike_backup["sum"] == pytest.approx(1_000_100.0)
        assert spike_backup["state"] == pytest.approx(1_000_100.0)

    def test_dry_run_makes_no_db_changes(self, preloaded_conn):
        conn = preloaded_conn
        original_rows = _lts_rows(conn, 1)
        result = apply_fix_sync(
            conn,
            statistic_id="sensor.test",
            metadata_id=1,
            candidates=[{"start_ts": 3600.0, "period": "hour"}],
            replacement=0.0,
            fix_id=_fix_id(),
            fix_ts=time.time(),
            dry_run=True,
        )
        assert result["applied"] == 0
        assert result["planned"] == 1
        after_rows = _lts_rows(conn, 1)
        for before, after in zip(original_rows, after_rows):
            assert before["sum"] == after["sum"]
            assert before["state"] == after["state"]

    def test_noop_when_change_equals_replacement(self, conn):
        _insert_meta(conn, 1)
        ensure_backup_table(conn)
        # Row with change = 1.0 (sum goes 100 → 101).
        _insert_lts(conn, 1, [(0.0, 100.0, 100.0), (3600.0, 101.0, 101.0)])
        result = apply_fix_sync(
            conn,
            statistic_id="sensor.test",
            metadata_id=1,
            candidates=[{"start_ts": 3600.0, "period": "hour"}],
            replacement=1.0,   # same as actual change
            fix_id=_fix_id(),
            fix_ts=time.time(),
        )
        assert result["applied"] == 0
        assert result["planned"] == 0

    def test_nonzero_replacement(self, preloaded_conn):
        conn = preloaded_conn
        apply_fix_sync(
            conn,
            statistic_id="sensor.test",
            metadata_id=1,
            candidates=[{"start_ts": 3600.0, "period": "hour"}],
            replacement=10.0,   # keep 10 kWh of the spike
            fix_id=_fix_id(),
            fix_ts=time.time(),
        )
        rows = _lts_rows(conn, 1)
        # change was 1_000_000, replacement=10 → delta = 10 - 1_000_000 = -999_990
        # spike sum = 1_000_100 + (-999_990) = 110
        assert rows[1]["sum"] == pytest.approx(110.0)

    def test_state_not_modified_for_nonzero_replacement(self, preloaded_conn):
        """state must remain unchanged regardless of replacement value."""
        conn = preloaded_conn
        apply_fix_sync(
            conn,
            statistic_id="sensor.test",
            metadata_id=1,
            candidates=[{"start_ts": 3600.0, "period": "hour"}],
            replacement=10.0,
            fix_id=_fix_id(),
            fix_ts=time.time(),
        )
        rows = _lts_rows(conn, 1)
        assert rows[1]["state"] == pytest.approx(1_000_100.0)


# ---------------------------------------------------------------------------
# apply_fix_sync — 5-minute candidates
# ---------------------------------------------------------------------------


class TestApplyFixSync5Min:
    """Spike in the statistics_short_term (STS) table.

    Setup: STS rows within one hour; one matching LTS hourly row whose
    sum/state must also be shifted.
    """

    @pytest.fixture
    def preloaded_conn(self, conn):
        _insert_meta(conn, 1)
        ensure_backup_table(conn)
        #                   start_ts   state        sum
        _insert_sts(conn, 1, [
            (0.0,    10.0,     10.0),        # normal
            (300.0,  1000010.0, 1000010.0),  # SPIKE  change = 1_000_000
            (600.0,  1000011.0, 1000011.0),  # subsequent
            (900.0,  1000012.0, 1000012.0),  # subsequent (last in the 0-3600 hour)
        ])
        # Enclosing hourly row: start_ts = floor(300/3600)*3600 = 0
        _insert_lts(conn, 1, [
            (0.0,    1000012.0, 1000012.0),  # end-of-hour sum
            (3600.0, 1000013.0, 1000013.0),  # next hour
        ])
        return conn

    def test_sts_spike_sum_corrected(self, preloaded_conn):
        conn = preloaded_conn
        apply_fix_sync(
            conn,
            statistic_id="sensor.test",
            metadata_id=1,
            candidates=[{"start_ts": 300.0, "period": "5minute"}],
            replacement=0.0,
            fix_id=_fix_id(),
            fix_ts=time.time(),
        )
        sts = _sts_rows(conn, 1)
        assert sts[1]["sum"] == pytest.approx(10.0)

    def test_sts_spike_state_not_modified(self, preloaded_conn):
        """STS state must remain the original meter reading — HA uses it as restart baseline."""
        conn = preloaded_conn
        apply_fix_sync(
            conn,
            statistic_id="sensor.test",
            metadata_id=1,
            candidates=[{"start_ts": 300.0, "period": "5minute"}],
            replacement=0.0,
            fix_id=_fix_id(),
            fix_ts=time.time(),
        )
        sts = _sts_rows(conn, 1)
        assert sts[1]["state"] == pytest.approx(1_000_010.0)

    def test_subsequent_sts_rows_shifted(self, preloaded_conn):
        conn = preloaded_conn
        apply_fix_sync(
            conn,
            statistic_id="sensor.test",
            metadata_id=1,
            candidates=[{"start_ts": 300.0, "period": "5minute"}],
            replacement=0.0,
            fix_id=_fix_id(),
            fix_ts=time.time(),
        )
        sts = _sts_rows(conn, 1)
        assert sts[2]["sum"] == pytest.approx(11.0)
        assert sts[3]["sum"] == pytest.approx(12.0)

    def test_enclosing_lts_hour_sum_shifted(self, preloaded_conn):
        conn = preloaded_conn
        apply_fix_sync(
            conn,
            statistic_id="sensor.test",
            metadata_id=1,
            candidates=[{"start_ts": 300.0, "period": "5minute"}],
            replacement=0.0,
            fix_id=_fix_id(),
            fix_ts=time.time(),
        )
        lts = _lts_rows(conn, 1)
        # Enclosing hour (start_ts=0.0) shifted by -1_000_000
        assert lts[0]["sum"] == pytest.approx(12.0)
        # Subsequent hourly row also shifted
        assert lts[1]["sum"] == pytest.approx(13.0)

    def test_sts_updated_before_lts(self, preloaded_conn):
        """
        Verify execution order: STS must be modified before LTS.

        We check this indirectly: after the fix, both tables are consistent
        (same delta applied) — if LTS were done first the STS delta would
        overwrite it incorrectly in a real HA session.

        The real ordering guarantee comes from the implementation; here we
        just assert the final state is correct for both tables.
        """
        conn = preloaded_conn
        apply_fix_sync(
            conn,
            statistic_id="sensor.test",
            metadata_id=1,
            candidates=[{"start_ts": 300.0, "period": "5minute"}],
            replacement=0.0,
            fix_id=_fix_id(),
            fix_ts=time.time(),
        )
        sts = _sts_rows(conn, 1)
        lts = _lts_rows(conn, 1)
        # Both should be shifted by the same delta.
        sts_last_sum = sts[-1]["sum"]
        lts_first_sum = lts[0]["sum"]
        # The LTS first row (end of hour) should equal the last STS row's sum.
        assert lts_first_sum == pytest.approx(sts_last_sum)

    def test_sts_state_not_modified_for_nonzero_replacement(self, preloaded_conn):
        """STS state must remain unchanged regardless of replacement value."""
        conn = preloaded_conn
        apply_fix_sync(
            conn,
            statistic_id="sensor.test",
            metadata_id=1,
            candidates=[{"start_ts": 300.0, "period": "5minute"}],
            replacement=5.0,
            fix_id=_fix_id(),
            fix_ts=time.time(),
        )
        sts = _sts_rows(conn, 1)
        assert sts[1]["state"] == pytest.approx(1_000_010.0)

    def test_lts_state_not_modified_when_spike_is_last_sts_in_hour(self, conn):
        """LTS state must remain unchanged — HA compiler derives it from live STS on restart."""
        _insert_meta(conn, 1)
        ensure_backup_table(conn)
        _insert_sts(conn, 1, [
            (0.0,   10.0,      10.0),
            (300.0, 1_000_010.0, 1_000_010.0),  # SPIKE — last in hour 0
        ])
        _insert_lts(conn, 1, [
            (-3600.0, 5.0,         5.0),
            (0.0,     1_000_010.0, 1_000_010.0),  # enclosing LTS hour
        ])
        apply_fix_sync(
            conn,
            statistic_id="sensor.test",
            metadata_id=1,
            candidates=[{"start_ts": 300.0, "period": "5minute"}],
            replacement=7.0,
            fix_id=_fix_id(),
            fix_ts=time.time(),
        )
        lts = _lts_rows(conn, 1)
        enclosing = next(r for r in lts if r["start_ts"] == pytest.approx(0.0))
        assert enclosing["state"] == pytest.approx(1_000_010.0)

    def test_backup_includes_sts_rows(self, preloaded_conn):
        conn = preloaded_conn
        fix_id = _fix_id()
        apply_fix_sync(
            conn,
            statistic_id="sensor.test",
            metadata_id=1,
            candidates=[{"start_ts": 300.0, "period": "5minute"}],
            replacement=0.0,
            fix_id=fix_id,
            fix_ts=time.time(),
        )
        backup = conn.execute(
            "SELECT source_table, start_ts FROM statistics_outlier_cleaner_backup "
            "WHERE fix_id = ? ORDER BY source_table, start_ts",
            (fix_id,),
        ).fetchall()
        sources = {(r["source_table"], r["start_ts"]) for r in backup}
        # Spike row + 2 subsequent STS rows + 2 LTS rows
        assert ("statistics_short_term", 300.0) in sources
        assert ("statistics_short_term", 600.0) in sources
        assert ("statistics_short_term", 900.0) in sources
        assert ("statistics", 0.0) in sources
        assert ("statistics", 3600.0) in sources


# ---------------------------------------------------------------------------
# restore_fix_sync
# ---------------------------------------------------------------------------


class TestRestoreFixSync:
    @pytest.fixture
    def applied_conn(self, conn):
        _insert_meta(conn, 1)
        ensure_backup_table(conn)
        _insert_lts(conn, 1, [
            (0.0,     100.0,     100.0),
            (3600.0,  1000100.0, 1000100.0),
            (7200.0,  1000101.0, 1000101.0),
        ])
        return conn

    def test_restores_original_values(self, applied_conn):
        conn = applied_conn
        fix_id = _fix_id()
        apply_fix_sync(
            conn,
            statistic_id="sensor.test",
            metadata_id=1,
            candidates=[{"start_ts": 3600.0, "period": "hour"}],
            replacement=0.0,
            fix_id=fix_id,
            fix_ts=time.time(),
        )
        restore_fix_sync(conn, fix_id)

        rows = _lts_rows(conn, 1)
        # Everything should be back to original.
        assert rows[0]["sum"] == pytest.approx(100.0)
        assert rows[1]["sum"] == pytest.approx(1_000_100.0)
        assert rows[1]["state"] == pytest.approx(1_000_100.0)
        assert rows[2]["sum"] == pytest.approx(1_000_101.0)

    def test_backup_rows_deleted_after_restore(self, applied_conn):
        conn = applied_conn
        fix_id = _fix_id()
        apply_fix_sync(
            conn,
            statistic_id="sensor.test",
            metadata_id=1,
            candidates=[{"start_ts": 3600.0, "period": "hour"}],
            replacement=0.0,
            fix_id=fix_id,
            fix_ts=time.time(),
        )
        restore_fix_sync(conn, fix_id)

        remaining = conn.execute(
            "SELECT COUNT(*) FROM statistics_outlier_cleaner_backup WHERE fix_id = ?",
            (fix_id,),
        ).fetchone()[0]
        assert remaining == 0

    def test_restore_unknown_fix_id_returns_zero_restored(self, applied_conn):
        conn = applied_conn
        result = restore_fix_sync(conn, "nonexistent-id")
        assert result["restored"] == 0

    def test_apply_restore_apply_cycle(self, applied_conn):
        """Apply, restore, then apply again — should work without error."""
        conn = applied_conn
        fix_id_1 = _fix_id()
        apply_fix_sync(
            conn,
            statistic_id="sensor.test",
            metadata_id=1,
            candidates=[{"start_ts": 3600.0, "period": "hour"}],
            replacement=0.0,
            fix_id=fix_id_1,
            fix_ts=time.time(),
        )
        restore_fix_sync(conn, fix_id_1)

        fix_id_2 = _fix_id()
        result = apply_fix_sync(
            conn,
            statistic_id="sensor.test",
            metadata_id=1,
            candidates=[{"start_ts": 3600.0, "period": "hour"}],
            replacement=0.0,
            fix_id=fix_id_2,
            fix_ts=time.time(),
        )
        assert result["applied"] == 1

        rows = _lts_rows(conn, 1)
        assert rows[1]["sum"] == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# list_fixes_sync
# ---------------------------------------------------------------------------


class TestListFixesSync:
    def test_lists_recent_fixes(self, conn):
        _insert_meta(conn, 1)
        ensure_backup_table(conn)
        _insert_lts(conn, 1, [
            (0.0,     100.0,     100.0),
            (3600.0,  1000100.0, 1000100.0),
            (7200.0,  1000101.0, 1000101.0),
        ])
        fix_id = _fix_id()
        apply_fix_sync(
            conn,
            statistic_id="sensor.test",
            metadata_id=1,
            candidates=[{"start_ts": 3600.0, "period": "hour"}],
            replacement=0.0,
            fix_id=fix_id,
            fix_ts=time.time(),
        )
        fixes = list_fixes_sync(conn)
        fix_ids = [f["fix_id"] for f in fixes]
        assert fix_id in fix_ids

    def test_fix_entry_includes_statistic_id(self, conn):
        _insert_meta(conn, 1)
        ensure_backup_table(conn)
        _insert_lts(conn, 1, [(0.0, 100.0, 100.0), (3600.0, 1000100.0, 1000100.0)])
        fix_id = _fix_id()
        apply_fix_sync(
            conn,
            statistic_id="sensor.test",
            metadata_id=1,
            candidates=[{"start_ts": 3600.0, "period": "hour"}],
            replacement=0.0,
            fix_id=fix_id,
            fix_ts=time.time(),
        )
        fixes = list_fixes_sync(conn)
        entry = next(f for f in fixes if f["fix_id"] == fix_id)
        assert entry["statistic_id"] == "sensor.test"

    def test_multiple_fixes_all_listed(self, conn):
        _insert_meta(conn, 1)
        ensure_backup_table(conn)
        _insert_lts(conn, 1, [
            (0.0,     100.0,     100.0),
            (3600.0,  1000100.0, 1000100.0),
            (7200.0,  1000101.0, 1000101.0),
        ])
        fix_ids = []
        for spike_ts in [3600.0, 7200.0]:
            fid = _fix_id()
            fix_ids.append(fid)
            apply_fix_sync(
                conn,
                statistic_id="sensor.test",
                metadata_id=1,
                candidates=[{"start_ts": spike_ts, "period": "hour"}],
                replacement=0.0,
                fix_id=fid,
                fix_ts=time.time(),
            )
        fixes = list_fixes_sync(conn)
        returned_ids = {f["fix_id"] for f in fixes}
        assert set(fix_ids).issubset(returned_ids)

    def test_limit_honoured(self, conn):
        _insert_meta(conn, 1)
        ensure_backup_table(conn)
        rows = [(i * 3600.0, float(i * 1000), float(i * 1000)) for i in range(25)]
        _insert_lts(conn, 1, rows)
        for i in range(1, 25):
            apply_fix_sync(
                conn,
                statistic_id="sensor.test",
                metadata_id=1,
                candidates=[{"start_ts": i * 3600.0, "period": "hour"}],
                replacement=0.0,
                fix_id=_fix_id(),
                fix_ts=float(i),
            )
        fixes = list_fixes_sync(conn, limit=5)
        assert len(fixes) <= 5


# ---------------------------------------------------------------------------
# apply_fix_sync — bounded spike (restart-hour up+down pair)
# ---------------------------------------------------------------------------


class TestApplyFixSyncBoundedSpike:
    """Restart-hour spike: one row jumps up, the next row cancels it out.

    Both rows are passed as candidates.  After applying the fix with
    replacement=0 the cumulative ``sum`` column shifts but every row's
    *change* (sum[i] - sum[i-1]) outside the fixed window must be
    identical to the original.
    """

    @pytest.fixture
    def preloaded_conn(self, conn):
        _insert_meta(conn, 1)
        ensure_backup_table(conn)
        #                   start_ts    state         sum
        _insert_lts(conn, 1, [
            (0.0,     100.0,       100.0),        # baseline
            (3600.0,  1_000_100.0, 1_000_100.0),  # spike UP   (+1_000_000)
            (7200.0,  101.0,       101.0),         # spike DOWN (≈ −999_999)
            (10800.0, 102.0,       102.0),         # normal row (change = +1)
        ])
        return conn

    def test_both_candidates_applied(self, preloaded_conn):
        result = apply_fix_sync(
            preloaded_conn,
            statistic_id="sensor.test",
            metadata_id=1,
            candidates=[
                {"start_ts": 3600.0, "period": "hour"},
                {"start_ts": 7200.0, "period": "hour"},
            ],
            replacement=0.0,
            fix_id=_fix_id(),
            fix_ts=time.time(),
        )
        assert result["applied"] == 2

    def test_subsequent_row_change_preserved(self, preloaded_conn):
        """Row after the spike window keeps its original change (+1)."""
        apply_fix_sync(
            preloaded_conn,
            statistic_id="sensor.test",
            metadata_id=1,
            candidates=[
                {"start_ts": 3600.0, "period": "hour"},
                {"start_ts": 7200.0, "period": "hour"},
            ],
            replacement=0.0,
            fix_id=_fix_id(),
            fix_ts=time.time(),
        )
        rows = _lts_rows(preloaded_conn, 1)
        # change at T=10800 was originally 102 - 101 = 1; must still be 1
        assert rows[3]["sum"] - rows[2]["sum"] == pytest.approx(1.0)

    def test_candidates_passed_in_reverse_order_give_same_result(self, preloaded_conn):
        """Candidates submitted newest-first must yield the same outcome."""
        apply_fix_sync(
            preloaded_conn,
            statistic_id="sensor.test",
            metadata_id=1,
            candidates=[
                {"start_ts": 7200.0, "period": "hour"},   # reversed
                {"start_ts": 3600.0, "period": "hour"},
            ],
            replacement=0.0,
            fix_id=_fix_id(),
            fix_ts=time.time(),
        )
        rows = _lts_rows(preloaded_conn, 1)
        assert rows[3]["sum"] - rows[2]["sum"] == pytest.approx(1.0)

    def test_spike_up_row_sum_zeroed(self, preloaded_conn):
        """Spike-up row gets sum = prev_sum + replacement = 100 + 0 = 100."""
        apply_fix_sync(
            preloaded_conn,
            statistic_id="sensor.test",
            metadata_id=1,
            candidates=[
                {"start_ts": 3600.0, "period": "hour"},
                {"start_ts": 7200.0, "period": "hour"},
            ],
            replacement=0.0,
            fix_id=_fix_id(),
            fix_ts=time.time(),
        )
        rows = _lts_rows(preloaded_conn, 1)
        assert rows[1]["sum"] == pytest.approx(100.0)

    def test_spike_down_row_sum_zeroed(self, preloaded_conn):
        """Spike-down row gets sum = prev_sum + replacement = 100 + 0 = 100."""
        apply_fix_sync(
            preloaded_conn,
            statistic_id="sensor.test",
            metadata_id=1,
            candidates=[
                {"start_ts": 3600.0, "period": "hour"},
                {"start_ts": 7200.0, "period": "hour"},
            ],
            replacement=0.0,
            fix_id=_fix_id(),
            fix_ts=time.time(),
        )
        rows = _lts_rows(preloaded_conn, 1)
        assert rows[2]["sum"] == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# apply_fix_sync — dry-run SQL queries
# ---------------------------------------------------------------------------


class TestDryRunQueries:
    def test_dry_run_returns_queries_list(self, conn):
        _insert_meta(conn, 1)
        ensure_backup_table(conn)
        _insert_lts(conn, 1, [
            (0.0, 100.0, 100.0),
            (3600.0, 1_000_100.0, 1_000_100.0),
            (7200.0, 1_000_101.0, 1_000_101.0),
        ])
        result = apply_fix_sync(
            conn,
            statistic_id="sensor.test",
            metadata_id=1,
            candidates=[{"start_ts": 3600.0, "period": "hour"}],
            replacement=0.0,
            fix_id=_fix_id(),
            fix_ts=time.time(),
            dry_run=True,
        )
        assert "queries" in result
        assert isinstance(result["queries"], list)
        assert len(result["queries"]) > 0

    def test_dry_run_queries_contain_update(self, conn):
        _insert_meta(conn, 1)
        ensure_backup_table(conn)
        _insert_lts(conn, 1, [
            (0.0, 100.0, 100.0),
            (3600.0, 1_000_100.0, 1_000_100.0),
        ])
        result = apply_fix_sync(
            conn,
            statistic_id="sensor.test",
            metadata_id=1,
            candidates=[{"start_ts": 3600.0, "period": "hour"}],
            replacement=0.0,
            fix_id=_fix_id(),
            fix_ts=time.time(),
            dry_run=True,
        )
        combined = "\n".join(result["queries"])
        assert "UPDATE" in combined or "INSERT" in combined

    def test_live_run_returns_empty_queries(self, conn):
        _insert_meta(conn, 1)
        ensure_backup_table(conn)
        _insert_lts(conn, 1, [
            (0.0, 100.0, 100.0),
            (3600.0, 1_000_100.0, 1_000_100.0),
        ])
        result = apply_fix_sync(
            conn,
            statistic_id="sensor.test",
            metadata_id=1,
            candidates=[{"start_ts": 3600.0, "period": "hour"}],
            replacement=0.0,
            fix_id=_fix_id(),
            fix_ts=time.time(),
            dry_run=False,
        )
        assert result["queries"] == []


class TestRestoreFixSyncOverlappingBackups:
    """Restoring a fix whose candidates had overlapping cascade ranges.

    Fixing two candidates in one call backs up every row at or after each
    candidate, so rows after the *second* candidate get backed up twice: once
    holding their true original value, and once holding the intermediate value
    left by the first candidate's cascade. Restore has to prefer the original.

    This is the bounded-spike case the README advertises as a headline feature,
    so it is the most likely way for a user to hit it.
    """

    ORIGINAL = [
        #  start_ts   state         sum
        (0.0,     100.0,       100.0),        # baseline
        (3600.0,  1_000_100.0, 1_000_100.0),  # spike UP
        (7200.0,  101.0,       101.0),        # compensating drop
        (10800.0, 102.0,       102.0),        # normal
        (14400.0, 103.0,       103.0),        # normal
    ]

    @pytest.fixture
    def preloaded_conn(self, conn):
        _insert_meta(conn, 1)
        ensure_backup_table(conn)
        _insert_lts(conn, 1, list(self.ORIGINAL))
        return conn

    def test_restore_returns_the_original_values_not_the_intermediate_ones(
        self, preloaded_conn
    ):
        fix_id = _fix_id()
        apply_fix_sync(
            preloaded_conn,
            statistic_id="sensor.test",
            metadata_id=1,
            candidates=[
                {"start_ts": 3600.0, "period": "hour"},
                {"start_ts": 7200.0, "period": "hour"},
            ],
            replacement=0.0,
            fix_id=fix_id,
            fix_ts=time.time(),
        )

        # Sanity: the fix actually changed something.
        assert [r["sum"] for r in _lts_rows(preloaded_conn, 1)] != [
            row[2] for row in self.ORIGINAL
        ]

        restore_fix_sync(preloaded_conn, fix_id)

        assert [r["sum"] for r in _lts_rows(preloaded_conn, 1)] == pytest.approx(
            [row[2] for row in self.ORIGINAL]
        )
        assert [r["state"] for r in _lts_rows(preloaded_conn, 1)] == pytest.approx(
            [row[1] for row in self.ORIGINAL]
        )

    def test_restore_reports_distinct_rows_not_backup_entries(self, preloaded_conn):
        """The count is rows put back, not backup entries consumed."""
        fix_id = _fix_id()
        apply_fix_sync(
            preloaded_conn,
            statistic_id="sensor.test",
            metadata_id=1,
            candidates=[
                {"start_ts": 3600.0, "period": "hour"},
                {"start_ts": 7200.0, "period": "hour"},
            ],
            replacement=0.0,
            fix_id=fix_id,
            fix_ts=time.time(),
        )
        result = restore_fix_sync(preloaded_conn, fix_id)

        # Rows at or after 3600.0: four of them. The backup table holds more
        # than that because of the overlap.
        assert result["restored"] == 4
        assert result["errors"] == []

    def test_backup_entries_are_cleared_after_restore(self, preloaded_conn):
        fix_id = _fix_id()
        apply_fix_sync(
            preloaded_conn,
            statistic_id="sensor.test",
            metadata_id=1,
            candidates=[
                {"start_ts": 3600.0, "period": "hour"},
                {"start_ts": 7200.0, "period": "hour"},
            ],
            replacement=0.0,
            fix_id=fix_id,
            fix_ts=time.time(),
        )
        restore_fix_sync(preloaded_conn, fix_id)

        assert _backup_rows(preloaded_conn) == []
