"""Pure-function tests for duplicate-entity detection. No HA imports."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from custom_components.recorder_toolkit.duplicates import (
    EntityCompleteness,
    align_series,
    build_candidate_pairs,
    group_duplicates,
    name_similarity,
    pearson_correlation,
    pick_correlation_column,
    rank_by_completeness,
)


def _entity(statistic_id: str, unit: str | None, name: str | None = None) -> dict:
    return {"statistic_id": statistic_id, "unit_of_measurement": unit, "name": name}


def test_name_similarity_identical_names_scores_one():
    a = _entity("sensor.kitchen_power", "W", "Kitchen Power")
    b = _entity("sensor.kitchen_power_2", "W", "Kitchen Power")
    assert name_similarity(a, b) == 1.0


def test_name_similarity_falls_back_to_entity_id_when_name_missing():
    a = _entity("sensor.kitchen_power", "W", None)
    b = _entity("sensor.kitchen_power_2", "W", None)
    assert name_similarity(a, b) > 0.8


def test_name_similarity_dissimilar_names_scores_low():
    a = _entity("sensor.kitchen_power", "W", "Kitchen Power")
    b = _entity("sensor.attic_temperature", "W", "Attic Temperature")
    assert name_similarity(a, b) < 0.5


def test_build_candidate_pairs_matches_same_unit_similar_name():
    entities = [
        _entity("sensor.kitchen_power", "W", "Kitchen Power"),
        _entity("sensor.kitchen_power_2", "W", "Kitchen Power"),
    ]
    assert build_candidate_pairs(entities) == [
        ("sensor.kitchen_power", "sensor.kitchen_power_2")
    ]


def test_build_candidate_pairs_rejects_different_units():
    entities = [
        _entity("sensor.kitchen_power_w", "W", "Kitchen Power"),
        _entity("sensor.kitchen_power_kw", "kW", "Kitchen Power"),
    ]
    assert build_candidate_pairs(entities) == []


def test_build_candidate_pairs_rejects_dissimilar_names_same_unit():
    entities = [
        _entity("sensor.kitchen_power", "W", "Kitchen Power"),
        _entity("sensor.attic_light_power", "W", "Attic Light Power"),
    ]
    assert build_candidate_pairs(entities, name_threshold=0.9) == []


def test_build_candidate_pairs_skips_entities_without_unit():
    entities = [
        _entity("sensor.kitchen_power", None, "Kitchen Power"),
        _entity("sensor.kitchen_power_2", None, "Kitchen Power"),
    ]
    assert build_candidate_pairs(entities) == []


def _row(hour: int, **values) -> dict:
    return {"start": datetime(2026, 1, 1, hour, tzinfo=timezone.utc), **values}


def test_pick_correlation_column_prefers_mean_when_both_have_it():
    rows_a = [_row(0, mean=1.0, state=None)]
    rows_b = [_row(0, mean=2.0, state=None)]
    assert pick_correlation_column(rows_a, rows_b) == "mean"


def test_pick_correlation_column_falls_back_to_state():
    rows_a = [_row(0, mean=None, state=1.0)]
    rows_b = [_row(0, mean=None, state=2.0)]
    assert pick_correlation_column(rows_a, rows_b) == "state"


def test_pick_correlation_column_none_when_no_shared_column():
    rows_a = [_row(0, mean=1.0, state=None)]
    rows_b = [_row(0, mean=None, state=2.0)]
    assert pick_correlation_column(rows_a, rows_b) is None


def test_align_series_inner_joins_by_start_and_drops_nulls():
    rows_a = [_row(0, state=1.0), _row(1, state=2.0), _row(2, state=None)]
    rows_b = [_row(0, state=10.0), _row(2, state=30.0)]
    xs, ys = align_series(rows_a, rows_b, "state")
    assert xs == [1.0]
    assert ys == [10.0]


def test_pearson_correlation_perfect_positive():
    xs = [1.0, 2.0, 3.0, 4.0]
    ys = [10.0, 20.0, 30.0, 40.0]
    assert pearson_correlation(xs, ys) == pytest.approx(1.0)


def test_pearson_correlation_uncorrelated():
    xs = [1.0, 2.0, 3.0, 4.0]
    ys = [4.0, 1.0, 4.0, 1.0]
    r = pearson_correlation(xs, ys)
    assert r is not None
    assert r < 0.5


def test_pearson_correlation_none_when_too_few_points():
    assert pearson_correlation([1.0], [2.0]) is None


def test_pearson_correlation_none_when_zero_variance():
    # A constant series has zero variance; correlation is undefined, not an error.
    assert pearson_correlation([5.0, 5.0, 5.0], [1.0, 2.0, 3.0]) is None


def test_group_duplicates_merges_transitive_pairs_into_one_group():
    # a-b and b-c correlated => {a, b, c} is one group, even though a-c
    # was never directly compared.
    pairs = [("sensor.a", "sensor.b"), ("sensor.b", "sensor.c")]
    assert group_duplicates(pairs) == [["sensor.a", "sensor.b", "sensor.c"]]


def test_group_duplicates_keeps_disjoint_pairs_separate():
    pairs = [("sensor.a", "sensor.b"), ("sensor.c", "sensor.d")]
    assert group_duplicates(pairs) == [
        ["sensor.a", "sensor.b"],
        ["sensor.c", "sensor.d"],
    ]


def test_group_duplicates_empty_input_returns_empty_list():
    assert group_duplicates([]) == []


def test_rank_by_completeness_prefers_more_rows():
    members = [
        EntityCompleteness("sensor.a", row_count=10, earliest_start_ms=1000),
        EntityCompleteness("sensor.b", row_count=100, earliest_start_ms=2000),
    ]
    ranked = rank_by_completeness(members)
    assert [m.entity_id for m in ranked] == ["sensor.b", "sensor.a"]


def test_rank_by_completeness_ties_broken_by_earliest_start():
    members = [
        EntityCompleteness("sensor.a", row_count=100, earliest_start_ms=2000),
        EntityCompleteness("sensor.b", row_count=100, earliest_start_ms=1000),
    ]
    ranked = rank_by_completeness(members)
    assert [m.entity_id for m in ranked] == ["sensor.b", "sensor.a"]
