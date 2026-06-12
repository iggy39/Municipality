from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from municipality.gis_importers import import_mapi_parcel_directory, import_mapi_parcel_zip  # noqa: E402
from municipality.gis_raw_storage import ImmutableRawStorage  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Import MAPI parcel ZIP or extracted shapefile directory.")
    parser.add_argument("path")
    parser.add_argument("--source-id", default="mapi_parcels")
    parser.add_argument("--raw-root", default="storage/raw/gis")
    parser.add_argument("--gush", help="Optional parcel block filter for targeted imports.")
    parser.add_argument("--helka", help="Optional parcel number filter for targeted imports.")
    parser.add_argument("--municipality-code", help="Optional official boundary municipality code used as a 2039 bbox prefilter.")
    parser.add_argument("--limit", type=int, help="Optional maximum number of matching parcels to import.")
    parser.add_argument("--encoding", default="cp1255", help="DBF encoding for extracted shapefiles; MAPI Hebrew data commonly uses cp1255.")
    args = parser.parse_args()
    engine = create_engine(os.environ["DATABASE_URL"], future=True)
    with Session(engine) as session, session.begin():
        input_path = Path(args.path)
        raw_storage = ImmutableRawStorage(Path(args.raw_root))
        boundary_bbox_filter = _municipality_boundary_bbox_2039(session, args.municipality_code) if args.municipality_code else None
        if input_path.is_dir():
            summary = import_mapi_parcel_directory(
                session,
                source_id=args.source_id,
                directory_path=input_path,
                raw_storage=raw_storage,
                gush_filter=args.gush,
                helka_filter=args.helka,
                boundary_bbox_filter=boundary_bbox_filter,
                limit=args.limit,
                encoding=args.encoding,
            )
        else:
            summary = import_mapi_parcel_zip(
                session,
                source_id=args.source_id,
                zip_path=input_path,
                raw_storage=raw_storage,
                gush_filter=args.gush,
                helka_filter=args.helka,
                boundary_bbox_filter=boundary_bbox_filter,
                limit=args.limit,
                encoding=args.encoding,
            )
    print(f"inserted_or_updated={summary.inserted_or_updated} rejected={summary.rejected}")


def _municipality_boundary_bbox_2039(session: Session, municipality_code: str) -> tuple[float, float, float, float]:
    row = session.execute(
        text(
            """
        SELECT
          ST_XMin(extent)::float AS min_x,
          ST_YMin(extent)::float AS min_y,
          ST_XMax(extent)::float AS max_x,
          ST_YMax(extent)::float AS max_y
        FROM (
          SELECT ST_Extent(geom_2039) AS extent
          FROM official_municipal_boundaries
          WHERE municipality_code = :municipality_code
        ) bbox
        """,
        ),
        {"municipality_code": str(municipality_code).strip()},
    ).mappings().one()
    if row["min_x"] is None:
        raise ValueError(f"No municipal boundary found for municipality_code={municipality_code}")
    return (float(row["min_x"]), float(row["min_y"]), float(row["max_x"]), float(row["max_y"]))


if __name__ == "__main__":
    main()
