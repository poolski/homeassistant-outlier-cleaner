"""Outlier detection for Home Assistant long-term statistics.

Replicates the algorithm used by the Developer Tools > Statistics > Outliers
feature, plus threshold-based and MAD-based methods safer for scheduled use.

Reference: home-assistant/frontend
  src/panels/config/developer-tools/statistics/dialog-statistics-adjust-sum.ts

Key facts mirrored from the upstream implementation:
  * Outliers operate on the per-period ``change`` field, NOT on ``state`` or ``sum``.
  * For "hour" period: every record's ``change`` is examined.
  * For "5minute" period: the FIRST datapoint is dropped (upstream convention —
    it contains the entire historical sum as its change).
  * In the "hybrid" mode (what the dev-tools dialog does): fetch BOTH hour and
    5minute data; for each hour, if all 12 five-minute samples exist use them,
    otherwise fall back to the hourly value.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Iterable, Literal

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import (
    list_statistic_ids,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

Period = Literal["hour", "5minute", "hybrid"]
Method = Literal["top_n", "absolute", "mad"]


@dataclass
class OutlierCandidate:
    """A single statistics row flagged as an outlier."""

    start: int  # ms epoch
    end: int    # ms epoch
    change: float
    state: float | None
    period: str  # "hour" or "5minute"

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start,
            "end": self.end,
            "change": self.change,
            "state": self.state,
            "period": self.period,
        }


@dataclass
class OutlierReport:
    """Result of an outlier scan."""

    statistic_id: str
    method: Method
    period_requested: Period
    candidates: list[OutlierCandidate] = field(default_factory=list)
    median: float | None = None
    mad: float | None = None
    scanned_rows: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "statistic_id": self.statistic_id,
            "method": self.method,
            "period_requested": self.period_requested,
            "candidates": [c.to_dict() for c in self.candidates],
            "median": self.median,
            "mad": self.mad,
            "scanned_rows": self.scanned_rows,
        }


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------


async def get_sum_statistic_ids(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Return statistic IDs that have a sum, enriched with friendly names from hass.states."""
    recorder = get_instance(hass)
    all_ids = await recorder.async_add_executor_job(list_statistic_ids, hass)
    results = []
    for s in all_ids:
        if not s.get("has_sum"):
            continue
        entry = dict(s)
        if not entry.get("name"):
            state = hass.states.get(s["statistic_id"])
            if state:
                entry["name"] = state.attributes.get("friendly_name")
        results.append(entry)
    return results


async def is_sum_statistic(hass: HomeAssistant, statistic_id: str) -> bool:
    """Check that a given statistic_id supports sum adjustment."""
    sums = await get_sum_statistic_ids(hass)
    return any(s["statistic_id"] == statistic_id for s in sums)


# ---------------------------------------------------------------------------
# Data fetch (read-only; uses the recorder's public Python API)
# ---------------------------------------------------------------------------


async def _fetch_period(
    hass: HomeAssistant,
    statistic_id: str,
    period: Literal["hour", "5minute"],
    start_ts: float | None = None,
    end_ts: float | None = None,
) -> list[dict[str, Any]]:
    """Fetch raw statistics rows for one period via the recorder API."""
    recorder = get_instance(hass)
    start_time = (
        datetime.fromtimestamp(start_ts, tz=timezone.utc)
        if start_ts is not None
        else datetime(1970, 1, 1, tzinfo=timezone.utc)
    )
    end_time = (
        datetime.fromtimestamp(end_ts, tz=timezone.utc)
        if end_ts is not None
        else dt_util.utcnow()
    )

    raw = await recorder.async_add_executor_job(
        statistics_during_period,
        hass,
        start_time,
        end_time,
        {statistic_id},
        period,
        None,
        {"change", "state"},
    )
    return raw.get(statistic_id, []) or []


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------


def _to_ms_epoch(value: Any) -> int:
    """Normalise a start/end field to a millisecond epoch int."""
    if isinstance(value, (int, float)):
        return int(value * 1000) if value < 1e12 else int(value)
    if isinstance(value, datetime):
        return int(value.timestamp() * 1000)
    raise TypeError(f"Unexpected start/end type: {type(value)!r}")


