from __future__ import annotations

import re
import unicodedata
from typing import Mapping

_HEBREW_MARKS_RE = re.compile(r"[\u0591-\u05C7]")
_GERESH_RE = re.compile(r"[׳'`]")
_GERSHAYIM_RE = re.compile(r"[״\"]|''")
_SPACE_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\u0590-\u05FF/\- ]+", re.UNICODE)
_LABELED_CADASTRAL_RE = re.compile(r"גוש\s*(?P<gush>\d+)\D+חלקה\s*(?P<helka>\d+)")
_SLASH_CADASTRAL_RE = re.compile(r"^(?P<gush>\d+)\s*[/\-]\s*(?P<helka>\d+)$")


def normalize_gershayim(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = _GERSHAYIM_RE.sub("", text)
    return _GERESH_RE.sub("", text)


def normalize_hebrew_text(value: str) -> str:
    text = normalize_gershayim(value)
    text = _HEBREW_MARKS_RE.sub("", text)
    text = _PUNCT_RE.sub(" ", text)
    return _SPACE_RE.sub(" ", text).strip().lower()


def normalize_cadastral_id(value: str) -> str:
    raw = _SPACE_RE.sub(" ", str(value or "")).strip()
    normalized = normalize_hebrew_text(raw)
    match = _LABELED_CADASTRAL_RE.search(normalized) or _SLASH_CADASTRAL_RE.search(normalized)
    if not match:
        return raw
    return f"{int(match.group('gush'))}/{int(match.group('helka'))}"


def extract_cadastral_id(value: str) -> tuple[str, str] | None:
    normalized = normalize_cadastral_id(value)
    match = _SLASH_CADASTRAL_RE.search(normalized)
    if not match:
        return None
    return str(int(match.group("gush"))), str(int(match.group("helka")))


def normalize_address(value: str, *, synonyms: Mapping[str, str] | None = None) -> str:
    tokens = normalize_hebrew_text(value).split()
    if synonyms:
        normalized_synonyms = {normalize_hebrew_text(key): normalize_hebrew_text(replacement) for key, replacement in synonyms.items()}
        tokens = [normalized_synonyms.get(token, token) for token in tokens]
    return " ".join(tokens)
