"""Duplicate-entity detection for the Recorder Toolkit.

Finds groups of numeric sensors that record the same underlying measurement
twice, so their recorder writes can be safely deduplicated. All functions in
this module are pure and HA-free except the `scan_duplicates` orchestrator
added in a later task.
"""

from __future__ import annotations

import re
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
