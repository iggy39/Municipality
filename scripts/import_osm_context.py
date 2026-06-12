from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from municipality.gis_importers import import_osm_context_zip  # noqa: E402
from municipality.gis_raw_storage import ImmutableRawStorage  # noqa: E402

DEFAULT_GEOFABRIK_URL = "https://download.geofabrik.de/asia/israel-and-palestine-latest-free.shp.zip"


def main() -> None:
    parser = argparse.ArgumentParser(description="Import OSM context buildings and POIs from a Geofabrik SHP ZIP.")
    parser.add_argument("--zip-path", help="Existing Geofabrik free SHP ZIP. If omitted, --url is downloaded.")
    parser.add_argument("--url", default=DEFAULT_GEOFABRIK_URL)
    parser.add_argument("--source-id", default="osm_context")
    parser.add_argument("--raw-root", default="storage/raw/gis")
    parser.add_argument("--limit-buildings", type=int, help="Optional smoke-test limit for buildings.")
    parser.add_argument("--limit-pois", type=int, help="Optional smoke-test limit for POIs.")
    parser.add_argument("--limit-context-geometries", type=int, help="Optional per-layer limit for roads, water, landuse, and other OSM context geometries.")
    parser.add_argument("--center-lon", type=float, help="Optional WGS84 longitude for spatial filtering.")
    parser.add_argument("--center-lat", type=float, help="Optional WGS84 latitude for spatial filtering.")
    parser.add_argument("--radius-m", type=float, help="Optional spatial filter radius in meters. Requires --center-lon and --center-lat.")
    args = parser.parse_args()

    if args.zip_path:
        zip_bytes = Path(args.zip_path).read_bytes()
        source_name = Path(args.zip_path).name
    else:
        with httpx.Client(timeout=120.0, follow_redirects=True) as client:
            response = client.get(args.url)
            response.raise_for_status()
            zip_bytes = response.content
        source_name = Path(args.url).name or "geofabrik_osm_context.zip"

    engine = create_engine(os.environ["DATABASE_URL"], future=True)
    with Session(engine) as session, session.begin():
        summary = import_osm_context_zip(
            session,
            source_id=args.source_id,
            zip_bytes=zip_bytes,
            raw_storage=ImmutableRawStorage(Path(args.raw_root)),
            source_name=source_name,
            buildings_limit=args.limit_buildings,
            pois_limit=args.limit_pois,
            context_geometry_limit=args.limit_context_geometries,
            center_lon=args.center_lon,
            center_lat=args.center_lat,
            radius_m=args.radius_m,
        )
    print(f"inserted_or_updated={summary.inserted_or_updated} rejected={summary.rejected}")
    if summary.warnings:
        print(f"warnings={summary.warnings}")


if __name__ == "__main__":
    main()
