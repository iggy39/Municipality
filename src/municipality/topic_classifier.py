from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from municipality.chunking import normalize_for_search
from municipality.models import ArtifactTopicAnnotation, RetrievalArtifact
from municipality.rag_llm import RAG_CALL_CLASSIFY, RagLlmClient, build_rag_llm_client
from municipality.topic_label_quality import is_low_quality_topic_label


ROOT_TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "הקצאות": ("הקצא", "עמותה", "רשות שימוש", "קרקע", "מקלט ציבורי"),
    "תכנון ובנייה": ("תכנית", "תוכנית", 'תב"ע', "התנגדות", "בנייה", "היתר"),
    "תחבורה ובטיחות": ("תחבורה", "חניה", "כביש", "צומת", "בטיחות", "תאונות"),
    "חינוך": ("בית ספר", "גני ילדים", "חינוך", "תלמיד", "כיתה"),
    "תקציב ומכרזים": ("תקציב", 'תב"ר', "מכרז", "הצעת מחיר", "מימון"),
    "רווחה וביטחון": ("רווחה", "אלימות", "ביטחון", "אבטחה", "מוקד"),
    "פינויים ואכיפה": ("פינוי", "פינויים", "אכיפה", "פינוי פולשים"),
    "הסכמים": ("הסכם", "התקשרות", "פטור ממכרז", "רשות שימוש"),
}
STOPWORDS = {normalize_for_search(term) for term in ("פרוטוקול", "ועדה", "ועדת", "ישיבה", "נושא", "סעיף", "החלטה", "החלטות")}
PROCEDURAL_PREFIXES = tuple(
    normalize_for_search(value)
    for value in (
        "מורשי חתימה",
        "בעלי זכות חתימה",
        "זכות חתימה",
        "כתובת",
        "גוש",
        "חלקה",
        "מגרש",
        "מספר תיק",
        "תאריך",
        "רחוב",
    )
)


@dataclass(slots=True)
class TopicClassification:
    structural_topic_he: str | None
    primary_topic_he: str | None
    secondary_topics: list[str]
    section_summary: str | None
    confidence: float
    classifier_route: str
    provider_name: str | None = None
    model_name: str | None = None


