from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from municipality.gis_importers import import_govmap_public_parcel  # noqa: E402
from municipality.gis_raw_storage import ImmutableRawStorage  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Import one parcel through GovMap public search endpoints, without an API key.")
    parser.add_argument("--gush", required=True)
    parser.add_argument("--helka", required=True)
    parser.add_argument("--source-id", default="govmap_public_parcels")
    parser.add_argument("--raw-root", default="storage/raw/gis")
    args = parser.parse_args()

    def post_json(url: str, payload: dict[str, Any]) -> Any:
        response = httpx.post(url, json=payload, timeout=30.0)
        response.raise_for_status()
        return response.json()

    engine = create_engine(os.environ["DATABASE_URL"], future=True)
    with Session(engine) as session, session.begin():
        summary = import_govmap_public_parcel(
            session,
            source_id=args.source_id,
            gush=args.gush,
            helka=args.helka,
            post_json=post_json,
            raw_storage=ImmutableRawStorage(Path(args.raw_root)),
        )
    print(f"inserted_or_updated={summary.inserted_or_updated} rejected={summary.rejected}")
    if summary.warnings:
        print(f"warnings={summary.warnings}")


if __name__ == "__main__":
    main()
