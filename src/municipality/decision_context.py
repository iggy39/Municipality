from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from municipality.chunking import normalize_for_search
from municipality.models import Decision, DecisionCitation, DecisionRequestContext, TextChunk


REQUEST_SUBJECT_RE = re.compile(r"מהות\s+הבקשה\s*:\s*(.+)")
REQUEST_INLINE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(בקשה\s+ל(?:מתן|הסדרת|הקצאת|הקצאה|החלפת|ביטול|קבלת)[^.\n]{0,260})"),
    re.compile(r"(בקשת\s+[^.\n:]{0,60}?\s+ל(?:מתן|הסדרת|הקצאת|הקצאה|החלפת|ביטול|קבלת)[^.\n]{0,260})"),
    re.compile(r"(העמותה\s+מבקשת\s+[^.\n]{0,260})"),
    re.compile(r"(מבקשת\s+[^.\n]{0,260})"),
    re.compile(r"(מבוקש(?:ת)?\s+[^.\n]{0,260})"),
    re.compile(r"(ביטול\s+הקצאה[^.\n]{0,260})"),
    re.compile(r"(החלפת\s+הקצאה[^.\n]{0,260})"),
)
ADDRESS_RE = re.compile(r"כתובת\s*:\s*(.+)")
GUSH_RE = re.compile(r"גוש\s*[:\-]?\s*(\d{2,6})")
GUSH_COMPACT_RE = re.compile(r"גוש(\d{2,6})")
HELKA_RE = re.compile(r"חלקה\s*[:\-]?\s*(\d{1,6})")
MIGRASH_RE = re.compile(r"מגרש\s*[:\-]?\s*(\d{1,6})")
REQUEST_STOP_MARKERS = (
    "מורשי חתימה",
    "חברי הנהלה",
    "בעלי זכות חתימה",
    "הבקשה פורסמה",
    "הוצבה הודעה",
    "הערת",
    "לא התקבל",
    "הוצג",
    "פרסום ראשון",
    "פרסום שני",
)
GENERIC_TOPIC_VALUES = {
    normalize_for_search("נושא כללי"),
    normalize_for_search("החלטה כללית"),
    normalize_for_search("החלטות עירוניות"),
}
SUBJECT_NOISE_TOKENS = {
    "כתובת",
    "גוש",
    "חלקה",
    "מגרש",
    "שטח",
    "שימושים",
    "תאור",
    "בעלי",
    "ענין",
    "מבקש",
    "סעיף",
}


class DecisionContextService:
    def __init__(self, session: Session):
        self.session = session

    def process_document(
        self,
        *,
        source_document_id: int,
        document_version_id: int,
        source_kind: str,
    ) -> dict[str, int]:
        if source_kind != "protocol":
            return {"contexts": 0}

        decisions = self.session.execute(
            select(Decision)
            .where(Decision.source_document_id == source_document_id)
            .order_by(Decision.id.asc())
        ).scalars().all()
        if not decisions:
            return {"contexts": 0}

        citations = self.session.execute(
            select(DecisionCitation)
            .where(
                DecisionCitation.document_id == source_document_id,
                DecisionCitation.document_version_id == document_version_id,
                DecisionCitation.source_type == "protocol",
            )
            .order_by(DecisionCitation.start_offset.asc(), DecisionCitation.id.asc())
        ).scalars().all()
        citations_by_decision: dict[int, list[DecisionCitation]] = {}
        for citation in citations:
            citations_by_decision.setdefault(int(citation.decision_id), []).append(citation)

        chunks = self.session.execute(
            select(TextChunk)
            .where(
                TextChunk.document_id == source_document_id,
                TextChunk.document_version_id == document_version_id,
                TextChunk.source_kind == "protocol",
            )
            .order_by(TextChunk.chunk_index.asc())
        ).scalars().all()
        if not chunks:
            return {"contexts": 0}

        existing_rows = self.session.execute(
            select(DecisionRequestContext).where(
                DecisionRequestContext.decision_id.in_([decision.id for decision in decisions])
            )
        ).scalars().all()
        existing_by_decision = {int(row.decision_id): row for row in existing_rows}

        touched = 0
        now = datetime.utcnow()
        for decision in decisions:
            payload = _build_request_context_payload(
                decision=decision,
                citations=citations_by_decision.get(int(decision.id), []),
                chunks=chunks,
            )
            if payload is None:
                continue

            row = existing_by_decision.get(int(decision.id))
            if row is None:
                row = DecisionRequestContext(
                    decision_id=decision.id,
                    source_document_id=source_document_id,
                    created_at=now,
                    updated_at=now,
                )
                self.session.add(row)

            row.source_document_id = source_document_id
            row.request_subject_he = payload.get("request_subject_he")
            row.subject_topic_he = payload.get("subject_topic_he")
            row.address_he = payload.get("address_he")
            row.gush = payload.get("gush")
            row.helka = payload.get("helka")
            row.migrash = payload.get("migrash")
            row.source_chunk_ids_json = json.dumps(payload.get("source_chunk_ids") or [], ensure_ascii=False)
            row.confidence = payload.get("confidence")
            row.metadata_json = json.dumps(payload.get("metadata") or {}, ensure_ascii=False)
            row.updated_at = now
            touched += 1

        return {"contexts": touched}


