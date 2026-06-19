from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlencode

import httpx
from pyproj import Transformer


DEFAULT_GOVMAP_API_KEY = "cb6f6d3f-758c-4d05-8308-1a09f8da9d70"
DEFAULT_GOVMAP_ORIGIN = "https://horizonscanninglab.org"
DEFAULT_GOVMAP_CENTER_X = 180428.96
DEFAULT_GOVMAP_CENTER_Y = 665728.35
DEFAULT_GOVMAP_RADIUS_M = 3000.0
DEFAULT_GOVMAP_LEVEL = 8

GOVMAP_BASE_URL = "https://www.govmap.gov.il"
GOVMAP_SEARCH_PATH = "/api/search-service/api-search"
GOVMAP_SPATIAL_PATH = "/api/spatial-analysis/layer-features-by-location"
GOVMAP_SELECT_FEATURE_PATH = "/api/spatial-analysis/select-feature-on-map"


@dataclass(frozen=True)
class GovMapLayerSpec:
    alias: str
    dashboard_key: str
    label_he: str
    category: str
    fields: tuple[str, ...]
    color: str
    geometry_kind: str = "point"


GOVMAP_DASHBOARD_LAYERS: tuple[GovMapLayerSpec, ...] = (
    GovMapLayerSpec("PARCEL_ALL", "nearby_parcels", "חלקות", "parcel", ("objectid", "GUSH_NUM", "PARCEL"), "#7c4dce", "polygon"),
    GovMapLayerSpec("SUB_GUSH_ALL", "nearby_parcels", "גושים", "parcel", ("objectid", "GUSH_NUM"), "#7c4dce", "polygon"),
    GovMapLayerSpec("retzefmigrashim", "plans", "מגרשי תב\"ע", "planning", ("objectid", "migrash", "pl_number", "pl_name"), "#7c4dce", "polygon"),
    GovMapLayerSpec("migrashim_msbs", "plans", "מגרשים - משרד הבינוי", "planning", ("objectid", "migrash", "pl_number", "plan_num", "name"), "#7c4dce", "polygon"),
    GovMapLayerSpec("neighborhoods_area", "neighborhoods", "שכונות", "municipal", ("objectid", "name", "shem", "SHEM"), "#7c4dce", "polygon"),
    GovMapLayerSpec("regional_authorities", "municipal_boundaries", "רשויות מקומיות", "municipal", ("objectid", "name", "shem", "SHEM"), "#7c4dce", "polygon"),
    GovMapLayerSpec("bus_stops", "transport", "תחבורה ציבורית", "transport_stop", ("objectid", "stop_name", "stop_id", "name"), "#0b68d1"),
    GovMapLayerSpec("school", "education", "בתי ספר", "school", ("objectid", "name", "shem_mosad", "SEMEL_MOSAD", "school_name"), "#f97316"),
    GovMapLayerSpec("kids_g", "education", "גני ילדים", "school", ("objectid", "name", "shem_mosad", "SEMEL_MOSAD", "gan_name"), "#f97316"),
    GovMapLayerSpec("clinics", "municipal_pois", "מרפאות", "public_building", ("objectid", "name", "address", "city"), "#f97316"),
    GovMapLayerSpec("pharmacies", "municipal_pois", "בתי מרקחת", "public_building", ("objectid", "name", "address", "city"), "#f97316"),
    GovMapLayerSpec("post_israel", "municipal_pois", "דואר ישראל", "public_building", ("objectid", "name", "address", "city"), "#f97316"),
    GovMapLayerSpec("mikve", "municipal_pois", "מקוואות", "public_building", ("objectid", "name", "address", "yeshuv", "phone"), "#f97316"),
    GovMapLayerSpec("bombshelters", "emergency", "מקלטים", "emergency", ("objectid", "name", "address"), "#64748b"),
    GovMapLayerSpec("aq_realtime", "environment", "מדד זיהום אוויר", "environment", ("objectid", "name", "station_name", "value", "pollutant", "time"), "#18a865"),
    GovMapLayerSpec("cell_active", "environment", "אנטנות סלולריות", "environment", ("objectid", "name", "site_name", "company", "address"), "#18a865"),
    GovMapLayerSpec("GASSTATIONS", "infrastructure", "תחנות דלק", "public_building", ("objectid", "name", "company", "address"), "#f97316"),
)

GOVMAP_DEFAULT_VISIBLE_LAYER_ALIASES = (
    "neighborhoods_area",
)

