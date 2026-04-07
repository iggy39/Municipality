from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from municipality.decision_embeddings import DecisionEmbeddingService
from municipality.extraction import resolve_pages_for_span
from municipality.fallback import BYTEZ_MODEL, BYTEZ_PROVIDER, BytezFallbackClient
from municipality.models import (
    AssetManifest,
    Decision,
    DecisionCitation,
    DecisionExtractionCache,
    DecisionDocumentLink,
    DecisionRequestContext,
    DecisionSemanticLink,
    Document,
    DocumentVersion,
    Meeting,
    MeetingDocumentLink,
    TaxonomyNode,
    Vote,
)


DECISION_SECTION_MARKERS = (
    "סיכום והחלטות",
    "החלטות",
    "נושא ההחלטה",
    "תוכן ההחלטה",
    "תחילת תוקף",
)
DECISION_PHRASES = ("הוחלט", "מחליטים", "מאשרים", "אושר", "החלטה")
UNANIMOUS_PHRASES = ("פה אחד", "אושר פה אחד", "התקבל פה אחד")
DATE_RE = re.compile(r"(\d{1,2})[./](\d{1,2})[./](\d{2,4})")
MEETING_CODE_RE = re.compile(r"(?:מס['\"]?|מספר)\s*([0-9]{1,3}[/-][0-9]{1,4}|[0-9]{1,4})")
NUMBERED_RE = re.compile(r"^(\d{1,3})[.)-]\s*(.+)$")
ALT_NUMBERED_RE = re.compile(r"^\.?\s*(\d{1,3})\s*(.+)$")
DECISION_NUMBER_RE = re.compile(r"החלטה\s*(?:מס['\"]?|מספר)?\s*([0-9]{1,4}[/-]?[0-9]{0,4})")
VOTE_SLASH_RE = re.compile(r"בעד\s*/\s*נגד\s*/\s*נמנע(?:ים)?\s*[:\-]?\s*(\d+)\s*/\s*(\d+)\s*/\s*(\d+)")
VOTE_COUNTS_RE = re.compile(
    r"בעד[^0-9]{0,12}(\d+)[^0-9]{0,12}נגד[^0-9]{0,12}(\d+)(?:[^0-9]{0,12}נמנע(?:ים)?[^0-9]{0,12}(\d+))?"
)
VOTE_EVIDENCE_RE = re.compile(r"(בעד|נגד|נמנע|פה אחד)")
HEADING_RE = re.compile(r"^(סעיף|פרק|נושא|סיכום|החלטות)\b")
WHITESPACE_RE = re.compile(r"\s+")
NON_WORD_RE = re.compile(r"[^\w\u0590-\u05FF]+")
HEBREW_CHAR_RE = re.compile(r"[\u0590-\u05FF]")

DECISION_EXTRACTION_STRATEGY_LOCAL_CACHE_FIRST = "local_cache_first"
DECISION_EXTRACTION_SELECTED_LOCAL = "local_deterministic"
DECISION_EXTRACTION_SELECTED_EXTERNAL = "external_rescue"
DEFAULT_DECISION_CACHE_VERSION = "decision_local_v1"


@dataclass(slots=True)
class DecisionLocalQuality:
    score: float
    marker_count: int
    candidate_count: int
    high_confidence_count: int
    citation_ready_count: int
    reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DecisionExtractionSelection:
    candidates: list[DecisionCandidate]
    strategy: str
    selected_mode: str
    from_cache: bool
    cache_key: str | None
    quality: DecisionLocalQuality | None
    external_attempts: int = 0
    external_status: str = "not_attempted"
    external_reasons: list[str] = field(default_factory=list)
    external_invoked_at: str | None = None
    external_configured: bool = False


@dataclass(slots=True)
class ParsedLine:
    index: int
    text: str
    start: int
    end: int


@dataclass(slots=True)
class DecisionCandidate:
    decision_text: str
    decision_number: str | None
    agenda_item: str | None
    start_offset: int
    end_offset: int
    confidence: float
    source_window: str
    vote_hint: dict | None


