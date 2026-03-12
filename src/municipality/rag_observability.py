from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence


DEFAULT_RAG_AUDIT_SAMPLE_RATE = 0.05

_LOGGER = logging.getLogger("municipality.rag")


def new_ask_request_id() -> str:
    return uuid.uuid4().hex


def hash_text(value: str) -> str:
    compact = value.strip()
    if not compact:
        return ""
    digest = hashlib.sha256(compact.encode("utf-8")).hexdigest()
    return digest[:24]


def build_retrieval_set_id(
    *,
    normalized_query: str,
    requested_source_kinds: Sequence[str],
    top_k: int,
    chunk_ids: Sequence[str],
) -> str:
    payload = {
        "normalized_query": normalized_query,
        "requested_source_kinds": list(requested_source_kinds),
        "top_k": top_k,
        "chunk_ids": list(chunk_ids),
    }
    packed = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(packed.encode("utf-8")).hexdigest()[:20]


def audit_sample_rate(env: Mapping[str, str] | None = None) -> float:
    source = env if env is not None else os.environ
    raw = source.get("RAG_AUDIT_SAMPLE_RATE")
    if raw is None:
        return DEFAULT_RAG_AUDIT_SAMPLE_RATE
    try:
        parsed = float(raw)
    except ValueError:
        return DEFAULT_RAG_AUDIT_SAMPLE_RATE
    return max(0.0, min(1.0, parsed))


def should_sample_audit(*, rate: float, random_value: float | None = None) -> bool:
    bounded = max(0.0, min(1.0, float(rate)))
    if bounded <= 0.0:
        return False
    probe = random.random() if random_value is None else random_value
    return probe < bounded


def log_rag_event(event: str, **fields: Any) -> None:
    payload: dict[str, Any] = {
        "event": event,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }
    for key, value in fields.items():
        if value is None:
            continue
        payload[key] = _normalize_log_value(value)
    _LOGGER.info(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))


def _normalize_log_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_normalize_log_value(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize_log_value(item) for item in value]
    if isinstance(value, set):
        normalized_items = [_normalize_log_value(item) for item in value]
        return sorted(normalized_items, key=lambda item: str(item))
    if isinstance(value, dict):
        return {str(key): _normalize_log_value(item) for key, item in value.items()}
    return str(value)