class ArtifactTopicClassifier:
    def __init__(self, session: Session, *, llm_client: RagLlmClient | None = None):
        self.session = session
        self.llm_client = llm_client or build_rag_llm_client()

    def annotate_document_version(self, *, document_version_id: int) -> int:
        artifacts: list[RetrievalArtifact] = list(self.session.execute(
            select(RetrievalArtifact)
            .where(RetrievalArtifact.document_version_id == document_version_id)
            .order_by(RetrievalArtifact.ordinal.asc())
        ).scalars().all())
        if not artifacts:
            return 0

        existing = self.session.execute(
            select(ArtifactTopicAnnotation).where(
                ArtifactTopicAnnotation.artifact_id.in_([artifact.artifact_id for artifact in artifacts])
            )
        ).scalars().all()
        existing_by_artifact = {str(row.artifact_id): row for row in existing}
        representative_by_group: dict[str, RetrievalArtifact] = {
            group_key: _representative_artifact(group_artifacts)
            for group_key, group_artifacts in _group_artifacts_for_annotation(artifacts).items()
        }
        classification_by_representative_id = self._batch_refine_topics_with_llm(list(representative_by_group.values()))
        touched = 0
        artifact_groups = _group_artifacts_for_annotation(artifacts)
        for _group_key, group_artifacts in artifact_groups.items():
            representative = _representative_artifact(group_artifacts)
            classification = classification_by_representative_id.get(str(representative.artifact_id))
            if classification is None:
                continue
            for artifact in group_artifacts:
                row = existing_by_artifact.get(str(artifact.artifact_id))
                if row is None:
                    row = ArtifactTopicAnnotation(artifact_id=str(artifact.artifact_id))
                    self.session.add(row)
                row.structural_topic_he = classification.structural_topic_he
                row.structural_topic_norm = normalize_for_search(classification.structural_topic_he or "") or None
                row.primary_topic_he = classification.primary_topic_he
                row.primary_topic_norm = normalize_for_search(classification.primary_topic_he or "") or None
                row.secondary_topics_json = json.dumps(classification.secondary_topics, ensure_ascii=False)
                row.section_summary = classification.section_summary
                row.classifier_confidence = classification.confidence
                row.classifier_route = classification.classifier_route
                row.provider_name = classification.provider_name
                row.model_name = classification.model_name
                touched += 1
        self.session.flush()
        return touched

    def _batch_refine_topics_with_llm(self, artifacts: list[RetrievalArtifact]) -> dict[str, TopicClassification]:
        if not self.llm_client.provider.is_configured():
            return {}

        items = []
        artifact_by_id: dict[str, RetrievalArtifact] = {}
        for artifact in artifacts:
            structural_topic = _structural_topic_for_artifact(artifact=artifact)
            if not _should_use_llm_topic_refinement(artifact):
                continue
            artifact_by_id[str(artifact.artifact_id)] = artifact
            items.append(
                {
                    "artifact_id": str(artifact.artifact_id),
                    "artifact_kind": artifact.artifact_kind,
                    "document_title": artifact.title_he,
                    "committee_name": artifact.committee_name,
                    "meeting_date": artifact.meeting_date,
                    "header_path": _loads_json_list(artifact.header_path_json),
                    "body_text": _trim_chars(artifact.body_text, limit=1200),
                    "structural_topic_prior": structural_topic,
                }
            )
        if not items:
            return {}
        refined_by_id: dict[str, TopicClassification] = {}
        for batch in _chunked(items, size=8):
            result = self.llm_client.generate(
                call_type=RAG_CALL_CLASSIFY,
                instruction=(
                    "classify each municipal artifact into a primary hebrew topic and optional secondary topics. "
                    "use structure as a high-priority prior, refine with body text, and return strict json with key items. "
                    "each item must include artifact_id, primary_topic_he, secondary_topics, section_summary, confidence."
                ),
                payload={"items": batch},
                temperature=0.0,
            )
            if result.error_code or not result.text:
                self._refine_batch_items_individually(
                    batch=batch,
                    artifact_by_id=artifact_by_id,
                    refined_by_id=refined_by_id,
                )
                continue
            parsed_items = _parse_ai21_topic_batch_payload(result.text)
            if not parsed_items:
                self._refine_batch_items_individually(
                    batch=batch,
                    artifact_by_id=artifact_by_id,
                    refined_by_id=refined_by_id,
                )
                continue
            parsed_by_artifact_id = {
                str(item.get("artifact_id") or "").strip(): item
                for item in parsed_items
                if str(item.get("artifact_id") or "").strip()
            }
            for parsed in parsed_items:
                artifact_id = str(parsed.get("artifact_id") or "").strip()
                artifact = artifact_by_id.get(artifact_id)
                if not artifact_id or artifact is None:
                    continue
                refined_by_id[artifact_id] = TopicClassification(
                    structural_topic_he=_structural_topic_for_artifact(artifact=artifact),
                    primary_topic_he=parsed.get("primary_topic_he"),
                    secondary_topics=parsed.get("secondary_topics") or [],
                    section_summary=parsed.get("section_summary") or None,
                    confidence=float(parsed.get("confidence") or 0.7),
                    classifier_route=f"{_provider_name(self.llm_client)}_dictalm_refine_batch",
                    provider_name=result.provider,
                    model_name=result.model,
                )
            missing_ids = [
                str(item.get("artifact_id") or "").strip()
                for item in batch
                if str(item.get("artifact_id") or "").strip() and str(item.get("artifact_id") or "").strip() not in parsed_by_artifact_id
            ]
            if missing_ids:
                self._refine_batch_items_individually(
                    batch=[item for item in batch if str(item.get("artifact_id") or "").strip() in set(missing_ids)],
                    artifact_by_id=artifact_by_id,
                    refined_by_id=refined_by_id,
                )
        return refined_by_id

    def _refine_batch_items_individually(
        self,
        *,
        batch: list[dict[str, Any]],
        artifact_by_id: dict[str, RetrievalArtifact],
        refined_by_id: dict[str, TopicClassification],
    ) -> None:
        for item in batch:
            artifact_id = str(item.get("artifact_id") or "").strip()
            artifact = artifact_by_id.get(artifact_id)
            if artifact is None:
                continue
            refined = self._single_refine_topic_with_llm(artifact=artifact)
            if refined is not None:
                refined_by_id[artifact_id] = refined

    def _single_refine_topic_with_llm(
        self,
        *,
        artifact: RetrievalArtifact,
    ) -> TopicClassification | None:
        structural_topic = _structural_topic_for_artifact(artifact=artifact)
        result = self.llm_client.generate(
            call_type=RAG_CALL_CLASSIFY,
            instruction=(
                "classify the municipal artifact into a primary hebrew topic and optional secondary topics. "
                "use structure as a high-priority prior, refine with body text, and return strict json with keys primary_topic_he, secondary_topics, section_summary, confidence."
            ),
            payload={
                "artifact_id": str(artifact.artifact_id),
                "artifact_kind": artifact.artifact_kind,
                "document_title": artifact.title_he,
                "committee_name": artifact.committee_name,
                "meeting_date": artifact.meeting_date,
                "header_path": _loads_json_list(artifact.header_path_json),
                "body_text": _trim_chars(artifact.body_text, limit=1200),
                "structural_topic_prior": structural_topic,
            },
            temperature=0.0,
        )
        if result.error_code or not result.text:
            return None
        parsed = _parse_ai21_topic_payload(result.text)
        if parsed is None:
            return None
        return TopicClassification(
            structural_topic_he=structural_topic,
            primary_topic_he=parsed.get("primary_topic_he"),
            secondary_topics=parsed.get("secondary_topics") or [],
            section_summary=parsed.get("section_summary"),
            confidence=float(parsed.get("confidence") or 0.7),
            classifier_route=f"{_provider_name(self.llm_client)}_dictalm_refine_single",
            provider_name=result.provider,
            model_name=result.model,
        )


