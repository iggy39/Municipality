from __future__ import annotations

import re

from municipality.chunking import normalize_for_search


HEBREW_TOPIC_TOKEN_RE = re.compile(r"[\u0590-\u05FF]{2,}")

LOW_QUALITY_TOPIC_EXACT = {
    normalize_for_search("החלטות עירוניות"),
    normalize_for_search("החלטה ענפית"),
    normalize_for_search("החלטה כללית"),
    normalize_for_search("עיקרי ההחלטה"),
    normalize_for_search("תוכן ההחלטה"),
    normalize_for_search("נושא ההחלטה"),
    normalize_for_search("מכותבים"),
    normalize_for_search("מכותבים תוכן ההחלטה"),
}

LOW_QUALITY_TOPIC_PHRASES = (
    normalize_for_search("עיקרי ההחלטה"),
    normalize_for_search("תוכן ההחלטה"),
    normalize_for_search("נושא ההחלטה"),
    normalize_for_search("מכותבים"),
    normalize_for_search("עדכון ניקוד"),
)

LOW_QUALITY_TOPIC_TOKEN_SETS = (
    {"עיקרי", "החלטה"},
    {"עיקרי", "החלטות"},
    {"תוכן", "החלטה"},
    {"תוכן", "החלטות"},
    {"מכותבים", "החלטה"},
    {"מכותבים", "החלטות"},
    {"עדכון", "ניקוד"},
)

LOW_QUALITY_TOPIC_LEAD_TOKENS = {
    "מכותבים",
    "עיקרי",
    "תוכן",
    "חתימות",
    "נספחים",
}


def topic_quality_tokens(value: str) -> list[str]:
    normalized = normalize_for_search(" ".join(str(value or "").split()))
    if not normalized:
        return []
    return [token for token in HEBREW_TOPIC_TOKEN_RE.findall(normalized) if token]


def is_low_quality_topic_label(value: str | None) -> bool:
    compact = " ".join(str(value or "").split()).strip()
    if not compact:
        return True

    normalized = normalize_for_search(compact)
    if not normalized:
        return True
    if normalized in LOW_QUALITY_TOPIC_EXACT:
        return True
    if any(phrase in normalized for phrase in LOW_QUALITY_TOPIC_PHRASES):
        return True

    tokens = topic_quality_tokens(normalized)
    if not tokens:
        return True

    token_set = set(tokens)
    if any(pattern.issubset(token_set) for pattern in LOW_QUALITY_TOPIC_TOKEN_SETS):
        return True
    if tokens[0] in LOW_QUALITY_TOPIC_LEAD_TOKENS and len(tokens) <= 4:
        return True
    return False


def sanitize_topic_path_label(value: str | None) -> str | None:
    parts = [part.strip() for part in str(value or "").split(">") if part and part.strip()]
    if not parts:
        compact = " ".join(str(value or "").split()).strip()
        return None if is_low_quality_topic_label(compact) else (compact or None)

    cleaned_parts: list[str] = []
    for part in parts:
        compact = " ".join(part.split()).strip()
        if not compact or is_low_quality_topic_label(compact):
            continue
        cleaned_parts.append(compact)

    if not cleaned_parts:
        return None
    return " > ".join(cleaned_parts)
