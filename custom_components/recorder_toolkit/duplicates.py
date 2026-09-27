"""Duplicate-entity detection for the Recorder Toolkit.

Finds groups of numeric sensors that record the same underlying measurement
twice, so their recorder writes can be safely deduplicated. All functions in
this module are pure and HA-free except the `scan_duplicates` orchestrator
added in a later task.
"""

from __future__ import annotations

import fnmatch
import math
import re
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher

from .const import DEFAULT_NAME_SIMILARITY_THRESHOLD

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
