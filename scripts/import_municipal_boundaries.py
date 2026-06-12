from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from municipality.gis_importers import import_municipal_boundaries_geojson, import_municipal_boundaries_geojson_postgis  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Import official municipal boundary GeoJSON.")
    parser.add_argument("geojson_path")
    parser.add_argument("--source-id", default="moin_municipal_boundaries")
    parser.add_argument("--code-field", required=True)
    parser.add_argument("--name-he-field", required=True)
    parser.add_argument("--code-values", help="Optional comma-separated municipality codes to import from the source file.")
    parser.add_argument("--legacy-python-validation", action="store_true", help="Use slower Python validation instead of optimized PostGIS grouping.")
    args = parser.parse_args()
    database_url = os.environ["DATABASE_URL"]
    payload = json.loads(Path(args.geojson_path).read_text(encoding="utf-8"))
    features = payload.get("features", [])
    if args.code_values:
        code_values = {_normalize_code(value) for value in args.code_values.split(",") if value.strip()}
        features = [feature for feature in features if _normalize_code((feature.get("properties") or {}).get(args.code_field)) in code_values]
    engine = create_engine(database_url, future=True)
    with Session(engine) as session, session.begin():
        importer = import_municipal_boundaries_geojson if args.legacy_python_validation else import_municipal_boundaries_geojson_postgis
        summary = importer(
            session,
            source_id=args.source_id,
            features=features,
            code_field=args.code_field,
            name_he_field=args.name_he_field,
            raw_storage_uri=str(Path(args.geojson_path)),
        )
    print(f"inserted_or_updated={summary.inserted_or_updated} rejected={summary.rejected}")


def _normalize_code(value: object) -> str:
    text_value = str(value or "").strip()
    digits = "".join(ch for ch in text_value if ch.isdigit())
    return digits.zfill(4) if digits else text_value


if __name__ == "__main__":
    main()
