from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
import yaml
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from municipality.gis_importers import (  # noqa: E402
    import_municipal_arcgis,
    import_municipal_csv,
    import_municipal_geojson,
    import_municipal_shp_zip,
)
from municipality.gis_raw_storage import ImmutableRawStorage  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a municipal GIS source using a generic field-mapping config.")
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--format", required=True, choices=["geojson", "csv", "shp-zip", "arcgis"])
    parser.add_argument("--config", required=True, help="YAML/JSON file containing layer_key, municipality_code, and field_mappings.")
    parser.add_argument("--input-path", help="Input file path for geojson, csv, or shp-zip imports.")
    parser.add_argument("--service-url", help="ArcGIS REST layer URL for arcgis imports.")
    parser.add_argument("--where", default="1=1")
    parser.add_argument("--page-size", type=int, default=1000)
    parser.add_argument("--center-lon", type=float, help="Optional WGS84 longitude for ArcGIS spatial filtering.")
    parser.add_argument("--center-lat", type=float, help="Optional WGS84 latitude for ArcGIS spatial filtering.")
    parser.add_argument("--radius-m", type=float, help="Optional ArcGIS spatial filter radius in meters.")
    parser.add_argument("--in-sr", default="4326", help="Input spatial reference for the ArcGIS spatial filter.")
    parser.add_argument("--out-sr", default="4326", help="Output spatial reference for ArcGIS GeoJSON.")
    parser.add_argument("--raw-root", default="storage/raw/gis")
    args = parser.parse_args()

    config = _load_config(Path(args.config))
    engine = create_engine(os.environ["DATABASE_URL"], future=True)
    raw_storage = ImmutableRawStorage(Path(args.raw_root))
    with Session(engine) as session, session.begin():
        if args.format == "geojson":
            _require_path(args.input_path)
            payload = json.loads(Path(args.input_path).read_text(encoding="utf-8"))
            summary = import_municipal_geojson(session, source_id=args.source_id, geojson_payload=payload, config=config, raw_storage=raw_storage, source_name=Path(args.input_path).name)
        elif args.format == "csv":
            _require_path(args.input_path)
            summary = import_municipal_csv(session, source_id=args.source_id, csv_text=Path(args.input_path).read_text(encoding="utf-8-sig"), config=config, raw_storage=raw_storage, source_name=Path(args.input_path).name)
        elif args.format == "shp-zip":
            _require_path(args.input_path)
            summary = import_municipal_shp_zip(session, source_id=args.source_id, zip_bytes=Path(args.input_path).read_bytes(), config=config, raw_storage=raw_storage, source_name=Path(args.input_path).name, input_srid=int(config.get("input_srid", 4326)))
        else:
            if not args.service_url:
                raise SystemExit("--service-url is required for arcgis imports")

            def fetch_json(url: str, params: dict[str, Any]) -> dict[str, Any]:
                response = httpx.get(url, params=params, timeout=60.0)
                response.raise_for_status()
                return response.json()

            summary = import_municipal_arcgis(
                session,
                source_id=args.source_id,
                service_url=args.service_url,
                fetch_json=fetch_json,
                config=config,
                raw_storage=raw_storage,
                page_size=args.page_size,
                where=args.where,
                query_params=_arcgis_spatial_query_params(args),
            )
    print(f"inserted_or_updated={summary.inserted_or_updated} rejected={summary.rejected}")
    if summary.warnings:
        print(f"warnings={summary.warnings}")


def _load_config(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        payload = json.loads(text)
    else:
        payload = yaml.safe_load(text)
    if not isinstance(payload, dict):
        raise SystemExit("municipal import config must be a mapping")
    return payload


def _require_path(value: str | None) -> None:
    if not value:
        raise SystemExit("--input-path is required for this import format")


def _arcgis_spatial_query_params(args: argparse.Namespace) -> dict[str, Any]:
    params: dict[str, Any] = {"outSR": args.out_sr}
    if args.center_lon is None and args.center_lat is None and args.radius_m is None:
        return params
    if args.center_lon is None or args.center_lat is None or args.radius_m is None:
        raise SystemExit("--center-lon, --center-lat, and --radius-m must be provided together")
    params.update(
        {
            "geometry": json.dumps({"x": args.center_lon, "y": args.center_lat, "spatialReference": {"wkid": int(args.in_sr)}}),
            "geometryType": "esriGeometryPoint",
            "inSR": args.in_sr,
            "spatialRel": "esriSpatialRelIntersects",
            "distance": args.radius_m,
            "units": "esriSRUnit_Meter",
        }
    )
    return params


if __name__ == "__main__":
    main()
