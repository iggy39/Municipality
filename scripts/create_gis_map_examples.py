from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import shapefile
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from municipality.gis_importers import (  # noqa: E402
    import_mapi_parcel_zip,
    import_mot_bus_stops_csv,
    import_municipal_boundaries_geojson,
    import_municipal_geojson,
    import_osm_context_zip,
    import_school_coordinates_csv,
    import_xplan_arcgis_plans,
)
from municipality.gis_raw_storage import ImmutableRawStorage  # noqa: E402
from municipality.gis_source_seed import DEFAULT_SEED_PATH, seed_from_file  # noqa: E402


DEMO_MUNICIPALITY_CODE = "9999"
DEMO_MUNICIPALITY_NAME_HE = "עיר הדגמה"
DEMO_GUSH = "7103"
DEMO_HELKA = "43"
DEMO_PLAN_NUMBER = "603-1373075"


def main() -> None:
    parser = argparse.ArgumentParser(description="Create realistic GIS map examples using the backend importer pipeline.")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--raw-root", default="storage/raw/gis/examples")
    parser.add_argument("--seed-path", default=str(DEFAULT_SEED_PATH))
    args = parser.parse_args()
    if not args.database_url:
        raise SystemExit("DATABASE_URL is required")

    engine = create_engine(args.database_url, future=True)
    seed_from_file(engine, Path(args.seed_path))
    raw_storage = ImmutableRawStorage(Path(args.raw_root))

    with Session(engine) as session, session.begin():
        summaries = {
            "boundaries": _seed_boundary(session),
            "parcel": _seed_parcel(session, raw_storage),
            "plan": _seed_plan(session, raw_storage),
            "transport_stops": _seed_transport_stops(session, raw_storage),
            "schools": _seed_schools(session, raw_storage),
            "osm_context": _seed_osm_context(session, raw_storage),
            "municipal_neighborhood": _seed_municipal_neighborhood(session, raw_storage),
        }
    with engine.connect() as connection:
        evidence = _evidence(connection)
    print(json.dumps({"summaries": summaries, "evidence": evidence}, ensure_ascii=False, indent=2, default=str))


def _seed_boundary(session: Session) -> dict[str, Any]:
    feature = {
        "type": "Feature",
        "properties": {"muni_code": DEMO_MUNICIPALITY_CODE, "name_he": DEMO_MUNICIPALITY_NAME_HE},
        "geometry": _polygon(34.72, 31.72, 34.84, 31.84),
    }
    summary = import_municipal_boundaries_geojson(
        session,
        source_id="moin_municipal_boundaries",
        features=[feature],
        code_field="muni_code",
        name_he_field="name_he",
        raw_storage_uri="generated://demo-boundary.geojson",
    )
    return summary.__dict__


def _seed_parcel(session: Session, raw_storage: ImmutableRawStorage) -> dict[str, Any]:
    zip_path = _parcel_zip()
    summary = import_mapi_parcel_zip(
        session,
        source_id="mapi_parcels",
        zip_path=zip_path,
        raw_storage=raw_storage,
        input_srid=4326,
    )
    return summary.__dict__


def _seed_plan(session: Session, raw_storage: ImmutableRawStorage) -> dict[str, Any]:
    page = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"pl_number": DEMO_PLAN_NUMBER, "pl_name": "תכנית רובע טו - הדגמה"},
                "geometry": _closed_polygon(
                    [
                        [34.758, 31.779],
                        [34.766, 31.793],
                        [34.781, 31.791],
                        [34.795, 31.785],
                        [34.803, 31.774],
                        [34.789, 31.764],
                        [34.772, 31.766],
                        [34.762, 31.772],
                    ]
                ),
            }
        ],
    }

    def fetch_json(_url: str, _params: dict[str, Any]) -> dict[str, Any]:
        return page

    summary = import_xplan_arcgis_plans(
        session,
        source_id="xplan_blue_lines",
        service_url="https://example.local/arcgis/xplan-demo",
        fetch_json=fetch_json,
        raw_storage=raw_storage,
        plan_number_field="pl_number",
        plan_name_field="pl_name",
    )
    return summary.__dict__