class DecisionExtractionService:
    def __init__(
        self,
        session: Session,
        *,
        fallback_client: BytezFallbackClient | None = None,
        low_confidence_threshold: float = 0.75,
        local_acceptance_threshold: float | None = None,
        cache_enabled: bool | None = None,
        external_rescue_enabled: bool | None = None,
    ):
        self.session = session
        self.fallback_client = fallback_client or BytezFallbackClient()
        self.low_confidence_threshold = low_confidence_threshold
        self.local_acceptance_threshold = (
            local_acceptance_threshold
            if local_acceptance_threshold is not None
            else _env_float(os.getenv("DECISION_LOCAL_ACCEPTANCE_THRESHOLD"), default=0.55, min_value=0.0, max_value=1.0)
        )
        self.cache_enabled = _env_bool(os.getenv("DECISION_CACHE_ENABLED"), default=True) if cache_enabled is None else bool(cache_enabled)
        self.external_rescue_enabled = (
            _env_bool(os.getenv("DECISION_EXTERNAL_RESCUE_ENABLED"), default=False)
            if external_rescue_enabled is None
            else bool(external_rescue_enabled)
        )
        self._taxonomy_cache: dict[int, dict[str, TaxonomyNode]] = {}

    def process_document(
        self,
        *,
        document: Document,
        document_version: DocumentVersion,
        extracted_text: str,
        citation_map: list[dict[str, int]],
        source_kind: str,
    ) -> dict[str, Any]:
        meeting_external_id, provenance, title_hint = self._resolve_meeting_context(document)
        meeting_metadata = _extract_meeting_metadata(
            title_he=document.title_he,
            title_hint=title_hint,
            header_text=extracted_text[:1800],
        )
        meeting = self._upsert_meeting(
            source_site_id=document.source_site_id,
            meeting_external_id=meeting_external_id,
            meeting_metadata=meeting_metadata,
        )
        self._upsert_meeting_document_link(
            meeting_id=meeting.id,
            document_id=document.id,
            source_type=source_kind,
            provenance=provenance,
            is_primary=(source_kind == "protocol"),
        )

        if source_kind != "protocol" or not extracted_text.strip():
            self._link_attachment_to_existing_decisions(meeting_id=meeting.id, attachment_document_id=document.id)
            return {"meeting_id": meeting.id, "decisions": 0}

        self._delete_existing_document_decisions(source_document_id=document.id)

        extraction = self._select_decision_candidates(
            document_version_id=document_version.id,
            extracted_text=extracted_text,
            citation_map=citation_map,
        )
        candidates = extraction.candidates

        seen_signatures: set[str] = set()
        inserted = 0
        inserted_decision_ids: list[int] = []
        for candidate in candidates:
            initial_signature = _decision_signature(candidate.decision_text)
            if not initial_signature or initial_signature in seen_signatures:
                continue

            metadata = _default_fallback_metadata()
            metadata.update(_default_api_model_metadata())
            metadata["decision_extraction_strategy"] = extraction.strategy
            metadata["decision_extraction_selected_mode"] = extraction.selected_mode
            metadata["decision_extraction_from_cache"] = extraction.from_cache
            metadata["decision_extraction_cache_key"] = extraction.cache_key
            metadata["decision_extraction_quality_score"] = extraction.quality.score if extraction.quality else None
            metadata["decision_extraction_quality_reasons"] = list(extraction.quality.reasons) if extraction.quality else []
            metadata["api_model_used"] = extraction.selected_mode == DECISION_EXTRACTION_SELECTED_EXTERNAL
            metadata["api_model_provider"] = BYTEZ_PROVIDER if extraction.selected_mode == DECISION_EXTRACTION_SELECTED_EXTERNAL else None
            metadata["api_model_name"] = BYTEZ_MODEL if extraction.selected_mode == DECISION_EXTRACTION_SELECTED_EXTERNAL else None
            metadata["api_model_invoked_at"] = extraction.external_invoked_at
            metadata["api_model_attempts"] = extraction.external_attempts
            metadata["api_model_status"] = extraction.external_status
            metadata["api_model_reasons"] = list(extraction.external_reasons)

            signature = _decision_signature(candidate.decision_text)
            if not signature or signature in seen_signatures:
                continue
            seen_signatures.add(signature)

            vote_data = candidate.vote_hint or _parse_vote(candidate.decision_text)
            row = Decision(
                meeting_id=meeting.id,
                source_document_id=document.id,
                decision_number=candidate.decision_number,
                agenda_item=candidate.agenda_item or "סעיף לא מזוהה",
                decision_text=candidate.decision_text,
                decision_signature_norm=signature,
                parser_confidence=round(candidate.confidence, 4),
                is_public=True,
                metadata_json=json.dumps(metadata, ensure_ascii=False),
                updated_at=datetime.utcnow(),
            )
            self.session.add(row)
            self.session.flush()

            citation_count = self._insert_decision_citations(
                decision_id=row.id,
                document_id=document.id,
                document_version_id=document_version.id,
                source_type=source_kind,
                decision_text=candidate.decision_text,
                start_offset=candidate.start_offset,
                end_offset=candidate.end_offset,
                citation_map=citation_map,
            )
            if citation_count == 0:
                row.is_public = False
                public_meta = json.loads(row.metadata_json or "{}")
                public_meta["public_block_reason"] = "missing_citation"
                row.metadata_json = json.dumps(public_meta, ensure_ascii=False)

            if vote_data is not None:
                self.session.add(
                    Vote(
                        decision_id=row.id,
                        for_count=vote_data.get("for_count"),
                        against_count=vote_data.get("against_count"),
                        abstain_count=vote_data.get("abstain_count"),
                        unanimous=vote_data.get("unanimous"),
                        is_uncertain=bool(vote_data.get("is_uncertain", False)),
                        confidence=float(vote_data.get("confidence", 0.0)),
                        raw_text=vote_data.get("raw_text"),
                        metadata_json=json.dumps(vote_data.get("metadata", {}), ensure_ascii=False),
                    )
                )

            self._upsert_decision_document_link(
                decision_id=row.id,
                document_id=document.id,
                source_type=source_kind,
                provenance="direct",
            )
            self._link_meeting_attachments_to_decision(meeting_id=meeting.id, decision_id=row.id, source_document_id=document.id)
            inserted_decision_ids.append(int(row.id))
            inserted += 1

        if inserted_decision_ids:
            DecisionEmbeddingService(self.session).index_document_decisions(document_id=document.id)

        return {
            "meeting_id": meeting.id,
            "decisions": inserted,
            "decision_extraction_mode": extraction.selected_mode,
            "decision_extraction_from_cache": extraction.from_cache,
        }

    def _select_decision_candidates(
        self,
        *,
        document_version_id: int,
        extracted_text: str,
        citation_map: list[dict[str, int]],
    ) -> DecisionExtractionSelection:
        cache_key = _decision_extraction_cache_key(
            low_confidence_threshold=self.low_confidence_threshold,
            local_acceptance_threshold=self.local_acceptance_threshold,
            external_rescue_enabled=self.external_rescue_enabled,
        )
        if self.cache_enabled:
            cached = self._load_cached_decision_extraction(
                document_version_id=document_version_id,
                cache_key=cache_key,
            )
            if cached is not None:
                return cached

        local_candidates = self._build_local_candidates(extracted_text)
        local_quality = self._evaluate_local_candidates(
            extracted_text=extracted_text,
            candidates=local_candidates,
            citation_map=citation_map,
        )

        selected_mode = DECISION_EXTRACTION_SELECTED_LOCAL
        candidates = local_candidates
        api_result = {
            "configured": self.fallback_client.is_configured(),
            "invoked_at": None,
            "attempts": 0,
            "status": "skipped_local_cache_first",
            "reasons": list(local_quality.reasons),
            "candidates": [],
        }

        should_try_external = bool(
            self.external_rescue_enabled
            and self.fallback_client.is_configured()
            and (
                not local_candidates or local_quality.score < self.local_acceptance_threshold
            )
        )
        if should_try_external:
            api_result = self._extract_api_model_candidates(
                extracted_text=extracted_text,
                deterministic_candidates=local_candidates,
            )
            if api_result["candidates"]:
                candidates = api_result["candidates"]
                selected_mode = DECISION_EXTRACTION_SELECTED_EXTERNAL

        selection = DecisionExtractionSelection(
            candidates=candidates,
            strategy=DECISION_EXTRACTION_STRATEGY_LOCAL_CACHE_FIRST,
            selected_mode=selected_mode,
            from_cache=False,
            cache_key=cache_key,
            quality=local_quality,
            external_attempts=int(api_result.get("attempts", 0) or 0),
            external_status=str(api_result.get("status") or "not_attempted"),
            external_reasons=[str(reason) for reason in api_result.get("reasons") or []],
            external_invoked_at=api_result.get("invoked_at"),
            external_configured=bool(api_result.get("configured")),
        )
        if self.cache_enabled:
            self._store_decision_extraction_cache(
                document_version_id=document_version_id,
                selection=selection,
            )
        return selection

    def _load_cached_decision_extraction(
        self,
        *,
        document_version_id: int,
        cache_key: str,
    ) -> DecisionExtractionSelection | None:
        row = self.session.execute(
            select(DecisionExtractionCache).where(
                DecisionExtractionCache.document_version_id == document_version_id,
                DecisionExtractionCache.cache_key == cache_key,
            )
        ).scalar_one_or_none()
        if row is None or row.status != "completed":
            return None

        candidates_payload = _loads_json_list_of_dicts(row.candidates_json)
        candidates = [_candidate_from_cache_payload(item) for item in candidates_payload]
        quality_payload = _loads_json_dict(row.quality_reasons_json)
        quality = None
        if quality_payload:
            quality = DecisionLocalQuality(
                score=float(quality_payload.get("score") or 0.0),
                marker_count=int(quality_payload.get("marker_count") or 0),
                candidate_count=int(quality_payload.get("candidate_count") or 0),
                high_confidence_count=int(quality_payload.get("high_confidence_count") or 0),
                citation_ready_count=int(quality_payload.get("citation_ready_count") or 0),
                reasons=[str(reason) for reason in quality_payload.get("reasons") or []],
            )
        metadata = _loads_json_dict(row.metadata_json)
        return DecisionExtractionSelection(
            candidates=candidates,
            strategy=row.strategy,
            selected_mode=row.selected_mode,
            from_cache=True,
            cache_key=row.cache_key,
            quality=quality,
            external_attempts=int(metadata.get("external_attempts") or 0),
            external_status=str(metadata.get("external_status") or row.selected_mode),
            external_reasons=[str(reason) for reason in metadata.get("external_reasons") or []],
            external_invoked_at=metadata.get("external_invoked_at"),
            external_configured=bool(metadata.get("external_configured")),
        )

    def _store_decision_extraction_cache(
        self,
        *,
        document_version_id: int,
        selection: DecisionExtractionSelection,
    ) -> None:
        if not selection.cache_key:
            return
        row = self.session.execute(
            select(DecisionExtractionCache).where(
                DecisionExtractionCache.document_version_id == document_version_id,
                DecisionExtractionCache.cache_key == selection.cache_key,
            )
        ).scalar_one_or_none()
        now = datetime.utcnow()
        if row is None:
            row = DecisionExtractionCache(
                document_version_id=document_version_id,
                cache_key=selection.cache_key,
                strategy=selection.strategy,
                selected_mode=selection.selected_mode,
                status="completed",
                created_at=now,
                updated_at=now,
            )
            self.session.add(row)
        row.strategy = selection.strategy
        row.selected_mode = selection.selected_mode
        row.status = "completed"
        row.candidate_count = len(selection.candidates)
        row.quality_score = selection.quality.score if selection.quality else None
        row.quality_reasons_json = json.dumps(_quality_to_payload(selection.quality), ensure_ascii=False)
        row.candidates_json = json.dumps([_candidate_to_cache_payload(item) for item in selection.candidates], ensure_ascii=False)
        row.metadata_json = json.dumps(
            {
                "external_attempts": selection.external_attempts,
                "external_status": selection.external_status,
                "external_reasons": list(selection.external_reasons),
                "external_invoked_at": selection.external_invoked_at,
                "external_configured": selection.external_configured,
            },
            ensure_ascii=False,
        )
        row.updated_at = now
        self.session.flush()

    def _build_local_candidates(self, extracted_text: str) -> list[DecisionCandidate]:
        parsed = _parse_decision_candidates(extracted_text)
        expanded: list[DecisionCandidate] = []
        for candidate in parsed:
            expanded.extend(_split_candidate_on_multi_approvals(candidate, extracted_text))
        deduped = _dedupe_candidates(expanded)
        return _merge_adjacent_candidates(deduped, extracted_text)

    def _evaluate_local_candidates(
        self,
        *,
        extracted_text: str,
        candidates: list[DecisionCandidate],
        citation_map: list[dict[str, int]],
    ) -> DecisionLocalQuality:
        marker_count = _count_decision_marker_lines(extracted_text)
        candidate_count = len(candidates)
        if candidate_count <= 0:
            return DecisionLocalQuality(
                score=0.0,
                marker_count=marker_count,
                candidate_count=0,
                high_confidence_count=0,
                citation_ready_count=0,
                reasons=["no_local_candidates"],
            )

        high_confidence_count = sum(1 for candidate in candidates if candidate.confidence >= self.low_confidence_threshold)
        citation_ready_count = sum(
            1
            for candidate in candidates
            if resolve_pages_for_span(citation_map, candidate.start_offset, candidate.end_offset)
            or _nearest_page(citation_map, candidate.start_offset) is not None
        )
        marker_coverage = min(1.0, candidate_count / max(marker_count, 1)) if marker_count else 1.0
        high_confidence_ratio = high_confidence_count / max(candidate_count, 1)
        citation_ready_ratio = citation_ready_count / max(candidate_count, 1)
        score = round(
            max(
                0.0,
                min(
                    1.0,
                    (0.45 * marker_coverage)
                    + (0.35 * high_confidence_ratio)
                    + (0.20 * citation_ready_ratio),
                ),
            ),
            4,
        )
        reasons: list[str] = []
        if marker_count and candidate_count < marker_count:
            reasons.append("local_marker_undercoverage")
        if high_confidence_count == 0:
            reasons.append("local_no_high_confidence_candidates")
        if citation_ready_count < candidate_count:
            reasons.append("local_missing_citation_ready_candidates")
        if score >= self.local_acceptance_threshold:
            reasons.append("local_quality_accepted")
        else:
            reasons.append("local_quality_below_threshold")
        return DecisionLocalQuality(
            score=score,
            marker_count=marker_count,
            candidate_count=candidate_count,
            high_confidence_count=high_confidence_count,
            citation_ready_count=citation_ready_count,
            reasons=_compact_reason_list(reasons, max_items=6),
        )

    def _extract_api_model_candidates(
        self,
        *,
        extracted_text: str,
        deterministic_candidates: list[DecisionCandidate],
    ) -> dict:
        if not self.fallback_client.is_configured():
            return {
                "configured": False,
                "invoked_at": None,
                "attempts": 0,
                "status": "not_configured",
                "reasons": ["api_model_no_api_key"],
                "candidates": [],
            }

        invoked_at = datetime.utcnow().isoformat()
        attempts = 0
        reasons: list[str] = []
        model_candidates: list[DecisionCandidate] = []
        windows = _build_api_windows(extracted_text)

        for idx, (start, end, window_text) in enumerate(windows, start=1):
            attempts += 1
            payloads = self.fallback_client.extract_decisions(source_text=window_text, text_offset=start)
            if not payloads:
                reasons.append(f"window_{idx}_empty_response")
                continue

            accepted_in_window = 0
            for payload in payloads:
                candidate, candidate_reasons = _candidate_from_model_payload(
                    payload=payload,
                    full_text=extracted_text,
                    window_start=start,
                    window_end=end,
                )
                if candidate is None:
                    reasons.extend(candidate_reasons)
                    continue
                expanded = _split_candidate_on_multi_approvals(candidate, extracted_text)
                for item in expanded:
                    model_candidates.append(item)
                    accepted_in_window += 1
            if accepted_in_window == 0:
                reasons.append(f"window_{idx}_no_valid_decisions")

        deduped: list[DecisionCandidate] = []
        seen: set[str] = set()
        for candidate in model_candidates:
            sig = _decision_signature(candidate.decision_text)
            if not sig or sig in seen:
                continue
            seen.add(sig)
            deduped.append(candidate)

        if len(deduped) < len(deterministic_candidates):
            for deterministic in deterministic_candidates:
                if _is_candidate_covered_by_existing(deterministic, deduped):
                    continue
                focused_start = max(0, deterministic.start_offset - 120)
                focused_end = min(len(extracted_text), deterministic.end_offset + 120)
                focused_text = extracted_text[focused_start:focused_end]
                attempts += 1
                payloads = self.fallback_client.extract_decisions(source_text=focused_text, text_offset=focused_start)
                if not payloads:
                    reasons.append("focused_window_empty_response")
                    continue
                for payload in payloads:
                    candidate, candidate_reasons = _candidate_from_model_payload(
                        payload=payload,
                        full_text=extracted_text,
                        window_start=focused_start,
                        window_end=focused_end,
                    )
                    if candidate is None:
                        reasons.extend(candidate_reasons)
                        continue
                    expanded = _split_candidate_on_multi_approvals(candidate, extracted_text)
                    for item in expanded:
                        sig = _decision_signature(item.decision_text)
                        if not sig or sig in seen:
                            continue
                        seen.add(sig)
                        deduped.append(item)

        deduped = _merge_adjacent_candidates(deduped, extracted_text)

        if deduped:
            return {
                "configured": True,
                "invoked_at": invoked_at,
                "attempts": attempts,
                "status": "accepted",
                "reasons": [],
                "candidates": deduped,
            }

        compact_reasons = _compact_reason_list(reasons)
        if not compact_reasons:
            compact_reasons = ["api_model_no_valid_decisions"]
        return {
            "configured": True,
            "invoked_at": invoked_at,
            "attempts": attempts,
            "status": "rejected",
            "reasons": compact_reasons,
            "candidates": [],
        }

    def _delete_existing_document_decisions(self, *, source_document_id: int) -> None:
        existing_ids = self.session.execute(
            select(Decision.id).where(Decision.source_document_id == source_document_id)
        ).scalars().all()
        if not existing_ids:
            return

        self.session.query(Vote).filter(Vote.decision_id.in_(existing_ids)).delete(synchronize_session=False)
        self.session.query(DecisionCitation).filter(DecisionCitation.decision_id.in_(existing_ids)).delete(synchronize_session=False)
        self.session.query(DecisionDocumentLink).filter(DecisionDocumentLink.decision_id.in_(existing_ids)).delete(
            synchronize_session=False
        )
        self.session.query(DecisionRequestContext).filter(DecisionRequestContext.decision_id.in_(existing_ids)).delete(
            synchronize_session=False
        )
        self.session.query(DecisionSemanticLink).filter(DecisionSemanticLink.decision_id.in_(existing_ids)).delete(
            synchronize_session=False
        )
        self.session.query(Decision).filter(Decision.id.in_(existing_ids)).delete(synchronize_session=False)

    def _resolve_meeting_context(self, document: Document) -> tuple[str, str, str | None]:
        manifest = self.session.execute(
            select(AssetManifest).where(
                AssetManifest.source_site_id == document.source_site_id,
                AssetManifest.asset_external_id == document.document_external_id,
            )
        ).scalar_one_or_none()
        if manifest is None:
            return f"doc:{document.id}", "heuristic", document.title_he

        source_external_id = manifest.source_node_external_id
        nodes = self._get_taxonomy_cache(document.source_site_id)
        node = nodes.get(source_external_id)
        if node is None:
            return source_external_id, "heuristic", document.title_he
        if node.node_type == "meeting_folder":
            return node.node_external_id, "direct", node.title_he

        current = node
        while current.parent_external_id:
            parent = nodes.get(current.parent_external_id)
            if parent is None:
                break
            if parent.node_type == "meeting_folder":
                return parent.node_external_id, "descendant", parent.title_he
            current = parent
        return source_external_id, "heuristic", node.title_he

    def _get_taxonomy_cache(self, source_site_id: int) -> dict[str, TaxonomyNode]:
        cached = self._taxonomy_cache.get(source_site_id)
        if cached is not None:
            return cached
        rows = self.session.execute(
            select(TaxonomyNode).where(TaxonomyNode.source_site_id == source_site_id)
        ).scalars().all()
        mapped = {row.node_external_id: row for row in rows}
        self._taxonomy_cache[source_site_id] = mapped
        return mapped

    def _upsert_meeting(
        self,
        *,
        source_site_id: int,
        meeting_external_id: str,
        meeting_metadata: dict,
    ) -> Meeting:
        existing = self.session.execute(
            select(Meeting).where(
                Meeting.source_site_id == source_site_id,
                Meeting.meeting_external_id == meeting_external_id,
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = Meeting(
                source_site_id=source_site_id,
                meeting_external_id=meeting_external_id,
                title_he=meeting_metadata["title_he"],
            )
            self.session.add(existing)

        existing.title_he = meeting_metadata["title_he"]
        existing.committee_name = meeting_metadata.get("committee_name")
        existing.meeting_kind = meeting_metadata.get("meeting_kind")
        existing.meeting_code = meeting_metadata.get("meeting_code")
        existing.meeting_date = meeting_metadata.get("meeting_date")
        existing.parse_confidence = meeting_metadata.get("parse_confidence")
        existing.metadata_json = json.dumps(meeting_metadata.get("metadata", {}), ensure_ascii=False)
        existing.updated_at = datetime.utcnow()
        self.session.flush()
        return existing

    def _upsert_meeting_document_link(
        self,
        *,
        meeting_id: int,
        document_id: int,
        source_type: str,
        provenance: str,
        is_primary: bool,
    ) -> None:
        existing = self.session.execute(
            select(MeetingDocumentLink).where(
                MeetingDocumentLink.meeting_id == meeting_id,
                MeetingDocumentLink.document_id == document_id,
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = MeetingDocumentLink(
                meeting_id=meeting_id,
                document_id=document_id,
                source_type=source_type,
                provenance=provenance,
                is_primary=is_primary,
                metadata_json=json.dumps({"linked_at": datetime.utcnow().isoformat()}),
            )
            self.session.add(existing)
            return
        existing.source_type = source_type
        existing.provenance = provenance
        existing.is_primary = bool(existing.is_primary or is_primary)

    def _insert_decision_citations(
        self,
        *,
        decision_id: int,
        document_id: int,
        document_version_id: int,
        source_type: str,
        decision_text: str,
        start_offset: int,
        end_offset: int,
        citation_map: list[dict[str, int]],
    ) -> int:
        pages = resolve_pages_for_span(citation_map, start_offset, end_offset)
        if not pages:
            page = _nearest_page(citation_map, start_offset)
            pages = [page] if page is not None else []

        for page in pages:
            label = f"p.{page}#{start_offset}-{end_offset}"
            metadata = {
                "anchor_id": f"doc-{document_id}-p{page}-o{start_offset}",
                "deep_link": f"#page={page}&offset={start_offset}",
            }
            self.session.add(
                DecisionCitation(
                    decision_id=decision_id,
                    document_id=document_id,
                    document_version_id=document_version_id,
                    source_type=source_type,
                    page_number=page,
                    start_offset=start_offset,
                    end_offset=end_offset,
                    anchor_label=label,
                    anchor_text=decision_text[:280],
                    metadata_json=json.dumps(metadata, ensure_ascii=False),
                )
            )
        return len(pages)

    def _upsert_decision_document_link(
        self,
        *,
        decision_id: int,
        document_id: int,
        source_type: str,
        provenance: str,
    ) -> None:
        existing = self.session.execute(
            select(DecisionDocumentLink).where(
                DecisionDocumentLink.decision_id == decision_id,
                DecisionDocumentLink.document_id == document_id,
            )
        ).scalar_one_or_none()
        if existing is None:
            self.session.add(
                DecisionDocumentLink(
                    decision_id=decision_id,
                    document_id=document_id,
                    source_type=source_type,
                    provenance=provenance,
                    metadata_json=json.dumps({"linked_at": datetime.utcnow().isoformat()}),
                )
            )
            return
        existing.source_type = source_type
        existing.provenance = provenance

    def _link_meeting_attachments_to_decision(self, *, meeting_id: int, decision_id: int, source_document_id: int) -> None:
        links = self.session.execute(
            select(MeetingDocumentLink).where(
                MeetingDocumentLink.meeting_id == meeting_id,
                MeetingDocumentLink.document_id != source_document_id,
                MeetingDocumentLink.source_type == "attachment",
            )
        ).scalars().all()
        for link in links:
            self._upsert_decision_document_link(
                decision_id=decision_id,
                document_id=link.document_id,
                source_type=link.source_type,
                provenance="heuristic",
            )

    def _link_attachment_to_existing_decisions(self, *, meeting_id: int, attachment_document_id: int) -> None:
        decisions = self.session.execute(
            select(Decision.id).where(Decision.meeting_id == meeting_id)
        ).scalars().all()
        for decision_id in decisions:
            self._upsert_decision_document_link(
                decision_id=decision_id,
                document_id=attachment_document_id,
                source_type="attachment",
                provenance="heuristic",
            )


def _build_api_windows(text: str, *, max_chars: int = 12000, overlap_chars: int = 1000) -> list[tuple[int, int, str]]:
    compact = text.strip()
    if not compact:
        return []
    if len(text) <= max_chars:
        return [(0, len(text), text)]

    windows: list[tuple[int, int, str]] = []
    cursor = 0
    text_len = len(text)
    while cursor < text_len:
        end = min(text_len, cursor + max_chars)
        if end < text_len:
            boundary = text.rfind("\n", cursor + int(max_chars * 0.6), end)
            if boundary > cursor:
                end = boundary
        window = text[cursor:end]
        windows.append((cursor, end, window))
        if end >= text_len:
            break
        cursor = max(0, end - overlap_chars)
    return windows


def _candidate_from_model_payload(
    *,
    payload: dict,
    full_text: str,
    window_start: int,
    window_end: int,
) -> tuple[DecisionCandidate | None, list[str]]:
    reasons: list[str] = []
    decision_text = payload.get("decision_text")
    if not isinstance(decision_text, str) or not decision_text.strip():
        return None, ["api_model_missing_decision_text"]

    start_offset = payload.get("start_offset")
    end_offset = payload.get("end_offset")
    span: tuple[int, int] | None = None
    used_offset_fallback = False
    located = _locate_decision_span_near_window(
        full_text=full_text,
        candidate_text=decision_text,
        window_start=window_start,
        window_end=window_end,
    )
    if located is not None:
        span = located
    elif isinstance(start_offset, int) and isinstance(end_offset, int) and 0 <= start_offset < end_offset <= len(full_text):
        span = (start_offset, end_offset)
        used_offset_fallback = True
    else:
        reasons.append("api_model_span_unresolved")
        return None, reasons

    start, end = span
    if used_offset_fallback or _span_has_midword_boundary(full_text, start, end):
        start, end = _expand_span_to_sentence_bounds(full_text, start, end)
    start, end = _normalize_connector_leading_span(full_text, start, end)
    source_span = full_text[start:end]
    if not source_span.strip():
        return None, ["api_model_empty_source_span"]

    if _is_background_department_approval(source_span):
        return None, ["api_model_background_statement"]

    if _should_expand_with_following_clause(full_text, start, end, source_span):
        end = _expand_span_right_to_sentence_end(full_text, start, end)
        source_span = full_text[start:end]

    if used_offset_fallback:
        overlap = _token_overlap_ratio(_normalized_text(source_span), _normalized_text(decision_text))
        if overlap < 0.8:
            return None, ["api_model_offset_mismatch"]
        if start > 0 and full_text[start - 1].isalnum() and full_text[start].isalnum():
            return None, ["api_model_offset_midword_start"]
        if end < len(full_text) and full_text[end - 1].isalnum() and full_text[end].isalnum():
            return None, ["api_model_offset_midword_end"]

    agenda_item = payload.get("agenda_item")
    if not isinstance(agenda_item, str) or not agenda_item.strip():
        inferred_agenda, _ = _extract_agenda_item_from_line(source_span)
        agenda_item = inferred_agenda

    decision_number = payload.get("decision_number")
    if not isinstance(decision_number, str) or not decision_number.strip():
        decision_number = _extract_decision_number(source_span)
    elif not _is_plausible_decision_number(decision_number, source_span):
        decision_number = _extract_decision_number(source_span)

    confidence_raw = payload.get("confidence")
    confidence = float(confidence_raw) if isinstance(confidence_raw, (int, float)) else 0.95
    vote_hint = _normalize_api_vote_hint(payload.get("vote"))

    context_start = max(0, start - 220)
    context_end = min(len(full_text), end + 220)
    return (
        DecisionCandidate(
            decision_text=source_span.strip(),
            decision_number=decision_number.strip() if isinstance(decision_number, str) else None,
            agenda_item=agenda_item.strip() if isinstance(agenda_item, str) else None,
            start_offset=start,
            end_offset=end,
            confidence=min(1.0, max(0.0, confidence)),
            source_window=full_text[context_start:context_end],
            vote_hint=vote_hint,
        ),
        reasons,
    )


def _locate_decision_span_near_window(
    *,
    full_text: str,
    candidate_text: str,
    window_start: int,
    window_end: int,
) -> tuple[int, int] | None:
    target = candidate_text.strip()
    if not target:
        return None

    search_start = max(0, window_start - 600)
    search_end = min(len(full_text), window_end + 600)
    search_space = full_text[search_start:search_end]
    direct_idx = search_space.find(target)
    if direct_idx >= 0:
        start = search_start + direct_idx
        return start, start + len(target)

    normalized_target = _normalized_text(target)
    best: tuple[int, int, float] | None = None
    for match in re.finditer(r"\S[\s\S]{20,400}", search_space):
        snippet = match.group(0)
        overlap = _token_overlap_ratio(_normalized_text(snippet), normalized_target)
        if overlap < 0.7:
            continue
        candidate_start = search_start + match.start()
        candidate_end = search_start + match.end()
        if best is None or overlap > best[2]:
            best = (candidate_start, candidate_end, overlap)
    if best is None:
        return None
    return best[0], best[1]


def _expand_span_to_sentence_bounds(full_text: str, start: int, end: int) -> tuple[int, int]:
    if not (0 <= start < end <= len(full_text)):
        return start, end

    left_limit = max(0, start - 160)
    while start > left_limit:
        prev_char = full_text[start - 1]
        if prev_char in "\n.:;!?":
            break
        start -= 1

    right_limit = min(len(full_text), end + 400)
    while end < right_limit:
        if full_text.startswith("________________", end):
            break
        ch = full_text[end]
        end += 1
        if ch in ".!?":
            break

    while start < end and full_text[start].isspace():
        start += 1
    while end > start and full_text[end - 1].isspace():
        end -= 1

    if start >= end:
        return max(0, start - 1), max(start, end)
    return start, end


def _expand_span_right_to_sentence_end(full_text: str, start: int, end: int) -> int:
    if not (0 <= start < end <= len(full_text)):
        return end
    right_limit = min(len(full_text), end + 400)
    cursor = end
    while cursor < right_limit:
        if full_text.startswith("________________", cursor):
            break
        ch = full_text[cursor]
        cursor += 1
        if ch in ".!?":
            break
    while cursor > start and full_text[cursor - 1].isspace():
        cursor -= 1
    return max(cursor, end)


def _span_has_midword_boundary(full_text: str, start: int, end: int) -> bool:
    if 0 < start < len(full_text):
        if full_text[start - 1].isalnum() and full_text[start].isalnum():
            return True
    if 0 < end < len(full_text):
        if full_text[end - 1].isalnum() and full_text[end].isalnum():
            return True
    return False


def _should_expand_with_following_clause(full_text: str, start: int, end: int, source_span: str) -> bool:
    compact = source_span.rstrip()
    if not compact:
        return False
    if compact[-1] in ".!?":
        return False
    if end >= len(full_text):
        return False

    tail = full_text[end : min(len(full_text), end + 24)]
    newline_pos = tail.find("\n")
    non_space_pos = None
    for idx, ch in enumerate(tail):
        if ch.isspace():
            continue
        non_space_pos = idx
        break
    if non_space_pos is None:
        return False
    if newline_pos >= 0 and newline_pos < non_space_pos:
        return False
    next_char = tail[non_space_pos]
    return next_char in {",", ";", ":"}


def _normalize_connector_leading_span(full_text: str, start: int, end: int) -> tuple[int, int]:
    if not (0 <= start < end <= len(full_text)):
        return start, end
    source_span = full_text[start:end]
    leading = source_span[:90]
    match = re.search(r"(?:כמו\s+כן\s+מאשרים|וכן\s+מאשרים|בנוסף\s+מאשרים)", leading)
    if match is None or match.start() <= 0:
        return start, end
    marker_text = match.group(0)
    verb_idx = marker_text.rfind("מאשרים")
    if verb_idx < 0:
        return start, end
    new_start = start + match.start() + verb_idx
    if new_start >= end:
        return start, end
    return new_start, end


def _normalize_api_vote_hint(value: dict | None) -> dict | None:
    if not isinstance(value, dict):
        return None
    return {
        "for_count": value.get("for_count") if isinstance(value.get("for_count"), int) else None,
        "against_count": value.get("against_count") if isinstance(value.get("against_count"), int) else None,
        "abstain_count": value.get("abstain_count") if isinstance(value.get("abstain_count"), int) else None,
        "unanimous": value.get("unanimous") if isinstance(value.get("unanimous"), bool) else None,
        "is_uncertain": False,
        "confidence": 0.95,
        "raw_text": None,
        "metadata": {"pattern": "api_model"},
    }


def _decision_extraction_cache_key(
    *,
    low_confidence_threshold: float,
    local_acceptance_threshold: float,
    external_rescue_enabled: bool,
) -> str:
    payload = (
        f"{DEFAULT_DECISION_CACHE_VERSION}|{DECISION_EXTRACTION_STRATEGY_LOCAL_CACHE_FIRST}|"
        f"low_conf={low_confidence_threshold:.4f}|local_accept={local_acceptance_threshold:.4f}|"
        f"external_rescue={1 if external_rescue_enabled else 0}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _count_decision_marker_lines(text: str) -> int:
    count = 0
    for line in text.split("\n"):
        compact = WHITESPACE_RE.sub(" ", line).strip()
        if not compact:
            continue
        if any(phrase in compact for phrase in DECISION_PHRASES):
            count += 1
            continue
        if NUMBERED_RE.match(compact) or ALT_NUMBERED_RE.match(compact):
            count += 1
    return count


def _dedupe_candidates(candidates: list[DecisionCandidate]) -> list[DecisionCandidate]:
    deduped: list[DecisionCandidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        signature = _decision_signature(candidate.decision_text)
        if not signature or signature in seen:
            continue
        seen.add(signature)
        deduped.append(candidate)
    return deduped


def _candidate_to_cache_payload(candidate: DecisionCandidate) -> dict[str, Any]:
    return {
        "decision_text": candidate.decision_text,
        "decision_number": candidate.decision_number,
        "agenda_item": candidate.agenda_item,
        "start_offset": candidate.start_offset,
        "end_offset": candidate.end_offset,
        "confidence": candidate.confidence,
        "source_window": candidate.source_window,
        "vote_hint": candidate.vote_hint,
    }


def _candidate_from_cache_payload(payload: dict[str, Any]) -> DecisionCandidate:
    return DecisionCandidate(
        decision_text=str(payload.get("decision_text") or "").strip(),
        decision_number=_as_optional_str(payload.get("decision_number")),
        agenda_item=_as_optional_str(payload.get("agenda_item")),
        start_offset=int(payload.get("start_offset") or 0),
        end_offset=int(payload.get("end_offset") or 0),
        confidence=float(payload.get("confidence") or 0.0),
        source_window=str(payload.get("source_window") or ""),
        vote_hint=payload.get("vote_hint") if isinstance(payload.get("vote_hint"), dict) else None,
    )


def _quality_to_payload(quality: DecisionLocalQuality | None) -> dict[str, Any]:
    if quality is None:
        return {}
    return {
        "score": quality.score,
        "marker_count": quality.marker_count,
        "candidate_count": quality.candidate_count,
        "high_confidence_count": quality.high_confidence_count,
        "citation_ready_count": quality.citation_ready_count,
        "reasons": list(quality.reasons),
    }


def _loads_json_dict(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _loads_json_list_of_dicts(value: str | None) -> list[dict[str, Any]]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def _as_optional_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    compact = value.strip()
    return compact or None


def _env_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _env_float(value: str | None, *, default: float, min_value: float, max_value: float) -> float:
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    return max(min_value, min(max_value, parsed))


def _compact_reason_list(reasons: list[str], *, max_items: int = 8) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for reason in reasons:
        compact = reason.strip()
        if not compact or compact in seen:
            continue
        seen.add(compact)
        out.append(compact)
        if len(out) >= max_items:
            break
    return out


def _is_candidate_covered_by_existing(candidate: DecisionCandidate, existing: list[DecisionCandidate]) -> bool:
    candidate_sig = _decision_signature(candidate.decision_text)
    for item in existing:
        item_sig = _decision_signature(item.decision_text)
        if candidate_sig and item_sig and (candidate_sig in item_sig or item_sig in candidate_sig):
            return True
        overlap = _span_overlap(candidate.start_offset, candidate.end_offset, item.start_offset, item.end_offset)
        if overlap >= 0.5:
            return True
    return False


def _span_overlap(start_a: int, end_a: int, start_b: int, end_b: int) -> float:
    inter_start = max(start_a, start_b)
    inter_end = min(end_a, end_b)
    if inter_end <= inter_start:
        return 0.0
    intersection = inter_end - inter_start
    base = max(min(end_a - start_a, end_b - start_b), 1)
    return intersection / base


def _merge_adjacent_candidates(candidates: list[DecisionCandidate], full_text: str) -> list[DecisionCandidate]:
    if len(candidates) <= 1:
        return candidates
    ordered = sorted(candidates, key=lambda item: (item.start_offset, item.end_offset))
    merged: list[DecisionCandidate] = []
    current = ordered[0]

    for nxt in ordered[1:]:
        should_merge = False
        if nxt.start_offset <= current.end_offset:
            overlap = _span_overlap(current.start_offset, current.end_offset, nxt.start_offset, nxt.end_offset)
            left_sig = _decision_signature(current.decision_text)
            right_sig = _decision_signature(nxt.decision_text)
            is_subsumed = bool(left_sig and right_sig and (left_sig in right_sig or right_sig in left_sig))
            has_connector = any(keyword in nxt.decision_text for keyword in ("כמו כן", "וכן", "בנוסף"))
            if overlap >= 0.85 and is_subsumed and not has_connector:
                should_merge = True
        else:
            separator = full_text[current.end_offset : nxt.start_offset]
            if separator and len(separator) <= 60:
                separator_compact = WHITESPACE_RE.sub(" ", separator)
                has_split_connector = any(keyword in separator_compact for keyword in ("כמו כן", "וכן", "בנוסף"))
                if (
                    "\n" not in separator
                    and "." not in separator
                    and "החלטה" not in separator
                    and not has_split_connector
                ):
                    should_merge = True

        if not should_merge:
            merged.append(current)
            current = nxt
            continue

        start = min(current.start_offset, nxt.start_offset)
        end = max(current.end_offset, nxt.end_offset)
        span_text = full_text[start:end].strip()
        context_start = max(0, start - 220)
        context_end = min(len(full_text), end + 220)
        current = DecisionCandidate(
            decision_text=span_text,
            decision_number=current.decision_number or nxt.decision_number,
            agenda_item=current.agenda_item or nxt.agenda_item,
            start_offset=start,
            end_offset=end,
            confidence=max(current.confidence, nxt.confidence),
            source_window=full_text[context_start:context_end],
            vote_hint=current.vote_hint or nxt.vote_hint,
        )

    merged.append(current)
    return merged


def _split_candidate_on_multi_approvals(candidate: DecisionCandidate, full_text: str) -> list[DecisionCandidate]:
    span_text = full_text[candidate.start_offset : candidate.end_offset]
    if not span_text.strip():
        return [candidate]

    markers = (
        "כמו כן מאשרים",
        "וכן מאשרים",
        "בנוסף מאשרים",
    )
    split_at: int | None = None
    skip_prefix = 0
    for marker in markers:
        idx = span_text.find(marker)
        if idx > 24:
            split_at = idx
            if marker.startswith("כמו כן"):
                skip_prefix = len("כמו כן ")
            break
    if split_at is None:
        return [candidate]

    first_start = candidate.start_offset
    first_end = candidate.start_offset + split_at
    second_start = candidate.start_offset + split_at + skip_prefix
    second_end = candidate.end_offset

    first = _candidate_from_span(candidate, full_text, first_start, first_end)
    second = _candidate_from_span(candidate, full_text, second_start, second_end)
    out = [item for item in (first, second) if item is not None]
    return out or [candidate]


def _candidate_from_span(
    base: DecisionCandidate,
    full_text: str,
    start: int,
    end: int,
) -> DecisionCandidate | None:
    if not (0 <= start < end <= len(full_text)):
        return None
    text = full_text[start:end].strip()
    if len(text) < 16:
        return None
    context_start = max(0, start - 220)
    context_end = min(len(full_text), end + 220)
    return DecisionCandidate(
        decision_text=text,
        decision_number=base.decision_number,
        agenda_item=base.agenda_item,
        start_offset=start,
        end_offset=end,
        confidence=base.confidence,
        source_window=full_text[context_start:context_end],
        vote_hint=base.vote_hint,
    )


def _is_background_department_approval(source_span: str) -> bool:
    compact = WHITESPACE_RE.sub(" ", source_span).strip()
    if not compact:
        return False
    if "אושרה" in compact and "ע" in compact and "י" in compact:
        if 'ע"י' in compact or "ע'י" in compact or "ע״י" in compact:
            has_operative_keyword = any(keyword in compact for keyword in ("מאשרים", "הוחלט", "החלטה:"))
            if not has_operative_keyword:
                return True
    return False


def _is_plausible_decision_number(number: str, source_span: str) -> bool:
    compact = number.strip()
    if not compact:
        return False
    if compact not in source_span:
        return False
    if "החלטה" not in source_span:
        return False
    return True


def _extract_meeting_metadata(*, title_he: str, title_hint: str | None, header_text: str) -> dict:
    basis = " ".join([title_he or "", title_hint or "", header_text[:300]]).strip()
    meeting_date = _extract_meeting_date(basis)
    meeting_kind = "רגילה" if "רגילה" in basis else "מיוחדת" if "מיוחדת" in basis else None
    meeting_code = _extract_meeting_code(basis)
    committee_name = _extract_committee_name(basis)

    found = sum(1 for value in (meeting_date, meeting_kind, meeting_code, committee_name) if value)
    confidence = round(min(1.0, 0.4 + (found * 0.15) + (0.1 if "מועצה" in basis else 0.0)), 4)
    metadata = {
        "parse_signals": {
            "date_detected": bool(meeting_date),
            "meeting_kind_detected": bool(meeting_kind),
            "meeting_code_detected": bool(meeting_code),
            "committee_detected": bool(committee_name),
        }
    }
    return {
        "title_he": (title_hint or title_he or "ישיבה ללא כותרת").strip(),
        "meeting_date": meeting_date,
        "meeting_kind": meeting_kind,
        "meeting_code": meeting_code,
        "committee_name": committee_name,
        "parse_confidence": confidence,
        "metadata": metadata,
    }


def _extract_meeting_date(value: str) -> str | None:
    match = DATE_RE.search(value)
    if not match:
        return None
    day = int(match.group(1))
    month = int(match.group(2))
    year = int(match.group(3))
    if year < 100:
        year += 2000
    if not (1 <= day <= 31 and 1 <= month <= 12 and 2000 <= year <= 2100):
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def _extract_meeting_code(value: str) -> str | None:
    match = MEETING_CODE_RE.search(value)
    if not match:
        return None
    return match.group(1)


def _extract_committee_name(value: str) -> str | None:
    if "ישיבות מועצה" in value or "מועצת" in value or "מועצה" in value:
        return "ישיבות מועצה"
    committee_match = re.search(r"ועדת\s+([\u0590-\u05FF\s\-]{2,40})", value)
    if committee_match:
        return f"ועדת {committee_match.group(1).strip()}"
    return None


def _parse_decision_candidates(text: str) -> list[DecisionCandidate]:
    lines = _collect_lines(text)
    if not lines:
        return []

    section_start_index = _locate_decision_section_start(lines)
    start_idx = section_start_index if section_start_index is not None else 0
    candidates: list[DecisionCandidate] = []
    idx = start_idx
    while idx < len(lines):
        line = lines[idx]
        stripped = line.text.strip()
        if not stripped:
            idx += 1
            continue

        in_section = section_start_index is not None and idx >= section_start_index
        number_match = NUMBERED_RE.match(stripped)
        if number_match is None and in_section:
            alt_match = ALT_NUMBERED_RE.match(stripped)
            if alt_match and HEBREW_CHAR_RE.search(alt_match.group(2)):
                number_match = alt_match
        numbered = number_match is not None
        text_payload = number_match.group(2).strip() if number_match else stripped
        is_candidate = _is_decision_candidate_line(text_payload, in_section=in_section, numbered=numbered)
        if not is_candidate:
            idx += 1
            continue

        end_idx = idx
        parts = [text_payload]
        while end_idx + 1 < len(lines):
            nxt = lines[end_idx + 1].text.strip()
            if not nxt:
                break
            if _looks_like_new_item(nxt):
                break
            if len(parts) >= 4:
                break
            parts.append(nxt)
            end_idx += 1

        decision_text = " ".join(parts).strip()
        if len(decision_text) < 12:
            idx = end_idx + 1
            continue

        start_offset = line.start
        end_offset = lines[end_idx].end
        source_window = text[max(0, start_offset - 220) : min(len(text), end_offset + 220)]
        vote_hint = _parse_vote(decision_text)
        confidence = 0.45
        if in_section:
            confidence += 0.2
        if numbered:
            confidence += 0.15
        if any(phrase in decision_text for phrase in ("הוחלט", "אושר", "מאשרים")):
            confidence += 0.15
        if "|" in decision_text:
            confidence += 0.1
        if vote_hint is not None:
            confidence += 0.05

        agenda_item, normalized_text = _extract_agenda_item_from_line(decision_text)
        decision_number = (number_match.group(1) if number_match else None) or _extract_decision_number(decision_text)
        candidates.append(
            DecisionCandidate(
                decision_text=normalized_text,
                decision_number=decision_number,
                agenda_item=agenda_item,
                start_offset=start_offset,
                end_offset=end_offset,
                confidence=min(1.0, round(confidence, 4)),
                source_window=source_window,
                vote_hint=vote_hint,
            )
        )
        idx = end_idx + 1

    deduped: list[DecisionCandidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        sig = _decision_signature(candidate.decision_text)
        if not sig or sig in seen:
            continue
        seen.add(sig)
        deduped.append(candidate)
    return deduped


def _collect_lines(text: str) -> list[ParsedLine]:
    lines: list[ParsedLine] = []
    cursor = 0
    for idx, raw in enumerate(text.split("\n")):
        start = cursor
        end = start + len(raw)
        lines.append(ParsedLine(index=idx, text=raw, start=start, end=end))
        cursor = end + 1
    return lines


def _locate_decision_section_start(lines: list[ParsedLine]) -> int | None:
    for line in lines:
        compact = WHITESPACE_RE.sub(" ", line.text.strip())
        if not compact:
            continue
        if any(marker in compact for marker in DECISION_SECTION_MARKERS):
            return line.index
    return None


def _is_decision_candidate_line(value: str, *, in_section: bool, numbered: bool) -> bool:
    compact = WHITESPACE_RE.sub(" ", value).strip()
    if not compact:
        return False
    if len(compact) > 1000:
        return False
    if compact in DECISION_SECTION_MARKERS:
        return False
    if any(marker in compact for marker in ("נושא ההחלטה", "תוכן ההחלטה", "תחילת תוקף")):
        return False
    if "|" in compact:
        if "החלטה" in compact or in_section:
            return True
    if any(phrase in compact for phrase in DECISION_PHRASES):
        return True
    if numbered and in_section and len(compact) > 25:
        return True
    if in_section and ("בעד" in compact or "פה אחד" in compact):
        return True
    return False


def _looks_like_new_item(value: str) -> bool:
    compact = value.strip()
    if not compact:
        return True
    if NUMBERED_RE.match(compact):
        return True
    alt_match = ALT_NUMBERED_RE.match(compact)
    if alt_match and HEBREW_CHAR_RE.search(alt_match.group(2)):
        return True
    if HEADING_RE.match(compact):
        return True
    if any(marker in compact for marker in DECISION_SECTION_MARKERS):
        return True
    return False


def _extract_agenda_item_from_line(value: str) -> tuple[str | None, str]:
    compact = WHITESPACE_RE.sub(" ", value).strip()
    if "|" in compact:
        parts = [part.strip() for part in compact.split("|") if part.strip()]
        if len(parts) >= 2:
            agenda = parts[0][:120]
            decision_text = max(parts[1:], key=len)
            return agenda, decision_text
    return None, compact


def _extract_decision_number(value: str) -> str | None:
    match = DECISION_NUMBER_RE.search(value)
    if not match:
        return None
    return match.group(1)


def _parse_vote(value: str) -> dict | None:
    compact = WHITESPACE_RE.sub(" ", value).strip()
    if not compact:
        return None

    slash = VOTE_SLASH_RE.search(compact)
    if slash:
        return {
            "for_count": int(slash.group(1)),
            "against_count": int(slash.group(2)),
            "abstain_count": int(slash.group(3)),
            "unanimous": False,
            "is_uncertain": False,
            "confidence": 0.96,
            "raw_text": slash.group(0),
            "metadata": {"pattern": "slash"},
        }

    counts = VOTE_COUNTS_RE.search(compact)
    if counts:
        abstain = int(counts.group(3)) if counts.group(3) is not None else None
        return {
            "for_count": int(counts.group(1)),
            "against_count": int(counts.group(2)),
            "abstain_count": abstain,
            "unanimous": False,
            "is_uncertain": False,
            "confidence": 0.94,
            "raw_text": counts.group(0),
            "metadata": {"pattern": "counts"},
        }

    if any(phrase in compact for phrase in UNANIMOUS_PHRASES):
        return {
            "for_count": None,
            "against_count": None,
            "abstain_count": None,
            "unanimous": True,
            "is_uncertain": False,
            "confidence": 0.92,
            "raw_text": next(phrase for phrase in UNANIMOUS_PHRASES if phrase in compact),
            "metadata": {"pattern": "unanimous_phrase"},
        }

    if VOTE_EVIDENCE_RE.search(compact):
        return {
            "for_count": None,
            "against_count": None,
            "abstain_count": None,
            "unanimous": None,
            "is_uncertain": True,
            "confidence": 0.4,
            "raw_text": compact,
            "metadata": {"pattern": "uncertain"},
        }
    return None


def _decision_signature(value: str) -> str:
    compact = WHITESPACE_RE.sub(" ", value).strip().casefold()
    compact = NON_WORD_RE.sub(" ", compact)
    compact = WHITESPACE_RE.sub(" ", compact).strip()
    return compact[:255]


def _nearest_page(citation_map: list[dict[str, int]], offset: int) -> int | None:
    best_distance: int | None = None
    best_page: int | None = None
    for item in citation_map:
        start = int(item.get("start", 0))
        end = int(item.get("end", 0))
        page = int(item.get("page", 0))
        if start <= offset <= end:
            return page
        if offset < start:
            distance = start - offset
        else:
            distance = offset - end
        if best_distance is None or distance < best_distance:
            best_distance = distance
            best_page = page
    return best_page


def _default_fallback_metadata() -> dict:
    return {
        "fallback_used": False,
        "fallback_reason": None,
        "fallback_provider": None,
        "fallback_model": None,
        "fallback_invoked_at": None,
        "fallback_validation_status": None,
        "fallback_validation_reasons": [],
    }


def _default_api_model_metadata() -> dict:
    return {
        "api_model_used": False,
        "api_model_provider": None,
        "api_model_name": None,
        "api_model_invoked_at": None,
        "api_model_attempts": 0,
        "api_model_status": None,
        "api_model_reasons": [],
    }


def _validate_fallback_output(*, payload: dict | None, source_window: str, row_text: str) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if payload is None:
        reasons.append("fallback_empty_response")
        return False, reasons

    decision_text_raw = payload.get("decision_text")
    decision_text = decision_text_raw if isinstance(decision_text_raw, str) else ""
    if not decision_text.strip():
        reasons.append("fallback_missing_decision_text")

    span_start = payload.get("span_start")
    span_end = payload.get("span_end")
    has_valid_span = (
        isinstance(span_start, int)
        and isinstance(span_end, int)
        and span_start >= 0
        and span_end > span_start
        and span_end <= len(source_window)
    )
    if has_valid_span:
        span_text = source_window[span_start:span_end]
    else:
        span_text = source_window

    if not reasons:
        target = _normalized_text(decision_text)
        source_norm = _normalized_text(source_window)
        span_norm = _normalized_text(span_text)
        row_norm = _normalized_text(row_text)
        if target not in source_norm and target not in span_norm:
            overlap = _token_overlap_ratio(target, span_norm)
            if overlap < 0.6:
                reasons.append("fallback_text_not_grounded")

        row_overlap = _token_overlap_ratio(target, row_norm)
        if row_overlap < 0.5:
            reasons.append("fallback_not_aligned_to_row")

    return not reasons, reasons


def _normalized_text(value: str) -> str:
    compact = value.casefold()
    compact = NON_WORD_RE.sub(" ", compact)
    return WHITESPACE_RE.sub(" ", compact).strip()


def _token_overlap_ratio(left: str, right: str) -> float:
    left_tokens = {token for token in left.split(" ") if token}
    right_tokens = {token for token in right.split(" ") if token}
    if not left_tokens or not right_tokens:
        return 0.0
    shared = left_tokens.intersection(right_tokens)
    return len(shared) / max(len(left_tokens), 1)
