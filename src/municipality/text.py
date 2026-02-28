from __future__ import annotations

import re

HEBREW_RE = re.compile(r"[\u0590-\u05FF]")
STRIP_BIDI_RE = re.compile(r"[\u200E\u200F\u202A-\u202E]")


def normalize_hebrew_text(value: str) -> str:
    text = STRIP_BIDI_RE.sub("", " ".join(value.split()))
    if not text or not HEBREW_RE.search(text):
        return text
    return text