def _seed_transport_stops(session: Session, raw_storage: ImmutableRawStorage) -> dict[str, Any]:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["StationId", "CityCode", "CityName", "Lat", "Long"])
    writer.writeheader()
    writer.writerows(
        [
            {"StationId": "demo-stop-1", "CityCode": DEMO_MUNICIPALITY_CODE, "CityName": DEMO_MUNICIPALITY_NAME_HE, "Lat": "31.792", "Long": "34.784"},
            {"StationId": "demo-stop-2", "CityCode": DEMO_MUNICIPALITY_CODE, "CityName": DEMO_MUNICIPALITY_NAME_HE, "Lat": "31.786", "Long": "34.788"},
            {"StationId": "demo-stop-3", "CityCode": DEMO_MUNICIPALITY_CODE, "CityName": DEMO_MUNICIPALITY_NAME_HE, "Lat": "31.778", "Long": "34.785"},
            {"StationId": "demo-stop-4", "CityCode": DEMO_MUNICIPALITY_CODE, "CityName": DEMO_MUNICIPALITY_NAME_HE, "Lat": "31.770", "Long": "34.782"},
        ]
    )
    summary = import_mot_bus_stops_csv(session, source_id="mot_gtfs_stops", csv_text=output.getvalue(), raw_storage=raw_storage)
    return summary.__dict__


def _seed_schools(session: Session, raw_storage: ImmutableRawStorage) -> dict[str, Any]:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["SEMEL_MOSAD", "SHEM_MOSAD", "UTM_X", "UTM_Y"])
    writer.writeheader()
    writer.writerows(
        [
            {"SEMEL_MOSAD": "demo-school-1", "SHEM_MOSAD": "בית ספר רובע טו", "UTM_X": "34.774", "UTM_Y": "31.775"},
            {"SEMEL_MOSAD": "demo-school-2", "SHEM_MOSAD": "מקיף הדגמה", "UTM_X": "34.797", "UTM_Y": "31.786"},
            {"SEMEL_MOSAD": "demo-school-3", "SHEM_MOSAD": "מרכז חינוך קהילתי", "UTM_X": "34.768", "UTM_Y": "31.788"},
        ]
    )
    summary = import_school_coordinates_csv(session, source_id="moe_school_coordinates", csv_text=output.getvalue(), raw_storage=raw_storage)
    return summary.__dict__


def _seed_osm_context(session: Session, raw_storage: ImmutableRawStorage) -> dict[str, Any]:
    summary = import_osm_context_zip(
        session,
        source_id="osm_context",
        zip_bytes=_osm_zip_bytes(),
        raw_storage=raw_storage,
        source_name="demo_osm_context.zip",
    )
    return summary.__dict__


def _seed_municipal_neighborhood(session: Session, raw_storage: ImmutableRawStorage) -> dict[str, Any]:
    payload = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"OBJECTID": "demo-neighborhood-1", "name_he": "רובע טו"},
                "geometry": _closed_polygon(
                    [
                        [34.748, 31.748],
                        [34.755, 31.812],
                        [34.799, 31.818],
                        [34.821, 31.792],
                        [34.813, 31.754],
                        [34.778, 31.741],
                    ]
                ),
            }
        ],
    }
    config = {
        "layer_key": "neighborhoods",
        "municipality_code": "0070",
        "municipality_name_he": "אשדוד",
        "field_mappings": {"source_object_id": ["OBJECTID"], "name_he": ["name_he"]},
    }
    summary = import_municipal_geojson(
        session,
        source_id="ashdod_quarter_candidate",
        geojson_payload=payload,
        config=config,
        raw_storage=raw_storage,
        source_name="demo_ashdod_quarter.geojson",
    )
    return summary.__dict__


