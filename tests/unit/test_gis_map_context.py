from __future__ import annotations

from typing import Any

from municipality.gis_map_context import build_map_context


SOURCE_BASE = {
    "provider_key": "mapi",
    "provenance_level": "official_national",
    "reuse_status": "verified",
    "display_status": "official",
    "source_is_official": True,
    "reuse_is_verified": True,
    "display_as_official": True,
    "attribution": None,
    "license_name": None,
    "license_url": None,
}


def test_map_context_for_parcel_focus_returns_core_layers() -> None:
    session = FakeMapContextSession()

    payload = build_map_context(
        session,
        {
            "intent": "parcel_context",
            "focus": {"focus_type": "parcel", "gush": "7103", "helka": "43", "confidence_label": "גבוהה"},
        },
    )

    assert payload is not None
    assert payload["status"] == "found"
    assert payload["municipality_code"] == "5000"
    layers = {layer["layer_key"]: layer for layer in payload["layers"]}
    assert layers["selected_parcel"]["items"][0]["source"]["source_id"] == "mapi_parcels"
    assert layers["municipality"]["items"][0]["source"]["source_id"] == "moin_municipal_boundaries"
    assert layers["plans"]["items"][0]["plan_number"] == "101-0057273"
    assert layers["transport_stops"]["items"][0]["source"]["source_id"] == "mot_gtfs_stops"
    assert layers["context_roads"]["status"] == "context_only"
    assert "שכבות OSM" in payload["caveats"][0]


def test_map_context_for_missing_plan_returns_explicit_not_found() -> None:
    session = FakeMapContextSession(plan_focus_rows=[])

    payload = build_map_context(session, {"intent": "plan_status", "focus": {"focus_type": "plan", "plan_number": "999-1"}})

    assert payload == {
        "status": "focus_not_found",
        "focus": {"focus_type": "plan", "plan_number": "999-1"},
        "layers": [{"layer_key": "selected_plan", "status": "not_found", "items": [], "count": 0}],
        "caveats": ["לא נמצאה תכנית תואמת ב-XPLAN שנטען למערכת."],
    }


def test_map_context_for_unresolved_relative_place_does_not_query_db() -> None:
    session = FakeMapContextSession()

    payload = build_map_context(
        session,
        {"intent": "services_near_place", "focus": {"focus_type": "unresolved_place", "place_query": "הבית שלי"}},
    )

    assert payload is not None
    assert payload["status"] == "focus_unresolved"
    assert payload["layers"] == []
    assert session.executed_markers == []


def test_map_context_for_point_focus_uses_coverage_for_missing_neighborhoods() -> None:
    session = FakeMapContextSession(neighborhood_rows=[])

    payload = build_map_context(session, {"intent": "activity_near_place", "focus": {"focus_type": "point", "lon": 34.78, "lat": 32.08}})

    assert payload is not None
    layers = {layer["layer_key"]: layer for layer in payload["layers"]}
    assert layers["neighborhoods"]["status"] == "not_enabled"
    assert layers["neighborhoods"]["coverage"]["source_id"] == "tel_aviv_open_data_discovered"


