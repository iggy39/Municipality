from __future__ import annotations

from dataclasses import dataclass
from math import fabs
from typing import Any, Iterable, Sequence

ISRAEL_BBOX = (33.7, 29.0, 36.0, 33.7)


@dataclass(frozen=True)
class GeometryValidationResult:
    status: str
    warnings: list[str]
    geometry: dict[str, Any] | None


def validate_geojson_geometry(
    geometry: dict[str, Any] | None,
    *,
    expected_type: str | Sequence[str] | None = None,
    bbox: tuple[float, float, float, float] = ISRAEL_BBOX,
    min_polygon_area_degrees: float = 0.0,
) -> GeometryValidationResult:
    warnings: list[str] = []
    if geometry is None:
        return GeometryValidationResult("invalid", ["null_geometry"], None)
    if not isinstance(geometry, dict):
        return GeometryValidationResult("invalid", ["geometry_not_object"], None)

    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if not isinstance(geometry_type, str):
        return GeometryValidationResult("invalid", ["missing_geometry_type"], geometry)
    if expected_type is not None:
        expected = {expected_type} if isinstance(expected_type, str) else set(expected_type)
        if geometry_type not in expected:
            warnings.append("type_mismatch")
    if _is_empty_coordinates(coordinates):
        return GeometryValidationResult("invalid", warnings + ["empty_geometry"], geometry)

    points = list(_iter_points(coordinates))
    if not points:
        return GeometryValidationResult("invalid", warnings + ["empty_geometry"], geometry)
    for lon, lat in points:
        if lon == 0 and lat == 0:
            return GeometryValidationResult("invalid", warnings + ["zero_zero_coordinate"], geometry)
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            return GeometryValidationResult("invalid", warnings + ["invalid_coordinate_range"], geometry)
        if not _point_in_bbox(lon, lat, bbox):
            warnings.append("outside_expected_bbox")
            break

    if geometry_type in {"Polygon", "MultiPolygon"}:
        if _has_self_intersection(geometry):
            return GeometryValidationResult("repaired", warnings + ["st_makevalid_applied"], geometry)
        area = _polygon_area(geometry)
        if area <= min_polygon_area_degrees:
            return GeometryValidationResult("invalid", warnings + ["polygon_area_too_small"], geometry)

    if "type_mismatch" in warnings:
        return GeometryValidationResult("invalid", warnings, geometry)
    if warnings:
        return GeometryValidationResult("warning", _dedupe(warnings), geometry)
    return GeometryValidationResult("valid", [], geometry)


def normalize_polygon_geometry(geometry: dict[str, Any]) -> dict[str, Any]:
    if geometry.get("type") == "Polygon":
        return {"type": "MultiPolygon", "coordinates": [geometry.get("coordinates", [])]}
    return geometry


def geometry_hash(geometry: dict[str, Any]) -> str:
    import hashlib
    import json

    payload = json.dumps(geometry, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _is_empty_coordinates(coordinates: Any) -> bool:
    if coordinates is None:
        return True
    if coordinates == []:
        return True
    if isinstance(coordinates, list):
        return all(_is_empty_coordinates(item) for item in coordinates)
    return False


def _iter_points(coordinates: Any) -> Iterable[tuple[float, float]]:
    if isinstance(coordinates, (list, tuple)) and len(coordinates) >= 2 and all(isinstance(v, (int, float)) for v in coordinates[:2]):
        yield float(coordinates[0]), float(coordinates[1])
        return
    if isinstance(coordinates, (list, tuple)):
        for item in coordinates:
            yield from _iter_points(item)


def _point_in_bbox(lon: float, lat: float, bbox: tuple[float, float, float, float]) -> bool:
    min_lon, min_lat, max_lon, max_lat = bbox
    return min_lon <= lon <= max_lon and min_lat <= lat <= max_lat


def _polygon_area(geometry: dict[str, Any]) -> float:
    polygons = [geometry.get("coordinates", [])] if geometry.get("type") == "Polygon" else geometry.get("coordinates", [])
    total = 0.0
    for polygon in polygons:
        if not polygon:
            continue
        exterior = polygon[0]
        total += fabs(_ring_area(exterior))
    return total


def _ring_area(ring: Sequence[Sequence[float]]) -> float:
    if len(ring) < 4:
        return 0.0
    total = 0.0
    for index, point in enumerate(ring):
        next_point = ring[(index + 1) % len(ring)]
        total += float(point[0]) * float(next_point[1]) - float(next_point[0]) * float(point[1])
    return total / 2.0


def _has_self_intersection(geometry: dict[str, Any]) -> bool:
    polygons = [geometry.get("coordinates", [])] if geometry.get("type") == "Polygon" else geometry.get("coordinates", [])
    for polygon in polygons:
        if polygon and _ring_has_self_intersection(polygon[0]):
            return True
    return False


def _ring_has_self_intersection(ring: Sequence[Sequence[float]]) -> bool:
    segments = list(zip(ring, ring[1:]))
    for first_index, first in enumerate(segments):
        for second_index, second in enumerate(segments):
            if abs(first_index - second_index) <= 1:
                continue
            if first_index == 0 and second_index == len(segments) - 1:
                continue
            if _segments_intersect(first[0], first[1], second[0], second[1]):
                return True
    return False


def _segments_intersect(a: Sequence[float], b: Sequence[float], c: Sequence[float], d: Sequence[float]) -> bool:
    def orientation(p: Sequence[float], q: Sequence[float], r: Sequence[float]) -> float:
        return (float(q[1]) - float(p[1])) * (float(r[0]) - float(q[0])) - (float(q[0]) - float(p[0])) * (float(r[1]) - float(q[1]))

    o1 = orientation(a, b, c)
    o2 = orientation(a, b, d)
    o3 = orientation(c, d, a)
    o4 = orientation(c, d, b)
    return (o1 > 0) != (o2 > 0) and (o3 > 0) != (o4 > 0)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result