GOVMAP_ADDRESS_DATATYPES = ("address", "street", "settlement")


class GovMapClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        origin: str | None = None,
        timeout_seconds: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("GOVMAP_API_KEY") or DEFAULT_GOVMAP_API_KEY
        self.origin = (origin or os.environ.get("GOVMAP_ORIGIN") or DEFAULT_GOVMAP_ORIGIN).rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": self.origin,
            "Referer": f"{self.origin}/",
        }

    def search(self, search_text: str, *, max_results: int = 5, layers: tuple[str, ...] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "searchText": search_text,
            "language": "he",
            "maxResults": max_results,
            "isAccurate": True,
            "apiKey": self.api_key,
        }
        if layers:
            payload["layers"] = list(layers)
        with httpx.Client(timeout=self.timeout_seconds, transport=self.transport) as client:
            response = client.post(f"{GOVMAP_BASE_URL}{GOVMAP_SEARCH_PATH}", headers=self.headers, json=payload)
            response.raise_for_status()
            data = response.json()
        return data if isinstance(data, dict) else {"results": [], "resultsCount": 0}

    def features_by_location(self, *, x: float, y: float, radius_m: float, layers: tuple[GovMapLayerSpec, ...]) -> dict[str, Any]:
        payload = {
            "apiToken": self.api_key,
            "data": {
                "geometry": f"POINT({x:.2f} {y:.2f})",
                "radius": min(float(radius_m), 3000.0),
                "layers": [{"name": layer.alias, "fields": list(layer.fields)} for layer in layers],
            },
        }
        with httpx.Client(timeout=self.timeout_seconds, transport=self.transport) as client:
            response = client.post(f"{GOVMAP_BASE_URL}{GOVMAP_SPATIAL_PATH}", headers=self.headers, json=payload)
            response.raise_for_status()
            data = response.json()
        return data if isinstance(data, dict) else {"layers": {}}

    def select_features_on_map(self, *, x: float, y: float, radius_m: float, layer: GovMapLayerSpec) -> list[dict[str, Any]]:
        mercator_x, mercator_y = Transformer.from_crs("EPSG:2039", "EPSG:3857", always_xy=True).transform(float(x), float(y))
        payload = {
            "shapeFilterGeoJson": json.dumps({"type": "Point", "coordinates": [mercator_x, mercator_y]}, separators=(",", ":")),
            "srid": "3857",
            "filters": "[]",
            "radius": min(float(radius_m), 3000.0),
            "layer": {"layerName": layer.alias, "pagination": {"start": 0, "amount": 500}},
            "returnFields": list(layer.fields),
        }
        with httpx.Client(timeout=self.timeout_seconds, transport=self.transport) as client:
            response = client.post(f"{GOVMAP_BASE_URL}{GOVMAP_SELECT_FEATURE_PATH}", headers=self.headers, json=payload)
            response.raise_for_status()
            data = response.json()
        return data if isinstance(data, list) else []