class FakeMapContextSession:
    def __init__(self, *, plan_focus_rows: list[dict[str, Any]] | None = None, neighborhood_rows: list[dict[str, Any]] | None = None) -> None:
        self.plan_focus_rows = plan_focus_rows
        self.neighborhood_rows = neighborhood_rows
        self.executed_markers: list[str] = []

    def execute(self, statement: Any, params: dict[str, Any]) -> "FakeResult":
        sql = str(statement)
        marker = _marker(sql)
        self.executed_markers.append(marker)
        if marker == "parcel_focus":
            return FakeResult([_feature_row("parcel-1", "mapi_parcels", "prov-parcel", label="7103/43", gush="7103", helka="43", lon=34.78, lat=32.08)])
        if marker == "plan_focus":
            return FakeResult(self.plan_focus_rows if self.plan_focus_rows is not None else [_feature_row("plan-focus", "xplan_blue_lines", "prov-plan-focus", label="תכנית", plan_number=params.get("plan_number"), lon=34.78, lat=32.08)])
        if marker == "municipality":
            return FakeResult([_feature_row("boundary-1", "moin_municipal_boundaries", "prov-boundary", label="תל אביב-יפו", municipality_code="5000", municipality_name_he="תל אביב-יפו")])
        if marker == "plans":
            return FakeResult([_feature_row("plan-1", "xplan_blue_lines", "prov-plan", label="תכנית מגורים", plan_number="101-0057273")])
        if marker == "neighborhoods":
            return FakeResult(self.neighborhood_rows if self.neighborhood_rows is not None else [_feature_row("neighborhood-1", "tel_aviv_open_data_discovered", "prov-neighborhood", label="רובע לדוגמה", display_status="municipal_license_under_review", reuse_status="municipal_license_under_review", municipality_code="5000")])
        if marker == "transport_stops":
            return FakeResult([_feature_row("stop-1", "mot_gtfs_stops", "prov-stop", label="תחנה", poi_category="transport_stop", distance_m=120.0)])
        if marker == "schools":
            return FakeResult([_feature_row("school-1", "moe_school_coordinates", "prov-school", label="בית ספר", poi_category="school", distance_m=210.0)])
        if marker == "context_geometry":
            return FakeResult([_feature_row("road-1", "osm_context", "prov-road", label="דרך", display_status="context_only", provenance_level="context", poi_category=None)])
        if marker == "buildings":
            return FakeResult([])
        if marker == "coverage":
            layer_key = params.get("layer_key")
            if layer_key == "neighborhoods":
                return FakeResult([_coverage_row(layer_key="neighborhoods", status="not_enabled", source_id="tel_aviv_open_data_discovered")])
            if layer_key == "buildings":
                return FakeResult([_coverage_row(layer_key="buildings", status="context_available", source_id="osm_context", display_status="context_only", provenance_level="context")])
            return FakeResult([])
        return FakeResult([])


class FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def mappings(self) -> "FakeResult":
        return self

    def first(self) -> dict[str, Any] | None:
        return self.rows[0] if self.rows else None

    def all(self) -> list[dict[str, Any]]:
        return self.rows


def _marker(sql: str) -> str:
    start = sql.find("map_context:")
    if start < 0:
        return "unknown"
    start += len("map_context:")
    end = sql.find(" ", start)
    newline = sql.find("*", start)
    candidates = [value for value in (end, newline) if value >= 0]
    return sql[start : min(candidates) if candidates else len(sql)].strip()


def _feature_row(
    row_id: str,
    source_id: str,
    provenance_id: str,
    *,
    label: str,
    display_status: str = "official",
    provenance_level: str = "official_national",
    reuse_status: str = "verified",
    **extra: Any,
) -> dict[str, Any]:
    return {
        "id": row_id,
        "feature_source_id": source_id,
        "source_id": source_id,
        "provenance_id": provenance_id,
        "source_name_he": source_id,
        "source_name_en": source_id,
        "label": label,
        "validation_status": "valid",
        **SOURCE_BASE,
        "display_status": display_status,
        "provenance_level": provenance_level,
        "reuse_status": reuse_status,
        **extra,
    }


def _coverage_row(*, layer_key: str, status: str, source_id: str, display_status: str = "municipal_license_under_review", provenance_level: str = "official_municipal") -> dict[str, Any]:
    return {
        "municipality_code": "5000",
        "municipality_name_he": "תל אביב-יפו",
        "layer_key": layer_key,
        "status": status,
        "source_id": source_id,
        "feature_source_id": source_id,
        "feature_count": 0,
        "source_name_he": source_id,
        "source_name_en": source_id,
        **SOURCE_BASE,
        "display_status": display_status,
        "provenance_level": provenance_level,
    }
