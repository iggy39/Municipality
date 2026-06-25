from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import request
from urllib.parse import quote, urlsplit, urlunsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from municipality.storage import RawStorage  # noqa: E402


TABLE_ENDPOINT = "https://www.tel-aviv.gov.il/_vti_bin/TlvSP2013PublicSite/TlvList.svc/GetTables"
MUNICIPALITY_SLUG = "tel_aviv"
DOC_KIND = "protocol_full"

# This payload is the generic SharePoint table configuration embedded in the
# public protocols page. It avoids hardcoding any protocol title/date format.
PROTOCOL_TABLE_PAYLOAD: dict[str, Any] = {
    "dataSource": {
        "Fields": None,
        "ItemdIds": [],
        "ListContentTypes": ["מסמך", "תיקיה"],
        "ListId": "6a95bd9f-d8d8-4564-911d-c9b2f99348df",
        "SiteId": "24aa409e-01ed-482e-b0ed-1956972addb1",
        "ViewId": "15735004-8390-40a9-a789-405e018f1bf1",
        "WebId": "259de9f4-37f3-4d7d-9423-74cab057ea28",
    },
    "dataSrcFullUrl": None,
    "showTitle": False,
    "title": "טבלה",
    "filters": [],
    "isDigitel": False,
    "isTitleOgen": False,
    "itemPageUrl": None,
    "maxColToShow": 7,
    "rowPaging": 15,
    "titleOgen": "טבלה",
}


@dataclass
class ProtocolItem:
    index: int
    title_he: str
    url: str
    meeting_date: str | None
    meeting_type: str | None
    council_term: str | None
    source_fields: dict[str, str]


@dataclass
class HttpBytes:
    body: bytes
    status_code: int
    content_type: str | None