def _build_request_context_payload(
    *,
    decision: Decision,
    citations: list[DecisionCitation],
    chunks: Sequence[TextChunk],
) -> dict[str, Any] | None:
    anchor_index = _anchor_chunk_index(citations=citations, chunks=chunks)
    if anchor_index is None:
        return None

    window_chunks = _window_chunks(chunks=chunks, anchor_index=anchor_index, lookback=10, lookahead=2)
    request_chunk, request_subject = _extract_request_subject(window_chunks)
    address = _extract_address(window_chunks)
    parcel = _extract_parcel(window_chunks)
    subject_topic = _derive_subject_topic(
        request_subject=request_subject,
        agenda_item=decision.agenda_item,
        decision_text=decision.decision_text,
    )
    source_chunk_ids = _context_source_chunk_ids(
        citations=citations,
        request_chunk=request_chunk,
        window_chunks=window_chunks,
        address=address,
        parcel=parcel,
    )
    if not request_subject and not subject_topic and not address and not parcel:
        return None

    confidence = 0.4
    if request_subject:
        confidence += 0.25
    if subject_topic:
        confidence += 0.2
    if address:
        confidence += 0.05
    if parcel:
        confidence += 0.1

    return {
        "request_subject_he": request_subject,
        "subject_topic_he": subject_topic,
        "address_he": address,
        "gush": parcel.get("gush"),
        "helka": parcel.get("helka"),
        "migrash": parcel.get("migrash"),
        "source_chunk_ids": source_chunk_ids,
        "confidence": round(min(1.0, confidence), 4),
        "metadata": {
            "anchor_chunk_index": int(anchor_index),
            "window_chunk_ids": [chunk.chunk_id for chunk in window_chunks],
            "request_context_method": "deterministic_neighbor_linking",
        },
    }


def _anchor_chunk_index(*, citations: list[DecisionCitation], chunks: Sequence[TextChunk]) -> int | None:
    if not chunks:
        return None

    chunk_positions = {int(chunk.chunk_index): idx for idx, chunk in enumerate(chunks)}
    best_index: int | None = None
    best_distance: int | None = None
    for citation in citations:
        for idx, chunk in enumerate(chunks):
            if _spans_overlap(
                int(citation.start_offset),
                int(citation.end_offset),
                int(chunk.start_offset),
                int(chunk.end_offset),
            ):
                return idx

            distance = min(
                abs(int(chunk.start_offset) - int(citation.start_offset)),
                abs(int(chunk.end_offset) - int(citation.end_offset)),
            )
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_index = idx

        anchor_label = normalize_for_search(citation.anchor_text or "")
        if anchor_label:
            for idx, chunk in enumerate(chunks):
                if anchor_label and anchor_label[:80] in normalize_for_search(chunk.chunk_text):
                    return idx

    if best_index is not None:
        return best_index
    if citations:
        citation_page = int(citations[0].page_number)
        for chunk in chunks:
            if chunk.start_page == citation_page and int(chunk.chunk_index) in chunk_positions:
                return chunk_positions[int(chunk.chunk_index)]
    return 0