def build_govmap_dashboard_payload(
    *,
    radius_m: float = DEFAULT_GOVMAP_RADIUS_M,
    profile: str = "initial",
    client: GovMapClient | None = None,
    center_x: float | None = None,
    center_y: float | None = None,
    municipality: str | None = None,
    address: str | None = None,
) -> dict[str, Any]:
    client = client or GovMapClient()
    radius_m = min(float(radius_m or DEFAULT_GOVMAP_RADIUS_M), 3000.0)
    center_source = "explicit" if center_x is not None and center_y is not None else "fallback_tel_aviv"
    resolved_center = None
    if center_x is None or center_y is None:
        resolved_center = _resolve_govmap_center(client, address=address, municipality=municipality)
        if resolved_center is not None:
            center_x = resolved_center["x"]
            center_y = resolved_center["y"]
            center_source = resolved_center["source"]
    center_x = float(center_x if center_x is not None else DEFAULT_GOVMAP_CENTER_X)
    center_y = float(center_y if center_y is not None else DEFAULT_GOVMAP_CENTER_Y)
    center = itm_to_wgs84(center_x, center_y)
    govmap_layers = GOVMAP_DASHBOARD_LAYERS
    default_visible_layers = [layer.alias for layer in govmap_layers if layer.alias in GOVMAP_DEFAULT_VISIBLE_LAYER_ALIASES]
    initial_profile = str(profile or "").strip().lower() == "initial"
    spatial_payload: dict[str, Any] = {"layers": {}}
    spatial_error: str | None = None
    address_payload: dict[str, Any] = {"results": [], "resultsCount": 0}
    address_error: str | None = None
    selected_neighborhood: dict[str, Any] | None = None
    selected_neighborhood_error: str | None = None

    if initial_profile:
        spatial_error = "initial_metadata_only"
        address_error = "initial_metadata_only"
        if _live_govmap_enabled():
            try:
                selected_neighborhood = _selected_neighborhood_from_govmap(client, center_x=center_x, center_y=center_y)
            except httpx.HTTPError as exc:
                selected_neighborhood_error = exc.__class__.__name__
    elif _live_govmap_enabled():
        try:
            spatial_payload = client.features_by_location(x=center_x, y=center_y, radius_m=radius_m, layers=govmap_layers)
        except httpx.HTTPError as exc:
            spatial_error = exc.__class__.__name__
        try:
            selected_neighborhood = _selected_neighborhood_from_govmap(client, center_x=center_x, center_y=center_y)
        except httpx.HTTPError as exc:
            selected_neighborhood_error = exc.__class__.__name__
        try:
            address_payload = client.search(os.environ.get("GOVMAP_ADDRESS_SMOKE_QUERY", "דרך מצדה 6 באר שבע"), max_results=3, layers=GOVMAP_ADDRESS_DATATYPES)
        except httpx.HTTPError as exc:
            address_error = exc.__class__.__name__
    else:
        spatial_error = "live_govmap_disabled"
        address_error = "live_govmap_disabled"
        selected_neighborhood_error = "live_govmap_disabled"

    grouped_layers = _dashboard_layers_from_govmap(spatial_payload.get("layers") or {}, govmap_layers, center=center)
    selected_area = _selected_area_feature(center, selected_neighborhood)
    nearby_pois = _nearby_pois_from_layers(grouped_layers)
    address_layer = _address_search_layer(address_payload, address_error=address_error)
    grouped_layers["address_points"] = address_layer

    return {
        "status": "found",
        "real_gis_available": True,
        "provider": "govmap",
        "selected_example": "govmap_resident_point",
        "examples": [{"id": "govmap_resident_point", "label_he": "GovMap - נקודת תושב"}],
        "query": {
            "provider": "govmap",
            "center_x": float(center_x),
            "center_y": float(center_y),
            "center_source": center_source,
            "municipality": municipality,
            "address": address,
            "radius_m": radius_m,
            "profile": profile,
            "address_search_supported": True,
            "address_search_datatypes": list(GOVMAP_ADDRESS_DATATYPES),
        },
        "title_he": "מפת GovMap לתושב: שכבות רשמיות סביב נקודה",
        "center": center,
        "zoom": 14,
        "focus": "govmap_resident_point",
        "visual_context": {
            "mode": "govmap_native",
            "status": "official_native_with_osm_fallback",
            "tile_url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
            "basemap_label_he": "GovMap רשמי · OSM רק אם GovMap נכשל",
        },
        "basemap": {
            "provider": "GovMap",
            "display_status": "official",
            "attribution": "GovMap / Survey of Israel and public layer publishers; OSM only as runtime fallback",
            "tile_url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        },
        "govmap": {
            "enabled": True,
            "origin": client.origin,
            "api_key": client.api_key,
            "iframe_url": build_govmap_url(center_x=center_x, center_y=center_y, layers=tuple(default_visible_layers), level=DEFAULT_GOVMAP_LEVEL),
            "center": {"x": float(center_x), "y": float(center_y), **center},
            "level": DEFAULT_GOVMAP_LEVEL,
            "background": 0,
            "radius_m": radius_m,
            "visible_layers": [layer.alias for layer in govmap_layers],
            "default_visible_layers": default_visible_layers,
            "layer_filters": _govmap_layer_filters(govmap_layers),
            "layer_groups": _govmap_layer_groups(),
            "spatial_status": "available" if spatial_error is None else "degraded",
            "spatial_error": spatial_error,
            "selected_area": selected_neighborhood,
            "selected_area_error": selected_neighborhood_error,
        },
        "parcel": selected_area,
        "primary_feature": selected_area,
        "nearby_pois": nearby_pois,
        "layers": grouped_layers,
        "coverage": _govmap_coverage(grouped_layers),
        "legend": _govmap_legend(),
        "caveats": [
            "GovMap הוא מקור ה-GIS הראשי במפה זו; התשובות נשענות על שכבות GovMap ועל מזהי אובייקטים שהוחזרו מה-API.",
            "הסימונים הצפים מעל המפה הם שכבת תצוגה של הדשבורד; שכבות המקור מוצגות ב-GovMap עצמו.",
            "חיפוש כתובות ורחובות זמין דרך GovMap Search API כ-datatypes: address, street, settlement.",
            "אם GovMap אינו זמין, ניתן לעבור חזרה לנתוני PostGIS מקומיים באמצעות GIS_DASHBOARD_PROVIDER=local או provider=local.",
        ],
    }


def build_govmap_url(*, center_x: float, center_y: float, layers: tuple[str, ...], level: int = DEFAULT_GOVMAP_LEVEL) -> str:
    query = {
        "c": f"{center_x:.2f},{center_y:.2f}",
        "z": str(int(level)),
        "b": "0",
        "et": "1",
        "lang": "he",
        "lay": ",".join(layers),
    }
    return f"{GOVMAP_BASE_URL}/?{urlencode(query)}"


def itm_to_wgs84(x: float, y: float) -> dict[str, float]:
    transformer = Transformer.from_crs("EPSG:2039", "EPSG:4326", always_xy=True)
    lon, lat = transformer.transform(float(x), float(y))
    return {"lon": float(lon), "lat": float(lat)}


def _live_govmap_enabled() -> bool:
    return os.environ.get("GOVMAP_DASHBOARD_LIVE", "1").strip().lower() not in {"0", "false", "no"}


def _resolve_govmap_center(client: GovMapClient, *, address: str | None, municipality: str | None) -> dict[str, Any] | None:
    if not _live_govmap_enabled():
        return None
    for source, raw_text in (("address_search", address), ("municipality_search", municipality)):
        text = _center_search_text(raw_text)
        if not text:
            continue
        try:
            payload = client.search(text, max_results=8)
        except httpx.HTTPError:
            continue
        result = _best_center_search_result(payload.get("results") or [], prefer_settlement=source == "municipality_search")
        point = _point_from_wkt(str(result.get("centroid") or "")) if result else None
        if point is not None:
            return {"x": point[0], "y": point[1], "source": source, "label": result.get("originalText") or result.get("text") or text}
    return None


def _center_search_text(value: str | None) -> str:
    text = str(value or "").replace("_", " ").strip()
    return " ".join(text.split())


def _best_center_search_result(results: list[Any], *, prefer_settlement: bool) -> Mapping[str, Any] | None:
    mappings = [item for item in results if isinstance(item, Mapping) and item.get("centroid")]
    if prefer_settlement:
        for item in mappings:
            if str(item.get("type") or "").lower() == "settlement":
                return item
    return mappings[0] if mappings else None


def _point_from_wkt(value: str) -> tuple[float, float] | None:
    compact = value.strip().upper()
    if not compact.startswith("POINT"):
        return None
    raw = value[value.find("(") + 1 : value.rfind(")")]
    parts = raw.replace(",", " ").split()
    if len(parts) < 2:
        return None
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return None


def _dashboard_layers_from_govmap(layers_payload: Mapping[str, Any], specs: tuple[GovMapLayerSpec, ...], *, center: Mapping[str, float]) -> dict[str, Any]:
    grouped: dict[str, dict[str, Any]] = {}
    display_index = 0
    for spec in specs:
        raw_items = layers_payload.get(spec.alias) if isinstance(layers_payload, Mapping) else []
        raw_items = raw_items if isinstance(raw_items, list) else []
        features = []
        for raw in raw_items:
            if not isinstance(raw, Mapping):
                continue
            display_index += 1
            features.append(_feature_from_govmap_item(spec, raw, center=center, display_index=display_index))
        group = grouped.setdefault(spec.dashboard_key, {"count": 0, "displayed_count": 0, "total_count": 0, "items": [], "status": "not_found", "source_id": "govmap"})
        group["items"].extend(features)
        group["count"] += len(features)
        group["displayed_count"] += len(features)
        group["total_count"] += len(raw_items)
        if raw_items:
            group["status"] = "found"
    for key in ("nearby_parcels", "plans", "municipal_boundaries", "neighborhoods", "municipal_pois", "emergency", "environment", "infrastructure"):
        grouped.setdefault(key, {"count": 0, "displayed_count": 0, "total_count": 0, "items": [], "status": "not_found", "source_id": "govmap"})
    return grouped


def _feature_from_govmap_item(spec: GovMapLayerSpec, item: Mapping[str, Any], *, center: Mapping[str, float], display_index: int) -> dict[str, Any]:
    attributes = item.get("attributes") if isinstance(item.get("attributes"), Mapping) else {}
    object_id = str(item.get("id") or attributes.get("objectid") or attributes.get("ObjectId") or display_index)
    title = _feature_title(spec, attributes, object_id)
    return {
        "id": f"govmap:{spec.alias}:{object_id}",
        "feature_type": "govmap_feature",
        "source_id": f"govmap:{spec.alias}",
        "provenance_id": _provenance_id(spec.alias, object_id, attributes),
        "source": _govmap_source(spec),
        "source_object_id": object_id,
        "govmap_layer": spec.alias,
        "label": title,
        "name_he": title,
        "poi_category": spec.category,
        "geometry_kind": spec.geometry_kind,
        "attributes": dict(attributes),
        "geometry": {"type": "Point", "coordinates": _display_coordinate(center, display_index)},
        "display_geometry_status": "dashboard_display_anchor_not_source_geometry",
    }


def _selected_neighborhood_from_govmap(client: GovMapClient, *, center_x: float, center_y: float) -> dict[str, Any] | None:
    layer = next(layer for layer in GOVMAP_DASHBOARD_LAYERS if layer.alias == "neighborhoods_area")
    items = client.select_features_on_map(x=center_x, y=center_y, radius_m=1, layer=layer)
    if len(items) != 1:
        return None
    item = items[0]
    object_id = str(item.get("objectid") or item.get("OBJECTID") or item.get("id") or "").strip()
    wkt = str(item.get("wkt") or "").strip()
    if not object_id or not wkt:
        return None
    geometry = _web_mercator_wkt_to_geojson(wkt)
    if not geometry:
        return None
    display_wkt = _web_mercator_wkt_to_itm_wkt(wkt)
    return {
        "layer": layer.alias,
        "objectid": object_id,
        "wkt": wkt,
        "display_wkt": display_wkt,
        "display_srid": "2039" if display_wkt else None,
        "geometry": geometry,
        "label": "אזור נבחר",
        "source_id": f"govmap:{layer.alias}",
    }


def _selected_area_feature(center: Mapping[str, float], selected_neighborhood: Mapping[str, Any] | None = None) -> dict[str, Any]:
    geometry = selected_neighborhood.get("geometry") if isinstance(selected_neighborhood, Mapping) else None
    object_id = selected_neighborhood.get("objectid") if isinstance(selected_neighborhood, Mapping) else None
    return {
        "id": f"govmap:neighborhoods_area:{object_id}" if object_id else "govmap:selected-area",
        "feature_type": "selected_area",
        "source_id": "govmap:neighborhoods_area" if object_id else "govmap",
        "provenance_id": f"govmap:neighborhoods_area:{object_id}" if object_id else "govmap:selected-area:not-resolved",
        "source": _govmap_source(None),
        "label": "אזור נבחר",
        "name_he": "אזור נבחר",
        "source_object_id": object_id,
        "govmap_layer": "neighborhoods_area" if object_id else None,
        "geometry": geometry,
        "display_geometry_status": "real_govmap_neighborhood_geometry" if geometry else "not_available",
    }


def _web_mercator_wkt_to_geojson(wkt: str) -> dict[str, Any] | None:
    text = " ".join(str(wkt or "").strip().split())
    upper = text.upper()
    transformer = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
    if upper.startswith("MULTIPOLYGON"):
        body = text[text.find("(") :]
        polygons = []
        for polygon_text in _split_wkt_top_level(_strip_one_outer_wkt_parens(body)):
            rings = []
            for ring_text in _split_wkt_top_level(_strip_one_outer_wkt_parens(polygon_text)):
                ring = _parse_wkt_ring(_strip_outer_wkt_parens(ring_text), transformer)
                if len(ring) >= 4:
                    rings.append(ring)
            if rings:
                polygons.append(rings)
        return {"type": "MultiPolygon", "coordinates": polygons} if polygons else None
    if upper.startswith("POLYGON"):
        body = text[text.find("(") :]
        rings = []
        for ring_text in _split_wkt_top_level(_strip_one_outer_wkt_parens(body)):
            ring = _parse_wkt_ring(_strip_outer_wkt_parens(ring_text), transformer)
            if len(ring) >= 4:
                rings.append(ring)
        return {"type": "Polygon", "coordinates": rings} if rings else None
    return None


def _web_mercator_wkt_to_itm_wkt(wkt: str) -> str | None:
    text = " ".join(str(wkt or "").strip().split())
    upper = text.upper()
    transformer = Transformer.from_crs("EPSG:3857", "EPSG:2039", always_xy=True)
    if upper.startswith("MULTIPOLYGON"):
        body = text[text.find("(") :]
        polygons = []
        for polygon_text in _split_wkt_top_level(_strip_one_outer_wkt_parens(body)):
            rings = []
            for ring_text in _split_wkt_top_level(_strip_one_outer_wkt_parens(polygon_text)):
                ring = _parse_wkt_ring(_strip_outer_wkt_parens(ring_text), transformer)
                if len(ring) >= 4:
                    rings.append(_wkt_ring(ring))
            if rings:
                polygons.append(f"({','.join(rings)})")
        return f"MULTIPOLYGON ({','.join(polygons)})" if polygons else None
    if upper.startswith("POLYGON"):
        body = text[text.find("(") :]
        rings = []
        for ring_text in _split_wkt_top_level(_strip_one_outer_wkt_parens(body)):
            ring = _parse_wkt_ring(_strip_outer_wkt_parens(ring_text), transformer)
            if len(ring) >= 4:
                rings.append(_wkt_ring(ring))
        return f"POLYGON ({','.join(rings)})" if rings else None
    return None


def _wkt_ring(ring: list[list[float]]) -> str:
    return "(" + ",".join(f"{point[0]:.3f} {point[1]:.3f}" for point in ring) + ")"


def _strip_one_outer_wkt_parens(value: str) -> str:
    text = str(value or "").strip()
    if text.startswith("(") and text.endswith(")"):
        return text[1:-1].strip()
    return text


def _strip_outer_wkt_parens(value: str) -> str:
    text = str(value or "").strip()
    while text.startswith("(") and text.endswith(")"):
        depth = 0
        wraps = True
        for index, char in enumerate(text):
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0 and index != len(text) - 1:
                    wraps = False
                    break
        if not wraps:
            break
        text = text[1:-1].strip()
    return text


def _split_wkt_top_level(value: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    for index, char in enumerate(value):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(value[start:index].strip())
            start = index + 1
    tail = value[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _parse_wkt_ring(value: str, transformer: Transformer) -> list[list[float]]:
    ring: list[list[float]] = []
    for raw_point in value.split(","):
        numbers = raw_point.strip().split()
        if len(numbers) < 2:
            continue
        lon, lat = transformer.transform(float(numbers[0]), float(numbers[1]))
        ring.append([float(lon), float(lat)])
    return ring


def _nearby_pois_from_layers(grouped_layers: Mapping[str, Any]) -> dict[str, Any]:
    transport = [item for item in grouped_layers.get("transport", {}).get("items", [])]
    education = [item for item in grouped_layers.get("education", {}).get("items", [])]
    public_items = [item for item in grouped_layers.get("municipal_pois", {}).get("items", [])]
    items = [*transport, *education, *public_items]
    return {
        "count": len(items),
        "displayed_count": len(items),
        "total_count": len(items),
        "items": items,
        "categories": {
            "transport_stop": {"displayed_count": len(transport), "total_count": len(transport), "source_id": "govmap:bus_stops"},
            "school": {"displayed_count": len(education), "total_count": len(education), "source_id": "govmap:education"},
            "public_building": {"displayed_count": len(public_items), "total_count": len(public_items), "source_id": "govmap:public_services"},
        },
    }


def _address_search_layer(search_payload: Mapping[str, Any], *, address_error: str | None) -> dict[str, Any]:
    results = search_payload.get("results") if isinstance(search_payload, Mapping) else []
    results = results if isinstance(results, list) else []
    items = []
    for result in results:
        if not isinstance(result, Mapping):
            continue
        result_id = str(result.get("id") or len(items) + 1)
        items.append(
            {
                "id": f"govmap:address_search:{result_id}",
                "feature_type": "address_search_result",
                "source_id": "govmap:search",
                "provenance_id": _provenance_id("search", result_id, result),
                "source": _govmap_source(None),
                "source_object_id": result_id,
                "label": str(result.get("text") or result_id),
                "name_he": str(result.get("text") or result_id),
                "govmap_datatype": result.get("type"),
                "attributes": dict(result),
            }
        )
    return {
        "count": len(items),
        "displayed_count": len(items),
        "total_count": int(search_payload.get("resultsCount") or len(items)) if isinstance(search_payload, Mapping) else len(items),
        "items": items,
        "status": "found" if items else "degraded" if address_error else "not_found",
        "source_id": "govmap:search",
        "message_he": "GovMap תומך בחיפוש כתובות, רחובות וישובים דרך Search API.",
        "error": address_error,
    }


def _govmap_source(spec: GovMapLayerSpec | None) -> dict[str, Any]:
    return {
        "source_id": f"govmap:{spec.alias}" if spec else "govmap",
        "name_he": spec.label_he if spec else "GovMap",
        "name_en": "GovMap",
        "provider_key": "govmap",
        "provenance_level": "official_national",
        "reuse_status": "verified",
        "display_status": "official",
        "source_is_official": True,
        "reuse_is_verified": True,
        "display_as_official": True,
        "attribution": "GovMap / Survey of Israel and public layer publishers",
        "license_name": None,
        "license_url": None,
    }


def _feature_title(spec: GovMapLayerSpec, attributes: Mapping[str, Any], object_id: str) -> str:
    for key in ("stop_name", "name", "shem_mosad", "school_name", "gan_name", "address", "city", "company", "migrash", "pl_number", "GUSH_NUM", "PARCEL"):
        value = attributes.get(key)
        if value:
            return str(value)
    return f"{spec.label_he} {object_id}"


def _provenance_id(layer: str, object_id: str, attributes: Mapping[str, Any]) -> str:
    digest = hashlib.sha1(repr(sorted(attributes.items())).encode("utf-8", errors="ignore")).hexdigest()[:12]
    return f"govmap:{layer}:{object_id}:{digest}"


def _display_coordinate(center: Mapping[str, float], index: int) -> list[float]:
    angle = (index * 137.5) * math.pi / 180
    ring = 360 + (index % 5) * 150
    return _meters_offset(center, math.cos(angle) * ring, math.sin(angle) * ring)


def _meters_offset(center: Mapping[str, float], east_m: float, north_m: float) -> list[float]:
    lat = float(center["lat"])
    lon = float(center["lon"])
    meters_per_lon = max(1.0, 111_320.0 * math.cos(lat * math.pi / 180))
    return [lon + east_m / meters_per_lon, lat + north_m / 110_540.0]


def _govmap_layer_groups() -> dict[str, list[str]]:
    return {
        "parcel": ["PARCEL_ALL", "SUB_GUSH_ALL"],
        "nearby_parcels": ["retzefmigrashim", "migrashim_msbs"],
        "pois": ["bus_stops", "school", "kids_g"],
        "municipal": ["neighborhoods_area", "regional_authorities", "clinics", "pharmacies", "post_israel", "mikve", "bombshelters"],
        "osm": ["aq_realtime", "cell_active", "GASSTATIONS"],
    }


def _govmap_layer_filters(layers: tuple[GovMapLayerSpec, ...]) -> list[dict[str, str]]:
    return [
        {
            "alias": layer.alias,
            "label_he": layer.label_he,
            "dashboard_key": layer.dashboard_key,
            "category": layer.category,
        }
        for layer in layers
    ]


def _govmap_legend() -> list[dict[str, str]]:
    return [
        {"id": "selected_area", "label": "אזור נבחר", "kind": "polygon", "color": "#7c4dce", "display_status": "official"},
        {"id": "transport_stop", "label": "תחבורה ציבורית", "kind": "point", "color": "#0b68d1", "display_status": "official"},
        {"id": "parks", "label": "פארקים וסביבה", "kind": "point", "color": "#18a865", "display_status": "official"},
        {"id": "public_building", "label": "מבני ציבור ושירותים", "kind": "point", "color": "#f97316", "display_status": "official"},
        {"id": "address_search", "label": "כתובות ורחובות בחיפוש", "kind": "search", "color": "#64748b", "display_status": "official"},
    ]


def _govmap_coverage(grouped_layers: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "municipality_code": "govmap",
            "municipality_name_he": "GovMap",
            "layer_key": key,
            "status": "national_available" if value.get("status") == "found" else value.get("status", "not_found"),
            "source_id": value.get("source_id", "govmap"),
            "feature_count": int(value.get("total_count") or value.get("count") or 0),
        }
        for key, value in grouped_layers.items()
    ]
