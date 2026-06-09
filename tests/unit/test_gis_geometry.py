from __future__ import annotations

from municipality.gis_geometry import validate_geojson_geometry


def test_geometry_validation_accepts_valid_polygon() -> None:
    result = validate_geojson_geometry(
        {
            "type": "Polygon",
            "coordinates": [[[34.7, 31.7], [34.8, 31.7], [34.8, 31.8], [34.7, 31.8], [34.7, 31.7]]],
        },
        expected_type="Polygon",
    )

    assert result.status == "valid"
    assert result.warnings == []


def test_geometry_validation_rejects_null_empty_and_type_mismatch() -> None:
    assert validate_geojson_geometry(None, expected_type="Point").status == "invalid"

    empty = validate_geojson_geometry({"type": "Point", "coordinates": []}, expected_type="Point")
    assert empty.status == "invalid"
    assert "empty_geometry" in empty.warnings

    mismatch = validate_geojson_geometry({"type": "Point", "coordinates": [34.7, 31.7]}, expected_type="Polygon")
    assert mismatch.status == "invalid"
    assert "type_mismatch" in mismatch.warnings


def test_geometry_validation_flags_out_of_bbox_without_deleting() -> None:
    result = validate_geojson_geometry({"type": "Point", "coordinates": [10.0, 10.0]}, expected_type="Point")

    assert result.status == "warning"
    assert "outside_expected_bbox" in result.warnings


def test_geometry_validation_rejects_invalid_point_coordinates_and_zero_zero() -> None:
    invalid_range = validate_geojson_geometry({"type": "Point", "coordinates": [200.0, 31.7]}, expected_type="Point")
    zero_zero = validate_geojson_geometry({"type": "Point", "coordinates": [0.0, 0.0]}, expected_type="Point")

    assert invalid_range.status == "invalid"
    assert "invalid_coordinate_range" in invalid_range.warnings
    assert zero_zero.status == "invalid"
    assert "zero_zero_coordinate" in zero_zero.warnings


def test_geometry_validation_marks_self_intersection_as_repairable() -> None:
    result = validate_geojson_geometry(
        {
            "type": "Polygon",
            "coordinates": [[[34.7, 31.7], [34.9, 31.9], [34.7, 31.9], [34.9, 31.7], [34.7, 31.7]]],
        },
        expected_type="Polygon",
    )

    assert result.status == "repaired"
    assert "st_makevalid_applied" in result.warnings