def _window_chunks(*, chunks: Sequence[TextChunk], anchor_index: int, lookback: int, lookahead: int) -> list[TextChunk]:
    start = max(0, anchor_index - lookback)
    end = min(len(chunks), anchor_index + lookahead + 1)
    return list(chunks[start:end])


def _extract_request_subject(window_chunks: list[TextChunk]) -> tuple[TextChunk | None, str | None]:
    best_chunk: TextChunk | None = None
    best_subject: str | None = None
    best_score = float("-inf")
    anchor_position = len(window_chunks) - 1

    for idx, chunk in enumerate(window_chunks):
        text = " ".join(str(chunk.chunk_text or "").split())
        if not text:
            continue

        explicit_match = REQUEST_SUBJECT_RE.search(text)
        if explicit_match is not None:
            subject = _trim_request_subject(explicit_match.group(1))
            if subject:
                score = 10.0 - (anchor_position - idx) * 0.15
                if score > best_score:
                    best_chunk = chunk
                    best_subject = subject
                    best_score = score
            continue

        for pattern in REQUEST_INLINE_PATTERNS:
            match = pattern.search(text)
            if match is None:
                continue
            subject = _trim_request_subject(match.group(1))
            score = _request_subject_candidate_score(subject=subject, text=text, distance=(anchor_position - idx))
            if subject and score > best_score:
                best_chunk = chunk
                best_subject = subject
                best_score = score

    return best_chunk, best_subject


def _request_subject_candidate_score(*, subject: str | None, text: str, distance: int) -> float:
    if not subject:
        return float("-inf")

    subject_norm = normalize_for_search(subject)
    if not subject_norm:
        return float("-inf")
    if any(token in subject_norm for token in {"סעיף לא מזוהה", "נושא כללי"}):
        return float("-inf")

    tokens = [token for token in re.findall(r"[א-ת]+", subject_norm) if len(token) >= 2]
    informative_tokens = [token for token in tokens if token not in SUBJECT_NOISE_TOKENS]
    if len(informative_tokens) < 2:
        return float("-inf")

    score = 3.0
    if any(token in subject_norm for token in {"בקשה", "מבקשת", "מבוקש", "הקצא", "רשות", "הסכם", "ביטול", "החלפת"}):
        score += 1.0
    if len(informative_tokens) >= 4:
        score += 0.5
    if distance >= 0:
        score -= distance * 0.12
    if "מהות הבקשה" in normalize_for_search(text):
        score += 4.0
    return score


def _trim_request_subject(value: str) -> str | None:
    compact = " ".join(str(value or "").split()).strip(" ,;:-")
    if not compact:
        return None
    for marker in REQUEST_STOP_MARKERS:
        marker_norm = normalize_for_search(marker)
        compact_norm = normalize_for_search(compact)
        if marker_norm in compact_norm:
            split_index = compact_norm.find(marker_norm)
            if split_index > 0:
                compact = compact[:split_index].rstrip(" ,;:-")
                break
    compact = compact.rstrip(".")
    compact = re.sub(r"(?:\s|,)+(?:ו|וכן)$", "", compact).strip(" ,;:-")
    if not compact:
        return None
    return compact[:260].rstrip()


def _extract_address(window_chunks: list[TextChunk]) -> str | None:
    for chunk in window_chunks:
        text = " ".join(str(chunk.chunk_text or "").split())
        if not text:
            continue
        match = ADDRESS_RE.search(text)
        if not match:
            continue
        address = match.group(1)
        address = re.split(r"(?:שכונה|גושים\s+וחלקות|חלקה|גוש|שטח)", address, maxsplit=1)[0]
        address = " ".join(address.split()).strip(" ,;:-")
        if address:
            return address[:180]
    return None


