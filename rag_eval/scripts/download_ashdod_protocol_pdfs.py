from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from collections import deque
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup


START_URL = "https://www.ashdod.muni.il/he-il/אתר-העיר/פרוטוקולים/רשימת-פרוטוקולים-לפי-נושא/?parentMediaID=42627&title=ישיבות%20מועצה"
DOMAIN = "www.ashdod.muni.il"
LISTING_MARKER = "/he-il/אתר-העיר/פרוטוקולים/רשימת-פרוטוקולים-לפי-נושא/"


def main() -> int:
    parser = argparse.ArgumentParser(description="Recursively download only PDF files from Ashdod council protocol tree")
    parser.add_argument("--start-url", default=START_URL)
    parser.add_argument("--output-dir", type=Path, default=Path("rag_eval/data/raw_docs/ashdod_council"))
    parser.add_argument("--links-file", type=Path, default=None)
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--reuse-links", action="store_true", help="Reuse an existing pdf_links.jsonl instead of crawling")
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument("--sleep-seconds", type=float, default=0.03)
    parser.add_argument("--max-downloads", type=int, default=None, help="Optional batch size for resumable runs")
    args = parser.parse_args()

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    links_file = args.links_file or output_dir / "pdf_links.jsonl"
    manifest = args.manifest or output_dir / "download_manifest.jsonl"

    headers = {
        "User-Agent": "MunicipalityRagEvalBot/0.1 (+local research)",
        "Accept": "text/html,application/pdf;q=0.9,*/*;q=0.8",
    }

    if args.reuse_links and links_file.exists():
        pdf_links = read_links_file(links_file)
        print(f"reused_links={len(pdf_links)} file={links_file}", flush=True)
    else:
        pdf_links = crawl_pdf_links(
            start_url=args.start_url,
            headers=headers,
            timeout_seconds=args.timeout_seconds,
            sleep_seconds=args.sleep_seconds,
        )
        write_links_file(links_file, pdf_links)
    saved = download_pdfs(
        pdf_links=pdf_links,
        output_dir=output_dir,
        manifest_path=manifest,
        headers=headers,
        timeout_seconds=args.timeout_seconds,
        sleep_seconds=args.sleep_seconds,
        max_downloads=args.max_downloads,
    )
    existing_count = len(list(output_dir.glob("*.pdf")))
    print(
        json.dumps(
            {
                "pdf_links": len(pdf_links),
                "saved_or_existing_this_run": saved,
                "pdf_files_on_disk": existing_count,
                "links_file": links_file.as_posix(),
                "manifest": manifest.as_posix(),
            },
            ensure_ascii=False,
        )
    )
    return 0


def crawl_pdf_links(
    *,
    start_url: str,
    headers: dict[str, str],
    timeout_seconds: float,
    sleep_seconds: float,
) -> dict[str, dict[str, str]]:
    seen_pages: set[str] = set()
    queue = deque([start_url])
    pdf_links: dict[str, dict[str, str]] = {}

    with httpx.Client(timeout=timeout_seconds, follow_redirects=True, headers=headers) as client:
        while queue:
            page_url = queue.popleft().split("#", 1)[0]
            if page_url in seen_pages or not is_listing_url(page_url):
                continue
            seen_pages.add(page_url)
            try:
                response = client.get(to_uri(page_url))
                response.raise_for_status()
            except Exception as exc:  # noqa: BLE001
                print(f"page_error {exc.__class__.__name__}:{exc} {page_url}")
                continue

            soup = BeautifulSoup(response.text, "html.parser")
            for anchor in soup.find_all("a", href=True):
                href = str(anchor.get("href") or "").strip()
                if not href or href.startswith("javascript:") or href.startswith("mailto:"):
                    continue
                absolute = urljoin(page_url, href).split("#", 1)[0]
                parsed = urlparse(absolute)
                if parsed.netloc and parsed.netloc != DOMAIN:
                    continue
                if is_pdf_url(absolute):
                    pdf_links.setdefault(
                        absolute,
                        {
                            "title": anchor.get_text(" ", strip=True),
                            "referer": page_url,
                        },
                    )
                elif is_listing_url(absolute) and absolute not in seen_pages:
                    queue.append(absolute)

            if len(seen_pages) % 50 == 0:
                print(f"crawled_pages={len(seen_pages)} pdf_links={len(pdf_links)} queue={len(queue)}")
            time.sleep(sleep_seconds)

    print(f"crawl_complete pages={len(seen_pages)} pdf_links={len(pdf_links)}")
    return pdf_links


