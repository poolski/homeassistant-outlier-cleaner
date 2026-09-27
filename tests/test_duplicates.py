"""Pure-function tests for duplicate-entity detection. No HA imports."""

from __future__ import annotations

from custom_components.recorder_toolkit.duplicates import (
    build_candidate_pairs,
    name_similarity,
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
