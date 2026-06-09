from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


@dataclass
class Check:
    label: str
    ok: bool
    evidence: dict[str, Any]


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify real GIS ingestion and Milestone 3 APIs with concise evidence.")
    parser.add_argument("--api-base", default=os.getenv("API_BASE_URL", "http://localhost:8000"))
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--plan-number")
    parser.add_argument("--gush")
    parser.add_argument("--helka")
    parser.add_argument("--lon", type=float)
    parser.add_argument("--lat", type=float)
    parser.add_argument("--skip-boundaries", action="store_true", help="Allow a POC run without municipal boundary ingestion.")
    parser.add_argument("--skip-parcels", action="store_true", help="Allow a POC run when MAPI parcel downloads are blocked or not imported.")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero when required real-data evidence is missing.")
    args = parser.parse_args()

    if not args.database_url:
        raise SystemExit("DATABASE_URL is required")

    checks: list[Check] = []
    engine = create_engine(args.database_url, future=True)
    with engine.connect() as connection:
        checks.extend(_db_checks(connection, skip_boundaries=args.skip_boundaries, skip_parcels=args.skip_parcels))
        plan_number = args.plan_number or _scalar(connection, "SELECT plan_number FROM plans ORDER BY fetched_at DESC LIMIT 1")
        parcel = None if args.skip_parcels else _parcel_sample(connection, args.gush, args.helka)
        point = _poi_point_sample(connection, args.lon, args.lat) if args.skip_boundaries else _point_sample(connection, args.lon, args.lat)

    checks.extend(
        _api_checks(
            args.api_base,
            plan_number=plan_number,
            parcel=parcel,
            point=point,
            skip_boundaries=args.skip_boundaries,
            skip_parcels=args.skip_parcels,
        )
    )

    for check in checks:
        status = "ok" if check.ok else "missing"
        print(f"{status} {check.label}: {check.evidence}")

    if args.strict and not all(check.ok for check in checks):
        raise SystemExit(1)


def _db_checks(connection: Any, *, skip_boundaries: bool, skip_parcels: bool) -> list[Check]:
    checks = [
        _count_check(connection, "source_registry", "sources", minimum=1),
        _count_check(connection, "layer_coverage", "coverage_rows", minimum=1),
        _count_check(connection, "plans", "plans", minimum=1),
        _count_check(connection, "poi_points", "poi_points", minimum=1),
    ]
    if skip_parcels:
        checks.append(Check("parcels", True, {"skipped": True, "reason": "MAPI parcel ZIP unavailable in this POC run"}))
    else:
        checks.append(_count_check(connection, "parcels", "parcels", minimum=1))
    if skip_boundaries:
        checks.append(Check("municipal_boundaries", True, {"skipped": True, "reason": "POC scope excludes MOIN boundaries"}))
    else:
        checks.append(_count_check(connection, "official_municipal_boundaries", "municipal_boundaries", minimum=1))
    checks.append(
        _where_count_check(
            connection,
            "poi_points",
            "transport_stops",
            "poi_category = 'transport_stop'",
            minimum=1,
        )
    )
    checks.append(_where_count_check(connection, "poi_points", "schools", "poi_category = 'school'", minimum=1))
    return checks


def _count_check(connection: Any, table_name: str, label: str, *, minimum: int) -> Check:
    try:
        count = int(connection.execute(text(f"SELECT count(*) FROM {table_name}")).scalar_one())
    except SQLAlchemyError as exc:
        return Check(label, False, {"error": exc.__class__.__name__})
    return Check(label, count >= minimum, {"count": count})


def _where_count_check(connection: Any, table_name: str, label: str, where_sql: str, *, minimum: int) -> Check:
    try:
        count = int(connection.execute(text(f"SELECT count(*) FROM {table_name} WHERE {where_sql}")).scalar_one())
    except SQLAlchemyError as exc:
        return Check(label, False, {"error": exc.__class__.__name__})
    return Check(label, count >= minimum, {"count": count})


