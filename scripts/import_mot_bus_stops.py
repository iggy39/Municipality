from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from municipality.gis_importers import import_mot_bus_stops_csv  # noqa: E402
from municipality.gis_raw_storage import ImmutableRawStorage  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Import MOT bus-stop CSV into poi_points.")
    parser.add_argument("csv_path")
    parser.add_argument("--source-id", default="mot_gtfs_stops")
    parser.add_argument("--raw-root", default="storage/raw/gis")
    args = parser.parse_args()

    engine = create_engine(os.environ["DATABASE_URL"], future=True)
    with Session(engine) as session, session.begin():
        summary = import_mot_bus_stops_csv(
            session,
            source_id=args.source_id,
            csv_text=Path(args.csv_path).read_text(encoding="utf-8-sig"),
            raw_storage=ImmutableRawStorage(Path(args.raw_root)),
        )
    print(f"inserted_or_updated={summary.inserted_or_updated} rejected={summary.rejected}")


if __name__ == "__main__":
    main()
