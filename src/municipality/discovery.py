from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from urllib.parse import parse_qs, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from municipality.text import normalize_hebrew_text

TRACKING_PREFIXES = ("utm_", "fbclid", "gclid", "mc_", "ref")
ALLOWED_YEARS = {"2025", "2026"}

MIME_BY_SUFFIX = {
    ".pdf": "application/pdf",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
}


def normalize_url(raw_url: str, base_url: str | None = None) -> str:
    absolute = urljoin(base_url, raw_url) if base_url else raw_url
    parsed = urlparse(absolute)
    cleaned_query = []
    for pair in parse_qs(parsed.query, keep_blank_values=True).items():
        key, values = pair
        if any(key.lower().startswith(prefix) for prefix in TRACKING_PREFIXES):
            continue
        for value in values:
            cleaned_query.append((key, value))
    cleaned_query.sort(key=lambda item: item[0])
    query = "&".join(f"{k}={v}" for k, v in cleaned_query)
    normalized_path = re.sub(r"/+", "/", parsed.path or "/")
    return urlunparse(
        (parsed.scheme.lower(), parsed.netloc.lower(), normalized_path, "", query, "")
    )


def resolve_external_id(canonical_url: str) -> str:
    parsed = urlparse(canonical_url)
    query = parse_qs(parsed.query)
    media_ids = query.get("parentMediaID") or query.get("parentmediaid")
    if media_ids and media_ids[0]:
        return f"pmid:{media_ids[0]}"
    digest = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:24]
    return f"urlhash:{digest}"


@dataclass(slots=True)
class LinkAsset:
    title_he: str
    url: str
    canonical_url: str
    source_node_external_id: str
    mime_hint: str
    discovered_at: datetime


@dataclass(slots=True)
class LinkNode:
    title_he: str
    url: str
    canonical_url: str
    external_id: str
    parent_external_id: str | None
    node_type: str
    depth: int
    count_hint: int | None
    discovered_at: datetime


@dataclass(slots=True)
class LinkCandidate:
    title_he: str
    href: str
    count_hint: int | None = None


class DiscoveryAdapter(Protocol):
    def extract_title(self, soup: BeautifulSoup, canonical_url: str) -> str: ...

    def extract_link_candidates(self, soup: BeautifulSoup, canonical_url: str) -> list[LinkCandidate]: ...

    def in_scope(self, canonical_url: str) -> bool: ...

    def infer_node_type(self, canonical_url: str, title_he: str) -> str: ...


class DiscoveryEngine:
    def __init__(self, html_fetcher, adapter: DiscoveryAdapter | None = None):
        self.html_fetcher = html_fetcher
        self.adapter = adapter

    def crawl(
        self, root_url: str, max_depth: int = 8
    ) -> tuple[list[LinkNode], list[LinkAsset]]:
        root_canonical = normalize_url(root_url)
        root_external = resolve_external_id(root_canonical)
        queue: list[tuple[str, int, str | None, int | None]] = [(root_canonical, 0, None, None)]
        visited: set[str] = set()
        nodes: list[LinkNode] = []
        assets: list[LinkAsset] = []

        while queue:
            url, depth, parent_external_id, count_hint = queue.pop(0)
            if url in visited or depth > max_depth:
                continue
            visited.add(url)

            html = self.html_fetcher(url)
            now = datetime.utcnow()
            soup = BeautifulSoup(html, "html.parser")
            external_id = root_external if depth == 0 else resolve_external_id(url)
            title = self._extract_title(soup, url)
            nodes.append(
                LinkNode(
                    title_he=title,
                    url=url,
                    canonical_url=url,
                    external_id=external_id,
                    parent_external_id=parent_external_id,
                    node_type=self._infer_node_type(url, title),
                    depth=depth,
                    count_hint=count_hint,
                    discovered_at=now,
                )
            )

            for candidate in self._extract_link_candidates(soup, url):
                canonical = normalize_url(candidate.href, url)
                mime_hint = _guess_mime(canonical)
                if mime_hint:
                    assets.append(
                        LinkAsset(
                            title_he=normalize_hebrew_text(candidate.title_he),
                            url=canonical,
                            canonical_url=canonical,
                            source_node_external_id=external_id,
                            mime_hint=mime_hint,
                            discovered_at=now,
                        )
                    )
                    continue
                if self._in_scope(canonical) and canonical not in visited:
                    queue.append((canonical, depth + 1, external_id, candidate.count_hint))

        return nodes, assets

    def _extract_title(self, soup: BeautifulSoup, canonical_url: str) -> str:
        if self.adapter:
            return self.adapter.extract_title(soup, canonical_url)
        title = soup.title.string if soup.title and soup.title.string else ""
        return normalize_hebrew_text(title.strip()) or "untitled"

    def _extract_link_candidates(self, soup: BeautifulSoup, canonical_url: str) -> list[LinkCandidate]:
        if self.adapter:
            return self.adapter.extract_link_candidates(soup, canonical_url)

        candidates: list[LinkCandidate] = []
        for anchor in soup.select("a[href]"):
            href_raw = anchor.get("href")
            if href_raw is None:
                continue
            href = str(href_raw).strip()
            if not href:
                continue
            title = normalize_hebrew_text(anchor.get_text(strip=True) or href)
            candidates.append(LinkCandidate(title_he=title, href=href))
        return candidates

    def _in_scope(self, canonical_url: str) -> bool:
        if self.adapter:
            return self.adapter.in_scope(canonical_url)
        if any(year in canonical_url for year in ALLOWED_YEARS):
            return True
        return "protocol" in canonical_url.lower() or "פרוטוקול" in canonical_url

    def _infer_node_type(self, canonical_url: str, title_he: str) -> str:
        if self.adapter:
            return self.adapter.infer_node_type(canonical_url, title_he)
        lower = canonical_url.lower()
        if re.search(r"/202[5-6]", lower):
            return "year_folder"
        if "meeting" in lower or "protocol" in lower or "פרוטוקול" in canonical_url:
            return "meeting_folder"
        return "generic_subfolder"


def classify_asset_kind(title_he: str, mime_hint: str) -> str:
    lower = title_he.lower()
    if "פרוטוקול קצר" in title_he or "short" in lower:
        return "protocol_short"
    if "פרוטוקול" in title_he or "protocol" in lower:
        return "protocol_full"
    if "נספח" in title_he or "attachment" in lower:
        return "attachment"
    if "audio" in lower:
        return "audio"
    if "video" in lower:
        return "video"
    if mime_hint != "application/pdf":
        return "other"
    return "attachment"


def _guess_mime(canonical_url: str) -> str | None:
    lower = urlparse(canonical_url).path.lower()
    for suffix, mime in MIME_BY_SUFFIX.items():
        if lower.endswith(suffix):
            return mime
    return None
