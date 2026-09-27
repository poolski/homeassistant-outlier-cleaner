"""Duplicate-entity detection for the Recorder Toolkit.

Finds groups of numeric sensors that record the same underlying measurement
twice, so their recorder writes can be safely deduplicated. Detection is
split into two phases so a scan never reads statistics for the whole
install up front (that pattern overloaded the recorder executor and could
crash HA on larger installs):

- `find_fuzzy_duplicate_groups` — cheap, automatic, no statistics reads.
  Groups numeric sensors by unit + name similarity from live entity state.
- `correlate_duplicate_group` — only run when explicitly requested for one
  fuzzy group; reads statistics for just that group's members to confirm
  which of them are real duplicates.

All functions in this module are pure and HA-free except these two.
"""

from __future__ import annotations

import fnmatch
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from itertools import combinations
from typing import Any, Iterable

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import (
    DEFAULT_CORRELATION_THRESHOLD,
    DEFAULT_DUPLICATE_LOOKBACK_DAYS,
    DEFAULT_MIN_OVERLAP_POINTS,
    DEFAULT_NAME_SIMILARITY_THRESHOLD,
)

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _normalise_name(statistic_id: str, name: str | None) -> str:
    """Lowercase and strip punctuation from a name, falling back to the id."""
    text = (name or statistic_id).lower()
    return _NON_ALNUM_RE.sub(" ", text).strip()


def name_similarity(entity_a: dict, entity_b: dict) -> float:
    """Return a 0.0-1.0 similarity score between two entities' display names.

    Falls back to the statistic_id when `name` is missing.
    """
    a = _normalise_name(entity_a["statistic_id"], entity_a.get("name"))
    b = _normalise_name(entity_b["statistic_id"], entity_b.get("name"))
    return SequenceMatcher(None, a, b).ratio()


def build_candidate_pairs(
    entities: list[dict],
    name_threshold: float = DEFAULT_NAME_SIMILARITY_THRESHOLD,
) -> list[tuple[str, str]]:
    """Return candidate duplicate pairs: same unit, similar name.

    `entities` items need `statistic_id`, `unit_of_measurement` (entities
    without one are skipped — a missing unit can't be matched), and
    optionally `name`.
    """
    by_unit: dict[str, list[dict]] = {}
    for entity in entities:
        unit = entity.get("unit_of_measurement")
        if not unit:
            continue
        by_unit.setdefault(unit, []).append(entity)

    pairs: set[tuple[str, str]] = set()
    for group in by_unit.values():
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                if name_similarity(group[i], group[j]) >= name_threshold:
                    pair = tuple(
                        sorted((group[i]["statistic_id"], group[j]["statistic_id"]))
                    )
                    pairs.add(pair)  # type: ignore[arg-type]
    return sorted(pairs)


_STATISTICS_ELIGIBLE_STATE_CLASSES = frozenset({"measurement", "total", "total_increasing"})


def list_numeric_sensor_entities(states: Iterable[Any]) -> list[dict]:
    """Build candidate-pairing input from live entity states, not statistics.

    `states` items need `entity_id` and `attributes` (a dict), matching
    `homeassistant.core.State` — e.g. the output of `hass.states.async_all`.
    Deliberately reads nothing from the recorder/statistics tables: fuzzy
    matching only needs unit + name, both already on the live state, so this
    stays a cheap in-memory pass over entities HA already holds.
    """
    entities = []
    for state in states:
        if state.attributes.get("state_class") not in _STATISTICS_ELIGIBLE_STATE_CLASSES:
            continue
        entities.append(
            {
                "statistic_id": state.entity_id,
                "unit_of_measurement": state.attributes.get("unit_of_measurement"),
                "name": state.attributes.get("friendly_name"),
            }
        )
    return entities


def find_fuzzy_duplicate_groups(
    hass: HomeAssistant,
    name_threshold: float = DEFAULT_NAME_SIMILARITY_THRESHOLD,
) -> list[list[str]]:
    """Cheaply group numeric sensors that plausibly record the same thing.

    Unit + name matching only, over live entity state — no statistics reads.
    Safe to run automatically on every scan; confirming a fuzzy group as a
    real duplicate is a separate, explicitly-triggered step
    (`correlate_duplicate_group`), since that step does read statistics.
    """
    entities = list_numeric_sensor_entities(hass.states.async_all("sensor"))
    pairs = build_candidate_pairs(entities, name_threshold)
    return group_duplicates(pairs)