def _structural_topic_for_artifact(*, artifact: RetrievalArtifact) -> str | None:
    header_path = _loads_json_list(artifact.header_path_json)
    basis = " ".join([str(artifact.title_he or ""), str(artifact.committee_name or ""), *header_path])
    basis_norm = normalize_for_search(basis)
    best_root = None
    best_score = 0
    for root_topic, keywords in ROOT_TOPIC_KEYWORDS.items():
        score = sum(1 for keyword in keywords if normalize_for_search(keyword) in basis_norm)
        if score > best_score:
            best_root = root_topic
            best_score = score
    if best_root:
        leaf = _best_leaf_topic(header_path=header_path, body_text=artifact.body_text)
        return f"{best_root} > {leaf}" if leaf else best_root
    return _best_leaf_topic(header_path=header_path, body_text=artifact.body_text)


def _group_artifacts_for_annotation(artifacts: Sequence[RetrievalArtifact]) -> dict[str, list[RetrievalArtifact]]:
    grouped: dict[str, list[RetrievalArtifact]] = {}
    for artifact in artifacts:
        group_key = str(artifact.section_id or artifact.artifact_id)
        grouped.setdefault(group_key, []).append(artifact)
    return grouped


def _representative_artifact(artifacts: list[RetrievalArtifact]) -> RetrievalArtifact:
    priority = {
        "decision_unit": 0,
        "section_unit": 1,
        "header_plus_opening": 2,
        "section_summary": 3,
        "header_anchor": 4,
        "context_window": 5,
        "document_profile": 6,
    }
    return min(
        artifacts,
        key=lambda artifact: (
            priority.get(str(artifact.artifact_kind), 99),
            int(artifact.ordinal or 0),
        ),
    )