def download_pdfs(
    *,
    pdf_links: dict[str, dict[str, str]],
    output_dir: Path,
    manifest_path: Path,
    headers: dict[str, str],
    timeout_seconds: float,
    sleep_seconds: float,
    max_downloads: int | None,
) -> int:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    saved = 0
    new_downloads = 0
    with httpx.Client(timeout=timeout_seconds, follow_redirects=True, headers=headers) as client:
        with manifest_path.open("a", encoding="utf-8") as manifest_handle:
            for index, (url, meta) in enumerate(sorted(pdf_links.items()), start=1):
                target = output_dir / safe_name(url, meta.get("title") or "")
                if target.exists() and target.stat().st_size > 0:
                    row = manifest_row(status="exists", url=url, meta=meta, target=target, body=None, content_type=None)
                    write_manifest_row(manifest_handle, row)
                    saved += 1
                    if index % 50 == 0:
                        print(f"{index}/{len(pdf_links)} exists {target.name}")
                    continue

                if max_downloads is not None and new_downloads >= max_downloads:
                    break

                try:
                    response = client.get(to_uri(url))
                except Exception as exc:  # noqa: BLE001
                    print(f"download_error {exc.__class__.__name__}:{exc} {url}")
                    continue
                body = response.content
                content_type = response.headers.get("content-type", "")
                if response.status_code >= 400 or ("pdf" not in content_type.lower() and not body.startswith(b"%PDF")):
                    print(f"skip_non_pdf status={response.status_code} type={content_type} url={url}")
                    continue

                target.write_bytes(body)
                row = manifest_row(status="downloaded", url=url, meta=meta, target=target, body=body, content_type=content_type)
                write_manifest_row(manifest_handle, row)
                saved += 1
                new_downloads += 1
                print(f"{index}/{len(pdf_links)} downloaded {target.name} bytes={len(body)}")
                time.sleep(sleep_seconds)
    return saved


def write_links_file(path: Path, pdf_links: dict[str, dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for url, meta in sorted(pdf_links.items()):
            handle.write(json.dumps({"url": url, **meta}, ensure_ascii=False, sort_keys=True) + "\n")


def read_links_file(path: Path) -> dict[str, dict[str, str]]:
    links: dict[str, dict[str, str]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            url = str(row.get("url") or "").strip()
            if not url:
                continue
            links[url] = {
                "title": str(row.get("title") or ""),
                "referer": str(row.get("referer") or ""),
            }
    return links


def write_manifest_row(handle, row: dict) -> None:
    handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    handle.flush()


def manifest_row(
    *,
    status: str,
    url: str,
    meta: dict[str, str],
    target: Path,
    body: bytes | None,
    content_type: str | None,
) -> dict:
    if body is None and target.exists():
        body = target.read_bytes()
    return {
        "status": status,
        "source_url": url,
        "referer": meta.get("referer"),
        "title_hint": meta.get("title"),
        "file": target.as_posix(),
        "bytes": len(body or b""),
        "sha256": hashlib.sha256(body or b"").hexdigest(),
        "content_type": content_type,
    }


def is_listing_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.netloc and parsed.netloc != DOMAIN:
        return False
    decoded_path = unquote(parsed.path)
    return decoded_path.rstrip("/") == LISTING_MARKER.rstrip("/") and bool(parse_qs(parsed.query).get("parentMediaID"))


def is_pdf_url(url: str) -> bool:
    return unquote(urlparse(url).path).casefold().endswith(".pdf")


def safe_name(url: str, title_hint: str) -> str:
    parsed = urlparse(url)
    name = unquote(Path(parsed.path).name) or (title_hint or "protocol") + ".pdf"
    stem = Path(name).stem.strip() or title_hint or "protocol"
    stem = re.sub(r"[<>:\"/\\|?*\x00-\x1f]+", "_", stem).strip(" ._") or "protocol"
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]
    return f"{stem}__{digest}.pdf"


def to_uri(url: str) -> str:
    parsed = urlparse(url)
    path = quote(unquote(parsed.path), safe="/%")
    query = quote(unquote(parsed.query), safe="=&%")
    return urlunparse((parsed.scheme, parsed.netloc, path, parsed.params, query, ""))


if __name__ == "__main__":
    raise SystemExit(main())