def _parcel_zip() -> Path:
    tmp_dir = Path(tempfile.mkdtemp(prefix="municipality-demo-parcel-"))
    shp_base = tmp_dir / "demo_parcels"
    writer = shapefile.Writer(str(shp_base), shapeType=shapefile.POLYGON)
    writer.field("OBJECTID", "C")
    writer.field("GUSH_NUM", "C")
    writer.field("PARCEL_NUM", "C")
    writer.record("demo-parcel-1", DEMO_GUSH, DEMO_HELKA)
    writer.poly([_ring(34.770, 31.770, 34.790, 31.790)])
    writer.close()
    zip_path = tmp_dir / "demo_parcels.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        for path in tmp_dir.glob("demo_parcels.*"):
            archive.write(path, path.name)
    return zip_path


def _osm_zip_bytes() -> bytes:
    tmp_dir = Path(tempfile.mkdtemp(prefix="municipality-demo-osm-"))
    buildings_base = tmp_dir / "gis_osm_buildings_a_free_1"
    writer = shapefile.Writer(str(buildings_base), shapeType=shapefile.POLYGON)
    writer.field("osm_id", "C")
    writer.field("fclass", "C")
    for index, (min_lon, min_lat, max_lon, max_lat) in enumerate(
        [
            (34.781, 31.781, 34.785, 31.785),
            (34.795, 31.772, 34.801, 31.778),
            (34.766, 31.786, 34.772, 31.792),
            (34.788, 31.796, 34.794, 31.802),
            (34.771, 31.764, 34.777, 31.770),
        ],
        start=1,
    ):
        writer.record(f"demo-building-{index}", "building")
        writer.poly([_ring(min_lon, min_lat, max_lon, max_lat)])
    writer.close()

    pois_base = tmp_dir / "gis_osm_pois_free_1"
    writer = shapefile.Writer(str(pois_base), shapeType=shapefile.POINT)
    writer.field("osm_id", "C")
    writer.field("fclass", "C")
    writer.field("name", "C")
    for osm_id, fclass, name, lon, lat in [
        ("demo-cafe", "cafe", "בית קפה הקשר", 34.782, 31.782),
        ("demo-park", "park", "פארק הקשר", 34.795, 31.791),
        ("demo-library", "library", "ספרייה קהילתית", 34.768, 31.789),
        ("demo-clinic", "public_building", "מרפאה קהילתית", 34.793, 31.772),
        ("demo-community", "community_centre", "מרכז קהילתי", 34.773, 31.799),
    ]:
        writer.record(osm_id, fclass, name)
        writer.point(lon, lat)
    writer.close()

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for path in tmp_dir.iterdir():
            archive.write(path, path.name)
    return output.getvalue()


def _polygon(min_lon: float, min_lat: float, max_lon: float, max_lat: float) -> dict[str, Any]:
    return {"type": "Polygon", "coordinates": [_ring(min_lon, min_lat, max_lon, max_lat)]}


def _closed_polygon(points: list[list[float]]) -> dict[str, Any]:
    ring = list(points)
    if ring and ring[0] != ring[-1]:
        ring.append(ring[0])
    return {"type": "Polygon", "coordinates": [ring]}


def _ring(min_lon: float, min_lat: float, max_lon: float, max_lat: float) -> list[list[float]]:
    return [[min_lon, min_lat], [min_lon, max_lat], [max_lon, max_lat], [max_lon, min_lat], [min_lon, min_lat]]


def _evidence(connection: Any) -> dict[str, Any]:
    tables = ["official_municipal_boundaries", "parcels", "plans", "poi_points", "context_pois", "buildings", "neighborhoods"]
    counts = {table: int(connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()) for table in tables}
    coverage = [
        dict(row)
        for row in connection.execute(
            text(
                """
                SELECT municipality_code, layer_key, status, source_id, feature_count
                FROM layer_coverage
                WHERE municipality_code IN ('9999', '0070')
                ORDER BY municipality_code, layer_key
                """
            )
        ).mappings()
    ]
    return {"counts": counts, "coverage": coverage, "sample": {"gush": DEMO_GUSH, "helka": DEMO_HELKA, "plan_number": DEMO_PLAN_NUMBER}}


if __name__ == "__main__":
    main()
