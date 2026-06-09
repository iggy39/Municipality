from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

import httpx

XPLAN_BLUE_LINES_URL = "https://ags.iplan.gov.il/arcgisiplan/rest/services/PlanningPublic/Xplan/MapServer/1"
MAPI_PARCELS_URL = "https://e.data.gov.il/dataset/dff8a168-af6c-4e0f-bbe3-c4bd3646084c/resource/c68b4df6-c809-4bb5-a546-61fa1528fed5/download/parcels.zip"
MOE_COORDINATES_RESOURCE_ID = "5c5d6bb0-755d-470d-84b6-d7dd3135ba9c"
MOT_BUS_STOPS_RESOURCE_ID = "e873e6a2-66c1-494f-a677-f5e77348edb0"
MOT_GTFS_URL = "https://gtfs.mot.gov.il/gtfsfiles/israel-public-transportation.zip"
CKAN_DATASTORE_URL = "https://data.gov.il/api/3/action/datastore_search"


def main() -> None:
    parser = argparse.ArgumentParser(description="Download accessible real GIS POC source data and write a manifest.")
    parser.add_argument("--out-dir", default="storage/raw/gis/poc_sources")
    parser.add_argument("--page-size", type=int, default=5000)
    parser.add_argument("--moe-limit", type=int, default=0, help="Limit MOE rows for smoke tests. 0 means all rows.")
    parser.add_argument("--include-mapi-parcels", action="store_true", help="Download the large MAPI parcel ZIP (~669 MB).")
    parser.add_argument("--include-gtfs", action="store_true", help="Attempt to download the MOT GTFS ZIP.")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "xplan_blue_lines": {
            "service_url": XPLAN_BLUE_LINES_URL,
            "plan_number_field": "pl_number",
            "plan_name_field": "pl_name",
            "sample_where": "pl_number='101-0057273'",
            "status": "service_url_recorded",
        },
        "mapi_parcels": {"url": MAPI_PARCELS_URL, "status": "skipped_large_download"},
        "mot_gtfs_stops": {"url": MOT_GTFS_URL, "status": "skipped_by_default"},
    }

    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        moe_path = out_dir / "moe_mosdot_coordinates.csv"
        moe_count = _download_ckan_datastore_csv(
            client,
            MOE_COORDINATES_RESOURCE_ID,
            moe_path,
            page_size=args.page_size,
            row_limit=args.moe_limit or None,
        )
        manifest["moe_school_coordinates"] = {
            "resource_id": MOE_COORDINATES_RESOURCE_ID,
            "path": str(moe_path),
            "rows": moe_count,
            "status": "downloaded_from_ckan_datastore",
        }

        mot_bus_stops_path = out_dir / "mot_bus_stops.csv"
        mot_bus_stops_count = _download_ckan_datastore_csv(
            client,
            MOT_BUS_STOPS_RESOURCE_ID,
            mot_bus_stops_path,
            page_size=args.page_size,
            row_limit=None,
        )
        manifest["mot_bus_stops"] = {
            "resource_id": MOT_BUS_STOPS_RESOURCE_ID,
            "path": str(mot_bus_stops_path),
            "rows": mot_bus_stops_count,
            "status": "downloaded_from_ckan_datastore",
        }

        if args.include_mapi_parcels:
            manifest["mapi_parcels"].update(_download_url(client, MAPI_PARCELS_URL, out_dir / "parcels.zip"))
        if args.include_gtfs:
            manifest["mot_gtfs_stops"].update(_download_url(client, MOT_GTFS_URL, out_dir / "israel-public-transportation.zip"))

    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"manifest": str(manifest_path), **manifest}, ensure_ascii=False, indent=2))


def _download_ckan_datastore_csv(
    client: httpx.Client,
    resource_id: str,
    output_path: Path,
    *,
    page_size: int,
    row_limit: int | None,
) -> int:
    offset = 0
    fieldnames: list[str] | None = None
    count = 0
    with output_path.open("w", encoding="utf-8-sig", newline="") as output:
        writer: csv.DictWriter[str] | None = None
        while True:
            limit = page_size if row_limit is None else min(page_size, row_limit - count)
            if limit <= 0:
                break
            response = client.get(
                CKAN_DATASTORE_URL,
                params={"resource_id": resource_id, "limit": limit, "offset": offset},
            )
            response.raise_for_status()
            payload = response.json()
            result = payload.get("result") or {}
            records = result.get("records") or []
            if not records:
                break
            if fieldnames is None:
                fieldnames = [field["id"] for field in result.get("fields", []) if field.get("id") != "_id"]
                writer = csv.DictWriter(output, fieldnames=fieldnames)
                writer.writeheader()
            assert writer is not None and fieldnames is not None
            for record in records:
                writer.writerow({field: record.get(field) for field in fieldnames})
            count += len(records)
            offset += len(records)
            total = int(result.get("total") or 0)
            if len(records) < limit or (total and offset >= total):
                break
    return count


def _download_url(client: httpx.Client, url: str, output_path: Path) -> dict[str, Any]:
    with client.stream("GET", url) as response:
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "html" in content_type.lower():
            return {"status": "blocked_or_html_response", "content_type": content_type}
        with output_path.open("wb") as output:
            for chunk in response.iter_bytes():
                output.write(chunk)
    return {"status": "downloaded", "path": str(output_path), "content_type": content_type}


if __name__ == "__main__":
    main()