def main() -> int:
    parser = argparse.ArgumentParser(description="Download Tel Aviv city-council protocol PDFs for later use.")
    parser.add_argument("--storage-root", type=Path, default=PROJECT_ROOT / "storage" / "raw")
    parser.add_argument("--manifest-path", type=Path)
    parser.add_argument("--limit", type=int, help="Limit downloads for smoke testing.")
    parser.add_argument("--start-index", type=int, default=1, help="Resume from this 1-based discovered item index.")
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="Download even when an existing manifest has the same URL and SHA.")
    parser.add_argument("--compact-manifest", action="store_true", help="Rewrite manifest to the latest row per source URL and exit.")
    args = parser.parse_args()

    started = time.perf_counter()
    storage_root = args.storage_root.expanduser().resolve()
    manifest_path = (args.manifest_path or storage_root / MUNICIPALITY_SLUG / "tel_aviv_protocols_manifest.jsonl").expanduser().resolve()
    if args.compact_manifest:
        print(json.dumps(_compact_manifest(manifest_path), ensure_ascii=False), flush=True)
        return 0

    timeout_seconds = float(args.timeout_seconds)
    items = _fetch_protocol_items(timeout_seconds=timeout_seconds)
    start_index = max(1, int(args.start_index))
    selected = [item for item in items if item.index >= start_index]
    selected = selected[: max(0, args.limit)] if args.limit is not None else selected
    print(
        json.dumps(
            {
                "phase": "discovered",
                "item_count": len(items),
                "selected_count": len(selected),
                "dry_run": bool(args.dry_run),
                "manifest_path": str(manifest_path),
                "storage_root": str(storage_root),
                "samples": [_sample_item(item) for item in selected[:3]],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    if args.dry_run:
        return 0

    storage = RawStorage(storage_root)
    existing_by_url = _read_existing_manifest(manifest_path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    summary = {"downloaded": 0, "skipped_existing": 0, "failed": 0}
    with manifest_path.open("a", encoding="utf-8") as manifest_file:
        for item in selected:
            try:
                result = _download_one(
                    storage=storage,
                    item=item,
                    existing_by_url=existing_by_url,
                    force=bool(args.force),
                    timeout_seconds=timeout_seconds,
                )
                manifest_file.write(json.dumps(result, ensure_ascii=False) + "\n")
                manifest_file.flush()
                status = str(result["status"])
                if status == "downloaded":
                    summary["downloaded"] += 1
                elif status == "skipped_existing":
                    summary["skipped_existing"] += 1
                else:
                    summary["failed"] += 1
                print(json.dumps(_progress_row(item, result), ensure_ascii=False), flush=True)
            except Exception as exc:  # noqa: BLE001 - downloader should keep evidence for failed rows.
                summary["failed"] += 1
                failure = {
                    "status": "failed",
                    "downloaded_at": _now_iso(),
                    "source_url": item.url,
                    "title_he": item.title_he,
                    "error": f"{exc.__class__.__name__}: {exc}",
                }
                manifest_file.write(json.dumps(failure, ensure_ascii=False) + "\n")
                manifest_file.flush()
                print(json.dumps(_progress_row(item, failure), ensure_ascii=False), flush=True)

    print(
        json.dumps(
            {
                "phase": "done",
                **summary,
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "manifest_path": str(manifest_path),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 1 if summary["failed"] else 0


def _fetch_protocol_items(*, timeout_seconds: float) -> list[ProtocolItem]:
    payload = _post_json(TABLE_ENDPOINT, PROTOCOL_TABLE_PAYLOAD, timeout_seconds=timeout_seconds)
    rows = payload.get("items") or []
    items: list[ProtocolItem] = []
    seen_urls: set[str] = set()
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        fields = _fields_by_caption(row.get("Fields") or [])
        title = fields.get("שם") or _first_field_value(row.get("Fields") or [], "LinkFilename") or f"protocol-{index}"
        for url in row.get("Attachments") or []:
            if not isinstance(url, str) or not url.strip():
                continue
            normalized_url = _quote_url(url.strip())
            if normalized_url in seen_urls:
                continue
            seen_urls.add(normalized_url)
            items.append(
                ProtocolItem(
                    index=len(items) + 1,
                    title_he=title,
                    url=normalized_url,
                    meeting_date=fields.get("תאריך הישיבה"),
                    meeting_type=fields.get("סוג ישיבה"),
                    council_term=fields.get("קדנציה מספר"),
                    source_fields=fields,
                )
            )
    return items


def _download_one(
    *,
    storage: RawStorage,
    item: ProtocolItem,
    existing_by_url: dict[str, dict[str, Any]],
    force: bool,
    timeout_seconds: float,
) -> dict[str, Any]:
    response = _get_bytes(item.url, timeout_seconds=timeout_seconds)
    data = response.body
    digest = hashlib.sha256(data).hexdigest()
    existing = existing_by_url.get(item.url)
    if existing and existing.get("sha256") == digest and not force:
        return {**existing, "status": "skipped_existing", "downloaded_at": _now_iso()}

    if item.url.lower().endswith(".pdf") and not data.startswith(b"%PDF"):
        raise ValueError("downloaded file does not start with a PDF header")

    # Store both by source tree and by content hash. The tree copy preserves the
    # municipal site layout; the hash copy matches the existing raw archive API.
    tree_uri = storage.write_tree(MUNICIPALITY_SLUG, item.url, data, tree_segments=["city_council_protocols"])
    storage_uri = storage.write(MUNICIPALITY_SLUG, DOC_KIND, digest, data)
    return {
        "status": "downloaded",
        "downloaded_at": _now_iso(),
        "source_url": item.url,
        "title_he": item.title_he,
        "meeting_date": item.meeting_date,
        "meeting_type": item.meeting_type,
        "council_term": item.council_term,
        "doc_kind": DOC_KIND,
        "sha256": digest,
        "byte_size": len(data),
        "content_type": response.content_type,
        "http_status": response.status_code,
        "storage_uri": storage_uri,
        "tree_uri": tree_uri,
        "source_fields": item.source_fields,
    }


def _post_json(url: str, payload: dict[str, Any], *, timeout_seconds: float) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "MunicipalityResearchDownloader/0.1",
        },
    )
    with request.urlopen(req, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8-sig"))


def _get_bytes(url: str, *, timeout_seconds: float) -> HttpBytes:
    req = request.Request(url, headers={"User-Agent": "MunicipalityResearchDownloader/0.1"})
    with request.urlopen(req, timeout=timeout_seconds) as response:
        return HttpBytes(
            body=response.read(),
            status_code=int(response.status),
            content_type=response.headers.get("content-type"),
        )


def _read_existing_manifest(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    by_url: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        url = row.get("source_url")
        if isinstance(url, str) and row.get("sha256"):
            by_url[url] = row
    return by_url


def _compact_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"phase": "compact_manifest", "manifest_path": str(path), "rows_before": 0, "rows_after": 0}
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row.get("source_url"), str):
            rows.append(row)
    latest: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in rows:
        url = str(row["source_url"])
        if url not in latest:
            order.append(url)
        latest[url] = row
    path.write_text("".join(json.dumps(latest[url], ensure_ascii=False) + "\n" for url in order), encoding="utf-8")
    failed_after = sum(1 for row in latest.values() if row.get("status") == "failed")
    return {
        "phase": "compact_manifest",
        "manifest_path": str(path),
        "rows_before": len(rows),
        "rows_after": len(latest),
        "failed_after": failed_after,
    }


def _fields_by_caption(fields: list[Any]) -> dict[str, str]:
    values: dict[str, str] = {}
    for field in fields:
        if not isinstance(field, dict):
            continue
        caption = str(field.get("Caption") or "").strip()
        value = str(field.get("Value") or "").strip()
        if caption:
            values[caption] = value
    return values


def _first_field_value(fields: list[Any], internal_name: str) -> str | None:
    for field in fields:
        if isinstance(field, dict) and field.get("InternalName") == internal_name:
            value = str(field.get("Value") or "").strip()
            return value or None
    return None


def _quote_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, quote(parts.path), parts.query, parts.fragment))


def _sample_item(item: ProtocolItem) -> dict[str, Any]:
    return {
        "index": item.index,
        "title_he": item.title_he,
        "meeting_date": item.meeting_date,
        "meeting_type": item.meeting_type,
        "url": item.url,
    }


def _progress_row(item: ProtocolItem, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "phase": "progress",
        "index": item.index,
        "status": result.get("status"),
        "title_he": item.title_he,
        "byte_size": result.get("byte_size"),
        "sha256": result.get("sha256"),
        "tree_uri": result.get("tree_uri"),
        "error": result.get("error"),
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
