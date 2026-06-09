from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from municipality.gis_importers import import_municipal_boundaries_geojson  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Import official municipal boundary GeoJSON.")
    parser.add_argument("geojson_path")
    parser.add_argument("--source-id", default="moin_municipal_boundaries")
    parser.add_argument("--code-field", required=True)
    parser.add_argument("--name-he-field", required=True)
    args = parser.parse_args()
    database_url = os.environ["DATABASE_URL"]
    payload = json.loads(Path(args.geojson_path).read_text(encoding="utf-8"))
    engine = create_engine(database_url, future=True)
    with Session(engine) as session, session.begin():
        summary = import_municipal_boundaries_geojson(
            session,
            source_id=args.source_id,
            features=payload.get("features", []),
            code_field=args.code_field,
            name_he_field=args.name_he_field,
            raw_storage_uri=str(Path(args.geojson_path)),
        )
    print(f"inserted_or_updated={summary.inserted_or_updated} rejected={summary.rejected}")


if __name__ == "__main__":
    main()
