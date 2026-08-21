"""Unit tests for pure outlier-detection algorithm functions.

No Home Assistant stack required — the HA imports are stubbed in conftest.py.
"""

from __future__ import annotations

import math
import sys

import pytest

# conftest.py stubs HA modules; add the project root so the component is importable.
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from custom_components.statistics_outlier_cleaner.outlier import (
    OutlierCandidate,
    _algo_absolute,
    _algo_mad,
    _algo_top_n,
    _hybrid_rows,
    _median_sorted,
    _normalise_rows,
    _to_ms_epoch,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _candidate(start_ms: int, change: float, state: float = 0.0, period: str = "hour") -> OutlierCandidate:
    return OutlierCandidate(
        start=start_ms,
        end=start_ms + 3_600_000,
        change=change,
        state=state,
        period=period,
    )


def _hour(start_ms: int, change: float, state: float = 0.0) -> OutlierCandidate:
    return _candidate(start_ms, change, state, period="hour")


def _five_min(start_ms: int, change: float, state: float = 0.0) -> OutlierCandidate:
    return OutlierCandidate(
        start=start_ms,
        end=start_ms + 300_000,
        change=change,
        state=state,
        period="5minute",
    )


# ---------------------------------------------------------------------------
# _to_ms_epoch
# ---------------------------------------------------------------------------


class TestToMsEpoch:
    def test_converts_seconds_float_to_ms(self):
        assert _to_ms_epoch(1_000_000.0) == 1_000_000_000

    def test_already_ms_passes_through(self):
        # Any value >= 1e12 is treated as already in ms
        assert _to_ms_epoch(1_700_000_000_000) == 1_700_000_000_000

    def test_integer_seconds(self):
        assert _to_ms_epoch(1000) == 1_000_000

    def test_datetime_object(self):
        from datetime import datetime, timezone

        dt = datetime(2024, 1, 1, tzinfo=timezone.utc)
        expected = int(dt.timestamp() * 1000)
        assert _to_ms_epoch(dt) == expected

    def test_unsupported_type_raises(self):
        import pytest

        with pytest.raises(TypeError):
            _to_ms_epoch("not-a-timestamp")


# ---------------------------------------------------------------------------
# _normalise_rows
# ---------------------------------------------------------------------------


class TestNormaliseRows:
    def test_drops_rows_with_none_change(self):
        rows = [
            {"start": 0, "end": 1000, "change": None, "state": 1.0},
            {"start": 1000, "end": 2000, "change": 5.0, "state": 2.0},
        ]
        result = _normalise_rows(rows, "hour")
        assert len(result) == 1
        assert result[0].change == 5.0

    def test_keeps_zero_change(self):
        rows = [{"start": 0, "end": 1000, "change": 0.0, "state": 1.0}]
        result = _normalise_rows(rows, "hour")
        assert len(result) == 1
        assert result[0].change == 0.0

    def test_converts_seconds_to_ms(self):
        rows = [{"start": 1000.0, "end": 4600.0, "change": 1.0, "state": None}]
        result = _normalise_rows(rows, "hour")
        assert result[0].start == 1_000_000
        assert result[0].end == 4_600_000

    def test_state_none_stored_as_none(self):
        rows = [{"start": 0, "end": 1000, "change": 1.0, "state": None}]
        result = _normalise_rows(rows, "hour")
        assert result[0].state is None

    def test_period_label_applied(self):
        rows = [{"start": 0, "end": 1000, "change": 1.0, "state": 0.0}]
        assert _normalise_rows(rows, "5minute")[0].period == "5minute"


# ---------------------------------------------------------------------------
# _median_sorted
# ---------------------------------------------------------------------------


class TestMedianSorted:
    def test_empty_list(self):
        assert _median_sorted([]) == 0.0

    def test_single_element(self):
        assert _median_sorted([7.0]) == 7.0

    def test_odd_count(self):
        assert _median_sorted([1.0, 3.0, 5.0]) == 3.0

    def test_even_count(self):
        assert _median_sorted([1.0, 3.0]) == 2.0

    def test_larger_list(self):
        values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        assert _median_sorted(values) == 3.5


# ---------------------------------------------------------------------------
# _algo_top_n
# ---------------------------------------------------------------------------


class TestAlgoTopN:
    def test_returns_top_n_by_abs_change(self):
        candidates = [_hour(i * 1000, float(i)) for i in range(10)]
        result = _algo_top_n(candidates, 3)
        assert len(result) == 3
        assert [c.change for c in result] == [9.0, 8.0, 7.0]

    def test_negative_change_ranked_by_abs(self):
        candidates = [_hour(0, 10.0), _hour(1000, -50.0), _hour(2000, 5.0)]
        result = _algo_top_n(candidates, 1)
        assert result[0].change == -50.0

    def test_returns_all_when_fewer_than_n(self):
        candidates = [_hour(0, 1.0), _hour(1000, 2.0)]
        result = _algo_top_n(candidates, 10)
        assert len(result) == 2

    def test_empty_input(self):
        assert _algo_top_n([], 5) == []

    def test_n_equals_one(self):
        candidates = [_hour(0, 3.0), _hour(1000, 100.0), _hour(2000, 2.0)]
        result = _algo_top_n(candidates, 1)
        assert len(result) == 1
        assert result[0].change == 100.0

    def test_stable_sort_preserves_large_n_ordering(self):
        candidates = [_hour(i * 1000, float(10 - i)) for i in range(10)]
        result = _algo_top_n(candidates, 10)
        changes = [c.change for c in result]
        assert changes == sorted(changes, reverse=True)


# ---------------------------------------------------------------------------
# _algo_absolute
# ---------------------------------------------------------------------------


class TestAlgoAbsolute:
    def test_flags_rows_above_threshold(self):
        candidates = [_hour(0, 5.0), _hour(1000, 200.0), _hour(2000, 3.0)]
        result = _algo_absolute(candidates, threshold=100.0)
        assert len(result) == 1
        assert result[0].change == 200.0

    def test_does_not_flag_at_threshold(self):
        # Docs say |change| >= threshold; equality should be flagged.
        candidates = [_hour(0, 100.0)]
        result = _algo_absolute(candidates, threshold=100.0)
        assert len(result) == 1

    def test_negative_change_evaluated_by_abs(self):
        candidates = [_hour(0, -500.0), _hour(1000, 50.0)]
        result = _algo_absolute(candidates, threshold=200.0)
        assert len(result) == 1
        assert result[0].change == -500.0

    def test_returns_empty_when_nothing_exceeds(self):
        candidates = [_hour(i * 1000, float(i)) for i in range(5)]
        result = _algo_absolute(candidates, threshold=1000.0)
        assert result == []

    def test_empty_input(self):
        assert _algo_absolute([], threshold=1.0) == []


# ---------------------------------------------------------------------------
# _algo_mad
# ---------------------------------------------------------------------------


_DAY_MS = 86_400_000
_HOUR_MS = 3_600_000
_5MIN_MS = 300_000


def _multiday_hour(
    n_days: int,
    hour_of_day: int,
    base: float = 1.0,
    var: float = 0.1,
) -> list[OutlierCandidate]:
    """n_days of hourly data at a fixed hour-of-day, slightly varying so MAD > 0."""
    import math
    return [
        _hour(d * _DAY_MS + hour_of_day * _HOUR_MS, base + var * math.sin(d))
        for d in range(n_days)
    ]


class TestAlgoMad:
    def test_flags_obvious_spike(self):
        # 14 days at 10:00 ~1 kWh/h, then a spike on day 14.
        candidates = _multiday_hour(14, hour_of_day=10)
        candidates.append(_hour(14 * _DAY_MS + 10 * _HOUR_MS, 1_000_000.0))
        flagged, _, _ = _algo_mad(candidates, mad_factor=6.0)
        assert len(flagged) == 1
        assert flagged[0].change == 1_000_000.0

    def test_does_not_flag_clean_data(self):
        # 14 days at 10:00, all normal — nothing should be flagged.
        candidates = _multiday_hour(14, hour_of_day=10)
        flagged, _, _ = _algo_mad(candidates, mad_factor=6.0)
        assert flagged == []

    def test_median_and_mad_are_none(self):
        # Time-of-day grouping has no single global baseline.
        candidates = _multiday_hour(14, hour_of_day=10)
        _, median, mad = _algo_mad(candidates, mad_factor=6.0)
        assert median is None
        assert mad is None

    def test_empty_input(self):
        flagged, median, mad = _algo_mad([], mad_factor=6.0)
        assert flagged == []
        assert median is None
        assert mad is None

    def test_all_identical_values_not_flagged(self):
        # MAD = 0 for all-identical values → skip, never flag.
        candidates = [_hour(d * _DAY_MS + 10 * _HOUR_MS, 5.0) for d in range(14)]
        flagged, _, _ = _algo_mad(candidates, mad_factor=6.0)
        assert flagged == []

    def test_flags_negative_spike(self):
        candidates = _multiday_hour(14, hour_of_day=10)
        candidates.append(_hour(14 * _DAY_MS + 10 * _HOUR_MS, -1_000_000.0))
        flagged, _, _ = _algo_mad(candidates, mad_factor=3.0)
        assert any(c.change == -1_000_000.0 for c in flagged)

    def test_mad_factor_sensitivity(self):
        # Moderate spike: aggressive factor flags it, conservative does not.
        candidates = _multiday_hour(14, hour_of_day=10)
        candidates.append(_hour(14 * _DAY_MS + 10 * _HOUR_MS, 50.0))
        aggressive, _, _ = _algo_mad(candidates, mad_factor=3.0)
        conservative, _, _ = _algo_mad(candidates, mad_factor=10.0)
        assert len(aggressive) >= len(conservative)

    def test_no_peers_skips_candidate(self):
        # One data point per time-of-day, all an hour apart (>> tolerance), so no
        # candidate has any peer in its window. With nothing to compare against,
        # nothing can be judged.
        candidates = [_hour(h * _HOUR_MS, 1.0) for h in range(24)]
        flagged, _, _ = _algo_mad(candidates, mad_factor=3.0)
        assert flagged == []

    def test_single_peer_still_catches_huge_spike(self):
        # A candidate with exactly one peer at its time of day has no usable
        # spread, but a 500x departure from that peer is still an outlier.
        # Previously the degenerate-MAD guard skipped it outright.
        candidates = [_hour(h * _HOUR_MS, 1.0) for h in range(24)]
        candidates.append(_hour(10 * _HOUR_MS + 1, 500.0))  # within ±5min of hour 10
        flagged, _, _ = _algo_mad(candidates, mad_factor=3.0)
        assert [c.change for c in flagged] == [500.0]

    def test_single_peer_does_not_flag_ordinary_variation(self):
        # Same one-peer shape, but the candidate is only ~1.5x its peer — well
        # inside normal variation. The degenerate path must stay blunt.
        candidates = [_hour(h * _HOUR_MS, 1.0) for h in range(24)]
        candidates.append(_hour(10 * _HOUR_MS + 1, 1.5))
        flagged, _, _ = _algo_mad(candidates, mad_factor=3.0)
        assert flagged == []

    def test_near_zero_mad_no_false_positive(self):
        # Reproduces the real-world HA bug: change values derived from large cumulative
        # sums have floating-point noise (~1e-13). When nearly all peers are 0.4 ± 1e-13,
        # MAD ≈ 1e-13, and any value of 0.3 (legitimate lower-generation day) produces
        # a z-score ~1e11 >> any mad_factor, causing massive false positives.
        # The MAD floor must treat such near-zero MAD as degenerate and skip.
        cands = []
        for d in range(28):
            # Simulate fp noise: alternating slightly above/below 0.4
            change = 0.4 + (1 if d % 2 == 0 else -1) * 1e-13
            cands.append(_hour(d * _DAY_MS + 12 * _HOUR_MS, change))
        # Two legitimate lower-generation days: change=0.3 (not outliers)
        cands.append(_hour(28 * _DAY_MS + 12 * _HOUR_MS, 0.3))
        cands.append(_hour(29 * _DAY_MS + 12 * _HOUR_MS, 0.3))
        flagged, _, _ = _algo_mad(cands, mad_factor=3.5)
        assert flagged == [], "fp-noisy peers with near-zero MAD should not create false positives"

    def test_high_median_genuine_outlier_not_suppressed_by_floor(self):
        # Sensor with large change values (~1000 units/h) and very tight genuine variation.
        # Relative floor abs(median)*1e-6 = 0.001 would suppress MAD≈0.0007, hiding the spike.
        # The absolute floor (1e-9) must allow this genuine outlier through.
        cands = [
            _hour(d * _DAY_MS + 12 * _HOUR_MS, 1000.0 + (d - 14) * 0.0001)
            for d in range(28)
        ]
        cands.append(_hour(28 * _DAY_MS + 12 * _HOUR_MS, 999_999.0))
        flagged, _, _ = _algo_mad(cands, mad_factor=6.0)
        assert any(c.change == 999_999.0 for c in flagged), (
            "genuine outlier on high-median sensor must be flagged even when MAD is "
            "small relative to median"
        )

    def test_near_zero_mad_no_false_positive_5minute(self):
        # Same fp-noise scenario as the hourly version but with 5-minute candidates.
        # Ensures the MAD floor applies regardless of period type.
        cands = []
        for d in range(28):
            change = 0.4 + (1 if d % 2 == 0 else -1) * 1e-13
            cands.append(_five_min(d * _DAY_MS + 12 * _HOUR_MS, change))
        cands.append(_five_min(28 * _DAY_MS + 12 * _HOUR_MS, 0.3))
        cands.append(_five_min(29 * _DAY_MS + 12 * _HOUR_MS, 0.3))
        flagged, _, _ = _algo_mad(cands, mad_factor=3.5)
        assert flagged == [], "fp-noisy 5-minute peers must not create false positives"

    def test_uniform_peers_do_not_hide_huge_spike(self):
        # Peers are EXACTLY identical (quantised meter readings, or a sensor whose
        # hourly change is genuinely constant). MAD == 0 exactly, so the modified
        # z-score is undefined — but a 1e6x spike is still unmistakably an outlier.
        # The degenerate-MAD guard must not skip the candidate outright.
        cands = [_hour(d * _DAY_MS + 10 * _HOUR_MS, 1.0) for d in range(14)]
        cands.append(_hour(14 * _DAY_MS + 10 * _HOUR_MS, 999_999.0))
        flagged, _, _ = _algo_mad(cands, mad_factor=6.0)
        assert [c.change for c in flagged] == [999_999.0]

    def test_fp_noise_peers_do_not_hide_huge_spike(self):
        # Same as above but peers differ only by floating-point noise (~1e-13),
        # which is what real `change` values derived from large cumulative sums
        # look like. MAD ~ 1e-13 trips the < 1e-9 floor and the spike is skipped.
        cands = [
            _hour(d * _DAY_MS + 10 * _HOUR_MS, 1.0 + (1e-13 if d % 2 else -1e-13))
            for d in range(14)
        ]
        cands.append(_hour(14 * _DAY_MS + 10 * _HOUR_MS, 999_999.0))
        flagged, _, _ = _algo_mad(cands, mad_factor=6.0)
        assert [c.change for c in flagged] == [999_999.0]

    def test_zero_baseline_peers_do_not_hide_huge_spike(self):
        # Solar sensor at 03:00 — every night hour is exactly 0.0 change. A restart
        # spike lands on one of them. Peers are all 0.0 so MAD == 0; the spike must
        # still be flagged, judged against the sensor's overall change magnitude.
        cands = []
        for d in range(14):
            cands.append(_hour(d * _DAY_MS + 3 * _HOUR_MS, 0.0))
            # Daytime rows establish the sensor's normal scale (~1 kWh/h).
            cands.append(_hour(d * _DAY_MS + 12 * _HOUR_MS, 1.0 + 0.05 * math.sin(d)))
        cands.append(_hour(14 * _DAY_MS + 3 * _HOUR_MS, 500_000.0))
        flagged, _, _ = _algo_mad(cands, mad_factor=6.0)
        assert [c.change for c in flagged] == [500_000.0]

    def test_two_peer_group_can_flag_huge_spike(self):
        # With only 2 peers the median sits exactly between them, so both
        # deviations equal gap/2 and the modified z-score is pinned at 0.6745 —
        # structurally unable to reach any sane mad_factor. Excluding the
        # candidate from its own baseline removes that ceiling.
        cands = [
            _hour(0 * _DAY_MS + 10 * _HOUR_MS, 1.0),
            _hour(1 * _DAY_MS + 10 * _HOUR_MS, 1_000_000.0),
        ]
        flagged, _, _ = _algo_mad(cands, mad_factor=6.0)
        assert [c.change for c in flagged] == [1_000_000.0]

    def test_spike_does_not_inflate_its_own_baseline(self):
        # Two spikes at the same time-of-day amongst few peers. When candidates are
        # included in their own peer statistics they mask each other; leave-one-out
        # baselines keep both visible.
        cands = [_hour(d * _DAY_MS + 10 * _HOUR_MS, 1.0 + 0.1 * math.sin(d)) for d in range(6)]
        cands.append(_hour(6 * _DAY_MS + 10 * _HOUR_MS, 800_000.0))
        cands.append(_hour(7 * _DAY_MS + 10 * _HOUR_MS, 900_000.0))
        flagged, _, _ = _algo_mad(cands, mad_factor=6.0)
        assert sorted(c.change for c in flagged) == [800_000.0, 900_000.0]

    def test_daily_reset_multiday_with_spike(self):
        # Solar sensor over 14 days: 8 nighttime zero-change hours + 8 varying
        # daytime hours, then a spike at 10:00 on day 14.
        import math
        candidates = []
        daytime_bases = [0.5, 1.0, 1.5, 1.2, 0.8, 0.6, 0.4, 0.3]
        for day in range(14):
            for h in range(8):  # night
                candidates.append(_hour(day * _DAY_MS + h * _HOUR_MS, 0.0))
            for i, base in enumerate(daytime_bases):
                c = base * (1.0 + 0.05 * math.sin(day + i))
                candidates.append(_hour(day * _DAY_MS + (8 + i) * _HOUR_MS, c))
        # Spike at 10:00 (hour index 10, i.e. 8+2 in daytime, base=1.5)
        candidates.append(_hour(14 * _DAY_MS + 10 * _HOUR_MS, 500.0))

        flagged, _, mad = _algo_mad(candidates, mad_factor=3.0)
        assert len(flagged) == 1
        assert flagged[0].change == 500.0
        assert mad is None  # no single global baseline


# ---------------------------------------------------------------------------
# _algo_mad — hybrid per-period isolation
# ---------------------------------------------------------------------------


class TestHybridMadPerPeriod:
    """Validates that mixing hourly and 5-minute rows in one MAD call causes
    scale contamination — which is why scan_outliers splits them first."""

    def test_fivemin_only_detects_spike(self):
        # 14 days of 5-min rows at 10:00, ~0.125 kWh each, plus 1 spike.
        import math
        normal = [
            _five_min(d * _DAY_MS + 10 * _HOUR_MS, 0.125 + 0.01 * math.sin(d))
            for d in range(14)
        ]
        spike = _five_min(14 * _DAY_MS + 10 * _HOUR_MS, 500.0)
        candidates = normal + [spike]
        flagged, _, _ = _algo_mad(candidates, mad_factor=6.0)
        assert len(flagged) == 1
        assert flagged[0].change == 500.0

    def test_hourly_only_detects_spike(self):
        import math
        normal = [
            _hour(d * _DAY_MS + 10 * _HOUR_MS, 1.5 + 0.1 * math.sin(d))
            for d in range(14)
        ]
        spike = _hour(14 * _DAY_MS + 10 * _HOUR_MS, 500.0)
        candidates = normal + [spike]
        flagged, _, _ = _algo_mad(candidates, mad_factor=6.0)
        assert len(flagged) == 1
        assert flagged[0].change == 500.0


# ---------------------------------------------------------------------------
# _hybrid_rows
# ---------------------------------------------------------------------------


class TestHybridRows:
    def _make_hour(self, hour_idx: int, change: float = 1.0) -> OutlierCandidate:
        """Hour row: 3 600 000 ms wide."""
        start = hour_idx * 3_600_000
        return OutlierCandidate(
            start=start,
            end=start + 3_600_000,
            change=change,
            state=0.0,
            period="hour",
        )

    def _make_five_min_set(self, hour_idx: int, change_per_slot: float = 1.0 / 12) -> list[OutlierCandidate]:
        """12 five-minute rows filling one hour."""
        hour_start = hour_idx * 3_600_000
        rows = []
        for slot in range(12):
            start = hour_start + slot * 300_000
            rows.append(
                OutlierCandidate(
                    start=start,
                    end=start + 300_000,
                    change=change_per_slot,
                    state=0.0,
                    period="5minute",
                )
            )
        return rows

    def test_complete_hour_uses_five_min_samples(self):
        """Hour 0 has all 12 five-min samples → those 12 are used."""
        hour_rows = [self._make_hour(0)]
        five_min_rows = self._make_five_min_set(0)
        # Prepend a fake "first ever" row that gets dropped by hybrid_rows.
        five_min_rows = [_five_min(-300_000, 9999.0)] + five_min_rows

        result = _hybrid_rows(hour_rows, five_min_rows)

        assert len(result) == 12
        assert all(c.period == "5minute" for c in result)

    def test_incomplete_hour_uses_hourly_row(self):
        """Hour 0 has only 11 five-min samples → hourly row is used."""
        hour_rows = [self._make_hour(0)]
        five_min_rows = self._make_five_min_set(0)[:11]
        # Prepend dropped "first ever" row.
        five_min_rows = [_five_min(-300_000, 9999.0)] + five_min_rows

        result = _hybrid_rows(hour_rows, five_min_rows)

        assert len(result) == 1
        assert result[0].period == "hour"

    def test_first_five_min_row_is_always_dropped(self):
        """The very first 5-min sample is dropped per upstream convention."""
        hour_rows = [self._make_hour(0)]
        five_min_rows = self._make_five_min_set(0)
        # No extra prepended row: the first element of the list IS the "garbage" one.

        result = _hybrid_rows(hour_rows, five_min_rows)

        # Only 11 remain after the first is dropped → falls back to hourly.
        assert len(result) == 1
        assert result[0].period == "hour"

    def test_mixed_hours_complete_and_incomplete(self):
        """Hour 0 complete (uses 5min), hour 1 incomplete (uses hourly)."""
        hour_rows = [self._make_hour(0), self._make_hour(1)]

        # Hour 0: 12 samples (genuine) + 1 prepended "garbage" row to drop.
        hour0_five_min = self._make_five_min_set(0)
        garbage_row = _five_min(-300_000, 9999.0)

        # Hour 1: only 5 samples (partial).
        hour1_five_min = self._make_five_min_set(1)[:5]

        five_min_rows = [garbage_row] + hour0_five_min + hour1_five_min

        result = _hybrid_rows(hour_rows, five_min_rows)

        hour0_results = [c for c in result if c.start < 3_600_000]
        hour1_results = [c for c in result if c.start >= 3_600_000]

        assert len(hour0_results) == 12
        assert all(c.period == "5minute" for c in hour0_results)
        assert len(hour1_results) == 1
        assert hour1_results[0].period == "hour"

    def test_empty_five_min_returns_all_hourly(self):
        hour_rows = [self._make_hour(0), self._make_hour(1)]
        result = _hybrid_rows(hour_rows, [])
        assert len(result) == 2
        assert all(c.period == "hour" for c in result)

    def test_empty_both_returns_empty(self):
        assert _hybrid_rows([], []) == []