def _start_ms(row: dict) -> int:
    """Normalise a row's `start` field to a millisecond epoch int."""
    value = row["start"]
    if isinstance(value, (int, float)):
        return int(value * 1000) if value < 1e12 else int(value)
    if isinstance(value, datetime):
        return int(value.timestamp() * 1000)
    raise TypeError(f"Unexpected start type: {type(value)!r}")


def pick_correlation_column(rows_a: list[dict], rows_b: list[dict]) -> str | None:
    """Return the value column both series actually populate.

    Prefers "mean" (measurement-class sensors), falls back to "state"
    (sum-class sensors' last raw value per period). Returns None if the two
    series share no usable numeric column.
    """
    for column in ("mean", "state"):
        if any(r.get(column) is not None for r in rows_a) and any(
            r.get(column) is not None for r in rows_b
        ):
            return column
    return None


def align_series(
    rows_a: list[dict], rows_b: list[dict], column: str
) -> tuple[list[float], list[float]]:
    """Inner-join two statistics series by start time, dropping null values."""
    by_start_b = {
        _start_ms(row): row[column] for row in rows_b if row.get(column) is not None
    }
    xs: list[float] = []
    ys: list[float] = []
    for row in rows_a:
        value = row.get(column)
        if value is None:
            continue
        match = by_start_b.get(_start_ms(row))
        if match is not None:
            xs.append(float(value))
            ys.append(float(match))
    return xs, ys


def pearson_correlation(xs: list[float], ys: list[float]) -> float | None:
    """Return the Pearson correlation coefficient, or None if undefined.

    Undefined when there are fewer than 2 aligned points, or either series
    has zero variance (a constant series can't be correlated).
    """
    n = len(xs)
    if n != len(ys) or n < 2:
        return None
    sum_x = sum(xs)
    sum_y = sum(ys)
    sum_xy = sum(x * y for x, y in zip(xs, ys))
    sum_x2 = sum(x * x for x in xs)
    sum_y2 = sum(y * y for y in ys)
    denom = math.sqrt((n * sum_x2 - sum_x**2) * (n * sum_y2 - sum_y**2))
    if denom == 0:
        return None
    return (n * sum_xy - sum_x * sum_y) / denom


def series_diff(values: list[float]) -> list[float]:
    """Return consecutive differences: [values[1]-values[0], values[2]-values[1], ...].

    Used to turn a cumulative/monotonic series (e.g. a sum-class sensor's raw
    "state" reading) into per-period deltas before correlating — two
    unrelated monotonically-increasing meters both trend upward and would
    otherwise correlate almost perfectly on their raw cumulative values.
    Returns an empty list for fewer than 2 input values.
    """
    return [b - a for a, b in zip(values, values[1:])]


def group_duplicates(confirmed_pairs: list[tuple[str, str]]) -> list[list[str]]:
    """Group confirmed-duplicate pairs into connected components.

    A 3-way duplicate (a-b and b-c both confirmed) becomes one group of
    three, not two overlapping pairs.
    """
    parent: dict[str, str] = {}

    def find(node: str) -> str:
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(a: str, b: str) -> None:
        root_a, root_b = find(a), find(b)
        if root_a != root_b:
            parent[root_a] = root_b

    for a, b in confirmed_pairs:
        union(a, b)

    groups: dict[str, list[str]] = {}
    for node in parent:
        groups.setdefault(find(node), []).append(node)

    return sorted((sorted(members) for members in groups.values()), key=lambda g: g[0])


@dataclass(frozen=True)
class EntityCompleteness:
    """One duplicate-group member's data-completeness stats."""

    entity_id: str
    row_count: int
    earliest_start_ms: int


def rank_by_completeness(
    members: list[EntityCompleteness],
) -> list[EntityCompleteness]:
    """Rank group members best-first: most rows, then earliest start.

    Index 0 is the suggested entity to keep.
    """
    return sorted(members, key=lambda m: (-m.row_count, m.earliest_start_ms))


def _common_glob(entity_ids: list[str]) -> str | None:
    """Return one glob covering every id, or None if they don't share one.

    Strips a trailing run of digits (with an optional separating `_`/`-`)
    from each id; if every id reduces to the same prefix, the glob is
    `<prefix>*`. A single-member group never gets a glob (nothing to gain).
    """
    if len(entity_ids) < 2:
        return None
    prefixes = set()
    for entity_id in entity_ids:
        match = re.match(r"^(.*?[_-]?)\d+$", entity_id)
        prefixes.add(match.group(1) if match else entity_id)
    if len(prefixes) != 1:
        return None
    return f"{next(iter(prefixes))}*"


