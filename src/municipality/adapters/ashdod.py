from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote, urlparse

from bs4 import BeautifulSoup

from municipality.discovery import ALLOWED_YEARS, LinkCandidate
from municipality.text import normalize_hebrew_text


HE_PROTOCOLS = "\u05e4\u05e8\u05d5\u05d8\u05d5\u05e7\u05d5\u05dc\u05d9\u05dd"
HE_APPENDIX = "\u05e0\u05e1\u05e4\u05d7"
HE_REGULAR = "\u05e8\u05d2\u05d9\u05dc\u05d4"
HE_SPECIAL = "\u05de\u05d9\u05d5\u05d7\u05d3\u05ea"
HE_COMMITTEE = "\u05d5\u05e2\u05d3\u05d4"
HE_COUNCIL_MEETINGS = "\u05d9\u05e9\u05d9\u05d1\u05d5\u05ea \u05de\u05d5\u05e2\u05e6\u05d4"
HE_PLENUM = "\u05de\u05dc\u05d9\u05d0\u05ea"


class AshdodDiscoveryAdapter:
    def extract_title(self, soup: BeautifulSoup, canonical_url: str) -> str:
        heading = (
            soup.select_one("main h1")
            or soup.select_one("h1")
            or soup.select_one("main h2")
        )
        if heading is not None:
            text = heading.get_text(" ", strip=True)
            if text:
                return text
        if soup.title and soup.title.string:
            text = soup.title.string.strip()
            if text:
                return text
        return "untitled"

    def extract_link_candidates(
        self, soup: BeautifulSoup, canonical_url: str
    ) -> list[LinkCandidate]:
        candidates: list[LinkCandidate] = []
        seen: set[str] = set()

        for anchor in soup.select("a[href]"):
            text_getter = getattr(anchor, "get_text", None)
            href_getter = getattr(anchor, "get", None)
            if text_getter is None or href_getter is None:
                continue
            href = str(href_getter("href", "")).strip()
            if not href:
                continue

            if self._is_media_asset_href(href):
                title = _normalize_text(text_getter(" ", strip=True)) or href
                if href not in seen:
                    candidates.append(LinkCandidate(title_he=title, href=href))
                    seen.add(href)
                continue

            if self._is_folder_card(anchor):
                h3 = anchor.select_one("h3")
                title_text = h3.get_text(" ", strip=True) if h3 else ""
                title = _normalize_text(title_text) or href
                count_hint = _extract_leading_count(
                    text_getter(" ", strip=True), title
                )
                if href not in seen:
                    candidates.append(
                        LinkCandidate(title_he=title, href=href, count_hint=count_hint)
                    )
                    seen.add(href)

        if candidates:
            return candidates

        for anchor in soup.select("a[href]"):
            text_getter = getattr(anchor, "get_text", None)
            href_getter = getattr(anchor, "get", None)
            if text_getter is None or href_getter is None:
                continue
            href = str(href_getter("href", "")).strip()
            if not href:
                continue
            if self._is_protocol_parent_media_link(href):
                title = _normalize_text(text_getter(" ", strip=True))
                if href not in seen:
                    candidates.append(LinkCandidate(title_he=title or href, href=href))
                    seen.add(href)
        return candidates

    def in_scope(self, canonical_url: str) -> bool:
        parsed = urlparse(canonical_url)
        if "ashdod.muni.il" not in parsed.netloc.lower():
            return False

        decoded_path = unquote(parsed.path).lower()
        query = parse_qs(parsed.query)
        has_parent_media = "parentMediaID" in query or "parentmediaid" in query
        if (
            HE_PROTOCOLS not in decoded_path
            and "protocol" not in decoded_path
            and not has_parent_media
        ):
            return False

        title = _first_query(query, "title")
        if title and re.fullmatch(r"20\d{2}", title):
            return title in ALLOWED_YEARS

        if re.search(r"\b20\d{2}\b", title or ""):
            year = re.search(r"\b(20\d{2})\b", title or "")
            if year and year.group(1) not in ALLOWED_YEARS:
                return False

        return True

    def infer_node_type(self, canonical_url: str, title_he: str) -> str:
        parsed = urlparse(canonical_url)
        query = parse_qs(parsed.query)
        title = _first_query(query, "title") or title_he
        if re.fullmatch(r"20\d{2}", title):
            return "year_folder"
        if HE_APPENDIX in title:
            return "generic_subfolder"
        if (
            re.search(r"\d{1,2}[./]\d{1,2}[./]\d{2,4}", title)
            or HE_REGULAR in title
            or HE_SPECIAL in title
        ):
            return "meeting_folder"
        if HE_COMMITTEE in title or HE_COUNCIL_MEETINGS in title or HE_PLENUM in title:
            return "topic_folder"
        return "generic_subfolder"

    @staticmethod
    def _is_folder_card(anchor) -> bool:
        if not anchor.select_one("h3"):
            return False
        image = anchor.select_one("img")
        if not image:
            return False
        src = str(image.get("src", "")).lower()
        return "folder-icon" in src

    @staticmethod
    def _is_media_asset_href(href: str) -> bool:
        parsed = urlparse(href)
        path = parsed.path.lower()
        if "/media/" not in path:
            return False
        return any(
            path.endswith(suffix) for suffix in (".pdf", ".mp3", ".wav", ".mp4", ".mov")
        )

    @staticmethod
    def _is_protocol_parent_media_link(href: str) -> bool:
        parsed = urlparse(href)
        query = parse_qs(parsed.query)
        has_parent_media = "parentMediaID" in query or "parentmediaid" in query
        if not has_parent_media:
            return False
        decoded_path = unquote(parsed.path).lower()
        return HE_PROTOCOLS in decoded_path or "protocol" in decoded_path


def _first_query(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key) or query.get(key.lower())
    if not values:
        return None
    return values[0].strip()


def _normalize_text(value: str) -> str:
    return normalize_hebrew_text(value)


def _extract_leading_count(raw_text: str, title: str) -> int | None:
    compact = _normalize_text(raw_text)
    if not compact:
        return None
    prefix = compact
    if title and title in compact:
        prefix = compact.split(title, 1)[0].strip()
    match = re.search(r"(\d+)", prefix)
    if not match:
        return None
    return int(match.group(1))