def _should_use_llm_topic_refinement(artifact: RetrievalArtifact) -> bool:
    if artifact.source_kind != "protocol":
        return False
    if artifact.artifact_kind not in {"decision_unit", "section_unit", "header_plus_opening"}:
        return False
    if len(" ".join(str(artifact.body_text or "").split())) < 30:
        return False
    return True


def _provider_name(llm_client: RagLlmClient) -> str:
    return str(getattr(llm_client.provider, "provider_name", "") or "").strip().casefold()


def _best_leaf_topic(*, header_path: list[str], body_text: str) -> str | None:
    for candidate in reversed(header_path):
        cleaned = _clean_topic_phrase(candidate)
        if cleaned:
            return cleaned
    return None


def _clean_topic_phrase(value: str) -> str | None:
    compact = " ".join(str(value or "").split()).strip(" .:-")
    if not compact:
        return None
    if is_low_quality_topic_label(compact):
        return None
    normalized_compact = normalize_for_search(compact)
    if any(normalized_compact.startswith(prefix) for prefix in PROCEDURAL_PREFIXES):
        return None
    if ":" in compact:
        prefix = normalize_for_search(compact.split(":", 1)[0])
        if any(prefix.startswith(proc_prefix) for proc_prefix in PROCEDURAL_PREFIXES):
            return None
    tokens = [token for token in compact.split() if normalize_for_search(token) not in STOPWORDS]
    if len(tokens) < 2:
        return None
    if len(tokens) > 6 and not any(normalize_for_search(token) in {normalize_for_search(root) for root in ROOT_TOPIC_KEYWORDS} for token in tokens):
        tokens = tokens[:6]
    cleaned = " ".join(tokens[:8]).strip() or None
    if cleaned and is_low_quality_topic_label(cleaned):
        return None
    return cleaned


def _parse_ai21_topic_payload(value: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    primary = " ".join(str(payload.get("primary_topic_he") or "").split()).strip()
    if not primary:
        return None
    if is_low_quality_topic_label(primary):
        return None
    secondary = _dedupe_topics(
        [
            candidate
            for candidate in [" ".join(str(item).split()).strip() for item in payload.get("secondary_topics") or [] if str(item).strip()]
            if candidate and not is_low_quality_topic_label(candidate)
        ]
    )
    summary = " ".join(str(payload.get("section_summary") or "").split()).strip() or None
    confidence = payload.get("confidence")
    try:
        confidence_value = max(0.0, min(1.0, float(confidence))) if confidence is not None else 0.7
    except (TypeError, ValueError):
        confidence_value = 0.7
    return {
        "primary_topic_he": primary,
        "secondary_topics": secondary,
        "section_summary": summary,
        "confidence": confidence_value,
    }


def _parse_ai21_topic_batch_payload(value: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, dict):
        return []
    items = payload.get("items")
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        artifact_id = " ".join(str(item.get("artifact_id") or "").split()).strip()
        parsed = _parse_ai21_topic_payload(json.dumps(item, ensure_ascii=False))
        if not artifact_id or parsed is None:
            continue
        out.append({"artifact_id": artifact_id, **parsed})
    return out


def _loads_json_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item).strip() for item in parsed if str(item).strip()]


def _dedupe_topics(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = normalize_for_search(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append(value)
    return out


def _trim_chars(value: str | None, *, limit: int) -> str:
    compact = " ".join(str(value or "").split()).strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 3].rstrip()}..."


def _chunked(items: list[dict[str, Any]], *, size: int) -> list[list[dict[str, Any]]]:
    if size <= 0:
        return [items]
    return [items[index : index + size] for index in range(0, len(items), size)]