def _glob_is_safe(glob: str, excluded_ids: set[str], all_known_ids: set[str]) -> bool:
    """A glob is safe only if it matches the excluded ids and nothing else."""
    matched = {eid for eid in all_known_ids if fnmatch.fnmatchcase(eid, glob)}
    return bool(matched) and matched <= excluded_ids


def build_exclude_config(
    exclude_groups: list[list[str]], all_known_entity_ids: set[str]
) -> dict[str, list[str]]:
    """Turn confirmed-exclude groups into a recorder exclude config.

    Prefers a glob per group when one exists and is provably safe (matches
    no entity outside that group); falls back to explicit entity ids
    otherwise, per group.
    """
    globs: list[str] = []
    entities: list[str] = []
    for group in exclude_groups:
        candidate = _common_glob(group)
        if candidate and _glob_is_safe(candidate, set(group), all_known_entity_ids):
            globs.append(candidate)
        else:
            entities.extend(group)

    config: dict[str, list[str]] = {}
    if globs:
        config["entity_globs"] = sorted(globs)
    if entities:
        config["entities"] = sorted(entities)
    return config


def render_exclude_yaml(config: dict[str, list[str]]) -> str:
    """Render a `recorder: exclude:` YAML block for a build_exclude_config result."""
    if not config:
        return ""
    lines = ["recorder:", "  exclude:"]
    if "entity_globs" in config:
        lines.append("    entity_globs:")
        lines.extend(f"      - {glob}" for glob in config["entity_globs"])
    if "entities" in config:
        lines.append("    entities:")
        lines.extend(f"      - {entity_id}" for entity_id in config["entities"])
    return "\n".join(lines) + "\n"


async def correlate_duplicate_group(
    hass: HomeAssistant,
    member_ids: list[str],
    *,
    lookback_days: int = DEFAULT_DUPLICATE_LOOKBACK_DAYS,
    correlation_threshold: float = DEFAULT_CORRELATION_THRESHOLD,
    min_overlap: int = DEFAULT_MIN_OVERLAP_POINTS,
) -> list[list[EntityCompleteness]]:
    """Confirm which members of one fuzzy-matched group are real duplicates.

    Reads statistics only for `member_ids` — the group the caller explicitly
    asked to check — never for the whole install. A fuzzy group can split
    into more than one confirmed group (or none), since fuzzy matching on
    unit + name doesn't guarantee every member actually correlates with
    every other.

    Returns each confirmed group ranked best-first (index 0 = suggested
    keep).
    """
    recorder = get_instance(hass)
    end_time = dt_util.utcnow()
    start_time = end_time - timedelta(days=lookback_days)
    rows_cache: dict[str, list[dict]] = {}

    async def rows_for(statistic_id: str) -> list[dict]:
        if statistic_id not in rows_cache:
            raw = await recorder.async_add_executor_job(
                statistics_during_period,
                hass,
                start_time,
                end_time,
                {statistic_id},
                "hour",
                None,
                {"mean", "state"},
            )
            rows_cache[statistic_id] = raw.get(statistic_id, []) or []
        return rows_cache[statistic_id]

    confirmed_pairs: list[tuple[str, str]] = []
    for a, b in combinations(sorted(member_ids), 2):
        rows_a = await rows_for(a)
        rows_b = await rows_for(b)
        column = pick_correlation_column(rows_a, rows_b)
        if column is None:
            continue
        xs, ys = align_series(rows_a, rows_b, column)
        if len(xs) < min_overlap:
            continue
        if column == "state":
            # "state" is the sum-class fallback (a cumulative/monotonic
            # reading): two unrelated ever-increasing meters both trend
            # upward and would misleadingly correlate on raw values, so
            # correlate per-period deltas instead.
            xs, ys = series_diff(xs), series_diff(ys)
        r = pearson_correlation(xs, ys)
        if r is not None and r >= correlation_threshold:
            confirmed_pairs.append((a, b))

    groups = group_duplicates(confirmed_pairs)

    result: list[list[EntityCompleteness]] = []
    for group in groups:
        members = []
        for entity_id in group:
            rows = rows_cache.get(entity_id, [])
            row_count = len(rows)
            earliest = min((_start_ms(row) for row in rows), default=0)
            members.append(EntityCompleteness(entity_id, row_count, earliest))
        result.append(rank_by_completeness(members))
    return result
