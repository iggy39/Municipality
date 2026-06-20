from __future__ import annotations

from municipality.resident_gis_registry import (
    find_questions_for_layer_tags,
    load_resident_gis_registry,
    recommend_layers_for_question,
    resident_question_recommendation_matrix,
)


def test_default_resident_gis_registry_loads_and_cross_references_layers() -> None:
    registry = load_resident_gis_registry()

    assert len(registry.layer_groups) >= 15
    assert len(registry.question_archetypes) >= 20
    assert "parcels_cadaster" in registry.layer_by_key
    assert "flooding_damage_and_drainage" in registry.question_by_id


def test_resident_registry_uses_only_verified_public_building_map_aliases() -> None:
    registry = load_resident_gis_registry()

    public_buildings = registry.layer_by_key["public_buildings_assets"]
    playgrounds = registry.layer_by_key["playgrounds_youth_space"]

    assert public_buildings.govmap_aliases == ("public_institutions_survey",)
    assert "layer_210692" not in public_buildings.govmap_aliases
    assert "layer_210697" not in public_buildings.govmap_aliases
    assert playgrounds.govmap_aliases == ()
    assert {layer.alias for layer in playgrounds.govmap_layers} == {"teva_ironi", "teva_ironi_nek"}
    assert {layer.status for layer in playgrounds.govmap_layers} == {"partial"}


def test_pulled_govmap_catalog_layers_are_registry_metadata() -> None:
    registry = load_resident_gis_registry()

    sport = next(layer for layer in registry.layer_by_key["culture_sport_leisure"].govmap_layers if layer.alias == "sport")
    policing = next(layer for layer in registry.layer_by_key["emergency_security_services"].govmap_layers if layer.alias == "situr_ironi")
    ravkav = next(layer for layer in registry.layer_by_key["public_transport_access"].govmap_layers if layer.alias == "ravkav")
    environment_aliases = {layer.alias for layer in registry.layer_by_key["environment_sanitation"].govmap_layers}
    planning_aliases = {layer.alias for layer in registry.layer_by_key["planning_land_use"].govmap_layers}

    assert sport.status == "confirmed"
    assert policing.status == "confirmed"
    assert ravkav.status == "confirmed"
    assert sport.source_scope == "generic_national"
    assert "orange_trash" in environment_aliases
    assert "svivanoiseday" in environment_aliases
    assert "michrazim" in planning_aliases
    assert "mehoziot_app_taba" in planning_aliases


def test_flooding_question_prioritizes_drainage_and_roads() -> None:
    registry = load_resident_gis_registry()
    recommendations = recommend_layers_for_question("flooding_damage_and_drainage", registry, limit=4)
    layer_keys = [row["layer_key"] for row in recommendations]

    assert layer_keys[0] == "drainage_water_sewer"
    assert "roads_parking_public_works" in layer_keys
    assert any("flooding" in row["matching_tags"] for row in recommendations)


def test_antenna_question_combines_protocol_agreement_and_gis_proximity_layers() -> None:
    registry = load_resident_gis_registry()
    recommendations = recommend_layers_for_question("antenna_agreement_near_home", registry, limit=5)
    layer_keys = [row["layer_key"] for row in recommendations]

    assert "cellular_and_radiation_context" in layer_keys
    assert "parcels_cadaster" in layer_keys
    assert "education_facilities" in layer_keys


def test_question_matrix_contains_concise_resident_question_and_layers() -> None:
    registry = load_resident_gis_registry()
    matrix = resident_question_recommendation_matrix(registry, layer_limit=3)

    assert matrix
    assert all(row["resident_question_he"] for row in matrix)
    assert all(1 <= len(row["recommended_layers"]) <= 3 for row in matrix)


def test_find_questions_by_tags_supports_reverse_research() -> None:
    registry = load_resident_gis_registry()
    rows = find_questions_for_layer_tags(["elderly", "welfare", "demographics"], registry, limit=3)

    assert rows[0]["question_id"] == "elderly_winter_vulnerability"
    assert "elderly" in rows[0]["matching_tags"]
