from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from municipality.gis_importers import import_xplan_arcgis_plans  # noqa: E402
from municipality.gis_raw_storage import ImmutableRawStorage  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Import XPLAN ArcGIS REST plan polygons.")
    parser.add_argument("service_url")
    parser.add_argument("--source-id", default="xplan_blue_lines")
    parser.add_argument("--plan-number-field", default="plan_number")
    parser.add_argument("--plan-name-field", default="plan_name")
    parser.add_argument("--raw-root", default="storage/raw/gis")
    args = parser.parse_args()

    def fetch_json(url: str, params: dict[str, object]) -> dict[str, object]:
        response = httpx.get(url, params=params, timeout=60.0)
        response.raise_for_status()
        return response.json()

    engine = create_engine(os.environ["DATABASE_URL"], future=True)
    with Session(engine) as session, session.begin():
        summary = import_xplan_arcgis_plans(
            session,
            source_id=args.source_id,
            service_url=args.service_url,
            fetch_json=fetch_json,
            raw_storage=ImmutableRawStorage(Path(args.raw_root)),
            plan_number_field=args.plan_number_field,
            plan_name_field=args.plan_name_field,
        )
    print(f"inserted_or_updated={summary.inserted_or_updated} rejected={summary.rejected}")


if __name__ == "__main__":
    main()