def _extract_parcel(window_chunks: list[TextChunk]) -> dict[str, str]:
    text_blob = "\n".join(" ".join(str(chunk.chunk_text or "").split()) for chunk in window_chunks)
    if not text_blob:
        return {}

    out: dict[str, str] = {}
    gush_match = GUSH_RE.search(text_blob) or GUSH_COMPACT_RE.search(text_blob)
    helka_match = HELKA_RE.search(text_blob)
    migrash_match = MIGRASH_RE.search(text_blob)
    if gush_match:
        out["gush"] = str(gush_match.group(1))
    if helka_match:
        out["helka"] = str(helka_match.group(1))
    if migrash_match:
        out["migrash"] = str(migrash_match.group(1))
    return out


def _derive_subject_topic(*, request_subject: str | None, agenda_item: str | None, decision_text: str) -> str | None:
    subject_norm = normalize_for_search(request_subject or "")
    if subject_norm:
        if "החלפה" in subject_norm and "מקלט" in subject_norm:
            return "החלפת הקצאה למקלט"
        if "ביטול" in subject_norm and "הקצא" in subject_norm:
            return "ביטול הקצאה"
        if "כיתות" in subject_norm and "גן" in subject_norm:
            return "הקצאת כיתות גן ילדים"
        if "הקצאת" in subject_norm and "קרקע" in subject_norm:
            return "הקצאת קרקע"
        if "רשות שימוש" in subject_norm:
            return "רשות שימוש במבנה"
        if "הסכם" in subject_norm and "רשות" in subject_norm:
            return "הסכם רשות"
        if "מקלט" in subject_norm and "הקצא" in subject_norm:
            return "הקצאת מקלט"

        compact = _compact_subject_topic(request_subject or "")
        if compact:
            return compact

    agenda_compact = _compact_subject_topic(agenda_item or "")
    if agenda_compact:
        return agenda_compact

    decision_norm = normalize_for_search(decision_text)
    if "ביטול" in decision_norm and "הקצא" in decision_norm:
        return "ביטול הקצאה"
    if "הסכם" in decision_norm and "רשות" in decision_norm:
        return "הסכם רשות"
    if "הקצא" in decision_norm:
        return "הליך הקצאה"
    return None


def _compact_subject_topic(value: str) -> str | None:
    compact = _trim_request_subject(value)
    if not compact:
        return None

    compact = re.sub(r"^בקשת\s+העמותה\s+ל", "", compact)
    compact = re.sub(r"^בקשה\s+ל", "", compact)
    compact = re.sub(r"^עמותת\s+[^\s]+\s+", "", compact)
    compact = " ".join(compact.split()).strip(" ,;:-")
    if not compact:
        return None

    tokens = compact.split(" ")
    compact = " ".join(tokens[:5]).strip()
    if normalize_for_search(compact) in GENERIC_TOPIC_VALUES:
        return None
    return compact


def _context_source_chunk_ids(
    *,
    citations: list[DecisionCitation],
    request_chunk: TextChunk | None,
    window_chunks: list[TextChunk],
    address: str | None,
    parcel: dict[str, str],
) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()

    candidate_ids: list[str] = []
    if request_chunk is not None:
        candidate_ids.append(str(request_chunk.chunk_id))
    if address or parcel:
        for chunk in window_chunks:
            text = normalize_for_search(chunk.chunk_text or "")
            if any(token in text for token in {"כתובת", "גוש", "חלקה", "מגרש"}):
                candidate_ids.append(str(chunk.chunk_id))
    for citation in citations:
        for chunk in window_chunks:
            if _spans_overlap(
                int(citation.start_offset),
                int(citation.end_offset),
                int(chunk.start_offset),
                int(chunk.end_offset),
            ):
                candidate_ids.append(str(chunk.chunk_id))

    for chunk_id in candidate_ids:
        if not chunk_id or chunk_id in seen:
            continue
        seen.add(chunk_id)
        ordered.append(chunk_id)
    return ordered


def _spans_overlap(start_a: int, end_a: int, start_b: int, end_b: int) -> bool:
    return start_a < end_b and start_b < end_a