def _normalise_rows(
    rows: Iterable[dict[str, Any]], period_label: str
) -> list[OutlierCandidate]:
    """Convert raw recorder rows to OutlierCandidate, dropping rows with no change."""
    out: list[OutlierCandidate] = []
    for r in rows:
        change = r.get("change")
        if change is None:
            continue
        out.append(
            OutlierCandidate(
                start=_to_ms_epoch(r["start"]),
                end=_to_ms_epoch(r["end"]),
                change=float(change),
                state=float(r["state"]) if r.get("state") is not None else None,
                period=period_label,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Detection algorithms
# ---------------------------------------------------------------------------


def _algo_top_n(
    candidates: list[OutlierCandidate], top_n: int
) -> list[OutlierCandidate]:
    """Top N by |change|, descending — matches the dev-tools JS exactly.

    Not safe for unattended scheduled runs: it always returns something.
    """
    return sorted(candidates, key=lambda c: abs(c.change), reverse=True)[:top_n]


def _algo_absolute(
    candidates: list[OutlierCandidate], threshold: float
) -> list[OutlierCandidate]:
    """Flag any row whose |change| >= threshold."""
    return [c for c in candidates if abs(c.change) >= threshold]


_DAY_MS = 24 * 3_600_000
_HOUR_MS = 3_600_000

# Per-period clock-jitter tolerance for time-of-day peer matching.
_TOD_TOLERANCE_MS: dict[str, int] = {
    "5minute": 30_000,   # ±30 seconds
    "hour":   300_000,   # ±5 minutes
}

# Below this, a MAD is indistinguishable from floating-point noise. HA computes
# `change` as sum[i] - sum[i-1]; for sums up to ~50 000 kWh fp error is ~1e-12,
# so any MAD >= 1e-9 reflects real variation.
_MAD_EPSILON = 1e-9

# When the peer baseline is degenerate (every peer effectively identical) the
# modified z-score is undefined, so magnitude is compared against the baseline
# scale instead. A candidate must exceed the baseline by this ratio — two orders
# of magnitude — to be flagged. Deliberately blunt: this path only exists to
# catch spikes that are obviously impossible, not to make fine judgements.
_DEGENERATE_MAD_RATIO = 100.0


def _algo_mad(
    candidates: list[OutlierCandidate], mad_factor: float
) -> tuple[list[OutlierCandidate], float | None, float | None]:
    """MAD with time-of-day peer grouping.

    Each candidate is judged against all other candidates that fall at the
    same time of day, within the period-appropriate tolerance:
      * ``"5minute"`` period: ±30 s
      * ``"hour"`` period:   ±5 min

    The candidate is excluded from its own baseline (leave-one-out). Including it
    lets a spike drag the median toward itself and inflate the MAD, capping the
    achievable z-score — with only two peers the median lands exactly between
    them and the score is pinned at 0.6745 no matter how large the spike is.

    When the peers are effectively identical the MAD collapses to ~0 and the
    z-score is undefined. Rather than skip such candidates (which hides spikes
    precisely on the flattest, most predictable sensors), magnitude is compared
    against the baseline scale — see ``_DEGENERATE_MAD_RATIO``.

    Complexity: O(N × D) where D = distinct time-of-day buckets (≤24 for hourly,
    ≤288 for 5-minute). For 8 000 rows × 24 buckets ≈ 192 000 ops vs 64 M for O(N²).

    Returns ``(flagged, None, None)`` — no single global baseline exists.
    """
    if not candidates:
        return [], None, None

    # Fallback scale for the degenerate-MAD path when the local peer median is
    # itself ~0 (e.g. a solar sensor's night hours are all exactly 0.0). The
    # median of non-zero |change| across the whole scan describes the sensor's
    # normal operating magnitude and is robust to a handful of spikes.
    nonzero_changes = sorted(abs(c.change) for c in candidates if c.change != 0)
    global_scale = _median_sorted(nonzero_changes)

    # Pre-group by exact time-of-day ms for O(log D) range lookup per candidate.
    tod_groups: dict[int, list[OutlierCandidate]] = {}
    for c in candidates:
        key = c.start % _DAY_MS
        tod_groups.setdefault(key, []).append(c)
    tod_keys = sorted(tod_groups)

    flagged: list[OutlierCandidate] = []
    for c in candidates:
        tolerance = _TOD_TOLERANCE_MS.get(c.period, _HOUR_MS)
        tod = c.start % _DAY_MS
        lo_tod = tod - tolerance
        hi_tod = tod + tolerance

        peers: list[OutlierCandidate] = []
        lo = bisect_left(tod_keys, lo_tod)
        hi = bisect_right(tod_keys, hi_tod)
        for key in tod_keys[lo:hi]:
            peers.extend(tod_groups[key])

        # Handle midnight wrap-around (tolerance window crosses 00:00).
        if lo_tod < 0:
            wrap_lo = bisect_left(tod_keys, lo_tod + _DAY_MS)
            for key in tod_keys[wrap_lo:]:
                peers.extend(tod_groups[key])
        elif hi_tod >= _DAY_MS:
            wrap_hi = bisect_right(tod_keys, hi_tod - _DAY_MS)
            for key in tod_keys[:wrap_hi]:
                peers.extend(tod_groups[key])

        # Leave-one-out: the candidate must not contribute to its own baseline.
        others = [p for p in peers if p is not c]
        nonzero_peers = [p for p in others if p.change != 0]
        stat_peers = nonzero_peers if len(nonzero_peers) >= 2 else others
        if not stat_peers:
            continue

        values = [p.change for p in stat_peers]
        median = _median_sorted(sorted(values))
        mad = _median_sorted(sorted(abs(v - median) for v in values))
        deviation = abs(c.change - median)

        if mad < _MAD_EPSILON:
            # Degenerate baseline — no usable spread, so the z-score is
            # undefined. Judge on raw magnitude relative to the baseline scale.
            scale = abs(median) if abs(median) >= _MAD_EPSILON else global_scale
            if scale < _MAD_EPSILON:
                continue
            if deviation >= _DEGENERATE_MAD_RATIO * scale:
                flagged.append(c)
            continue

        if 0.6745 * deviation / mad >= mad_factor:
            flagged.append(c)

    return flagged, None, None


def _median_sorted(sorted_values: list[float]) -> float:
    n = len(sorted_values)
    if n == 0:
        return 0.0
    mid = n // 2
    if n % 2 == 1:
        return sorted_values[mid]
    return (sorted_values[mid - 1] + sorted_values[mid]) / 2.0


# ---------------------------------------------------------------------------
# Hybrid period reconciliation (mirrors the dev-tools dialog)
# ---------------------------------------------------------------------------


def _hybrid_rows(
    hour_rows: list[OutlierCandidate],
    five_min_rows: list[OutlierCandidate],
) -> list[OutlierCandidate]:
    """Reconcile hour + 5-minute rows the way the frontend does.

    For each hour: if it has exactly 12 five-minute samples, use them.
    Otherwise (partial hour) use the hourly value.

    The FIRST five-minute sample is always dropped — it contains the entire
    historical sum as its change (per upstream comment in the frontend code).
    """
    if five_min_rows:
        five_min_rows = five_min_rows[1:]

    by_hour: dict[int, list[OutlierCandidate]] = {h.start: [] for h in hour_rows}
    hour_lookup = {h.start: h for h in hour_rows}
    hour_keys_sorted = sorted(by_hour.keys())

    i = 0
    leftover: list[OutlierCandidate] = []
    for s in sorted(five_min_rows, key=lambda x: x.start):
        matched = False
        while i < len(hour_keys_sorted):
            hour_start = hour_keys_sorted[i]
            hour = hour_lookup[hour_start]
            if s.start >= hour.start and s.end <= hour.end:
                by_hour[hour_start].append(s)
                matched = True
                break
            if s.start >= hour.end:
                i += 1
                continue
            break
        if not matched:
            leftover.append(s)

    result: list[OutlierCandidate] = []
    for hour in hour_rows:
        children = by_hour[hour.start]
        if len(children) == 12:
            result.extend(children)
        else:
            result.append(hour)
    result.extend(leftover)
    return result


# ---------------------------------------------------------------------------
# Top-level scan (read-only)
# ---------------------------------------------------------------------------


async def scan_outliers(
    hass: HomeAssistant,
    statistic_id: str,
    *,
    period: Period = "hybrid",
    method: Method = "top_n",
    top_n: int = 10,
    threshold: float = 0.0,
    mad_factor: float = 6.0,
    lookback_days: int = 0,
    start_ts: float | None = None,
    end_ts: float | None = None,
) -> OutlierReport:
    """Run an outlier scan and return a report. No mutation."""
    # start_ts/end_ts take precedence; convert lookback_days as a fallback
    if start_ts is None and lookback_days > 0:
        start_ts = (dt_util.utcnow() - timedelta(days=lookback_days)).timestamp()

    if period == "hour":
        raw = await _fetch_period(hass, statistic_id, "hour", start_ts=start_ts, end_ts=end_ts)
        candidates = _normalise_rows(raw, "hour")
    elif period == "5minute":
        raw = await _fetch_period(hass, statistic_id, "5minute", start_ts=start_ts, end_ts=end_ts)
        rows = _normalise_rows(raw, "5minute")
        candidates = rows[1:] if rows else []
    else:  # hybrid
        hour_raw = await _fetch_period(hass, statistic_id, "hour", start_ts=start_ts, end_ts=end_ts)
        five_raw = await _fetch_period(hass, statistic_id, "5minute", start_ts=start_ts, end_ts=end_ts)
        candidates = _hybrid_rows(
            _normalise_rows(hour_raw, "hour"),
            _normalise_rows(five_raw, "5minute"),
        )

    scanned = len(candidates)
    median: float | None = None
    mad: float | None = None

    if method == "top_n":
        flagged = _algo_top_n(candidates, top_n)
    elif method == "absolute":
        if threshold <= 0:
            raise ValueError("absolute method requires a positive 'threshold' parameter")
        flagged = _algo_absolute(candidates, threshold)
    elif method == "mad":
        if period == "hybrid":
            # Run MAD independently per period type to avoid scale contamination
            # (hourly changes ~12× larger than 5-minute changes).
            hour_cands = [c for c in candidates if c.period == "hour"]
            fivemin_cands = [c for c in candidates if c.period == "5minute"]
            h_flagged, _, _ = _algo_mad(hour_cands, mad_factor)
            f_flagged, _, _ = _algo_mad(fivemin_cands, mad_factor)
            flagged = h_flagged + f_flagged
        else:
            flagged, median, mad = _algo_mad(candidates, mad_factor)
    else:
        raise ValueError(f"Unknown method: {method!r}")

    flagged = sorted(flagged, key=lambda c: abs(c.change), reverse=True)

    return OutlierReport(
        statistic_id=statistic_id,
        method=method,
        period_requested=period,
        candidates=flagged,
        median=median,
        mad=mad,
        scanned_rows=scanned,
    )