def _api_checks(
    api_base: str,
    *,
    plan_number: str | None,
    parcel: tuple[str, str] | None,
    point: tuple[float, float] | None,
    skip_boundaries: bool,
    skip_parcels: bool,
) -> list[Check]:
    checks: list[Check] = []
    client = httpx.Client(base_url=api_base.rstrip("/"), timeout=10.0)
    try:
        checks.append(_api_get(client, "/v1/health", "api_health", expected_status=200))
        if plan_number:
            checks.append(_feature_api_check(client, f"/v1/plans/{plan_number}", "plan_lookup", {"plan_number": plan_number}))
        else:
            checks.append(Check("plan_lookup", False, {"reason": "no plan_number sample in DB"}))

        if skip_parcels:
            checks.append(Check("parcel_lookup", True, {"skipped": True, "reason": "MAPI parcel ZIP unavailable in this POC run"}))
        elif parcel:
            gush, helka = parcel
            checks.append(_feature_api_check(client, "/v1/parcels", "parcel_lookup", {"gush": gush, "helka": helka}))
        else:
            checks.append(Check("parcel_lookup", False, {"reason": "no gush/helka sample in DB"}))

        if skip_boundaries:
            checks.append(Check("point_report", True, {"skipped": True, "reason": "POC scope excludes municipality identification"}))
            if point:
                lon, lat = point
                checks.append(_api_get(client, "/v1/nearby", "nearby", params={"lon": lon, "lat": lat}, expected_status=200))
            else:
                checks.append(Check("nearby", False, {"reason": "no POI point sample in DB"}))
        elif point:
            lon, lat = point
            checks.append(_point_report_check(client, lon=lon, lat=lat))
            checks.append(_api_get(client, "/v1/nearby", "nearby", params={"lon": lon, "lat": lat}, expected_status=200))
        else:
            checks.append(Check("point_report", False, {"reason": "no boundary point sample in DB"}))
            checks.append(Check("nearby", False, {"reason": "no boundary point sample in DB"}))
    except httpx.HTTPError as exc:
        checks.append(Check("api_available", False, {"api_base": api_base, "error": exc.__class__.__name__}))
    finally:
        client.close()
    return checks


def _api_get(client: httpx.Client, path: str, label: str, *, expected_status: int, params: dict[str, Any] | None = None) -> Check:
    response = client.get(path, params=params)
    return Check(label, response.status_code == expected_status, {"status_code": response.status_code})


def _feature_api_check(client: httpx.Client, path: str, label: str, params: dict[str, Any]) -> Check:
    response = client.get(path, params=params if path == "/v1/parcels" else None)
    if response.status_code != 200:
        return Check(label, False, {**params, "status_code": response.status_code})
    payload = response.json()
    items = payload.get("items") or []
    first = items[0] if items else {}
    ok = bool(items and first.get("source") and first.get("provenance_id"))
    return Check(
        label,
        ok,
        {
            **params,
            "status": payload.get("status"),
            "count": payload.get("count", len(items)),
            "source_id": (first.get("source") or {}).get("source_id"),
            "provenance_id": first.get("provenance_id"),
        },
    )


def _point_report_check(client: httpx.Client, *, lon: float, lat: float) -> Check:
    response = client.get("/v1/point-report", params={"lon": lon, "lat": lat})
    if response.status_code != 200:
        return Check("point_report", False, {"lon": lon, "lat": lat, "status_code": response.status_code})
    payload = response.json()
    municipality = payload.get("municipality") or {}
    return Check(
        "point_report",
        municipality.get("status") == "found" and bool(municipality.get("source")),
        {
            "lon": lon,
            "lat": lat,
            "municipality_code": municipality.get("municipality_code"),
            "parcel": (payload.get("parcel") or {}).get("status"),
            "plans": (payload.get("plans") or {}).get("status"),
            "neighborhood": (payload.get("neighborhood") or {}).get("status"),
        },
    )


def _scalar(connection: Any, sql: str) -> Any | None:
    try:
        return connection.execute(text(sql)).scalar_one_or_none()
    except SQLAlchemyError:
        return None


def _parcel_sample(connection: Any, gush: str | None, helka: str | None) -> tuple[str, str] | None:
    if gush and helka:
        return gush, helka
    try:
        row = connection.execute(
            text("SELECT gush, helka FROM parcels WHERE gush IS NOT NULL AND helka IS NOT NULL ORDER BY fetched_at DESC LIMIT 1")
        ).mappings().first()
    except SQLAlchemyError:
        return None
    return (str(row["gush"]), str(row["helka"])) if row else None


def _point_sample(connection: Any, lon: float | None, lat: float | None) -> tuple[float, float] | None:
    if lon is not None and lat is not None:
        return lon, lat
    try:
        row = connection.execute(
            text(
                """
                SELECT ST_X(ST_PointOnSurface(geom)) AS lon, ST_Y(ST_PointOnSurface(geom)) AS lat
                FROM official_municipal_boundaries
                ORDER BY fetched_at DESC
                LIMIT 1
                """
            )
        ).mappings().first()
    except SQLAlchemyError:
        return None
    return (float(row["lon"]), float(row["lat"])) if row else None


def _poi_point_sample(connection: Any, lon: float | None, lat: float | None) -> tuple[float, float] | None:
    if lon is not None and lat is not None:
        return lon, lat
    try:
        row = connection.execute(
            text(
                """
                SELECT ST_X(geom) AS lon, ST_Y(geom) AS lat
                FROM poi_points
                ORDER BY fetched_at DESC
                LIMIT 1
                """
            )
        ).mappings().first()
    except SQLAlchemyError:
        return None
    return (float(row["lon"]), float(row["lat"])) if row else None


if __name__ == "__main__":
    main()
