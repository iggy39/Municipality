from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from municipality.chunking import normalize_for_search
from municipality.models import (
    ArtifactSemanticLink,
    RetrievalArtifact,
    SemanticAlias,
    SemanticCandidateReject,
    SemanticDocumentRun,
    SemanticNode,
)
from municipality.pdf_first_v4_topic_policy import best_topic_policy_match
from municipality.topic_label_quality import is_low_quality_topic_label


TOPIC_TREE_VERSION = "pdf_first_v4_global_tree_2026_06_07"
TOPIC_ASSIGNMENT_BACKEND = "pdf_first_v4_global_tree"
BACKEND_VERSION = "v4"
V4_PROVENANCE = "pdf_first_v4_global_tree"

STRUCTURAL_ROLE_LABELS = {
    "body",
    "continuation",
    "outline_item",
    "metadata",
    "task_row",
    "section_heading",
    "vote_or_result",
    "table_header_only",
    "noise",
}

V4_ROOT_TOPICS: tuple[dict[str, Any], ...] = (
    {"root_topic_id": "root_agenda_queries", "root_label_he": "סדר יום ושאילתות", "keywords": ["שאילתה", "שאילתא", "שאילתות", "סדר יום", "סדר היום"]},
    {"root_topic_id": "root_mayor_updates", "root_label_he": "עדכוני ראש העיר", "keywords": ["עדכוני ראש העיר", "תשובת ראש העיר"]},
    {"root_topic_id": "root_order_proposals", "root_label_he": "הצעות לסדר", "keywords": ["הצעה לסדר", "הצעות לסדר"]},
    {"root_topic_id": "root_allocations", "root_label_he": "הקצאות ושימושים", "keywords": ["הקצאה", "הקצאות", "רשות שימוש", "שימוש", "מקרקעין", "גוש", "חלקה", "מגרש"]},
    {"root_topic_id": "root_agreements", "root_label_he": "הסכמים והתקשרויות", "keywords": ["הסכם", "הסכמים", "התקשרות", "מכרז", "פטור ממכרז", "הרשאה"]},
    {"root_topic_id": "root_supports", "root_label_he": "תמיכות", "keywords": ["תמיכה", "תמיכות", "ועדת תמיכות"]},
    {"root_topic_id": "root_budget_finance", "root_label_he": "תקציב וכספים", "keywords": ["תקציב", "כספים", "תב\"ר", "תבר", "חובות", "דוח כספי", "דוחות כספיים", "הרשאות"]},
    {"root_topic_id": "root_planning_building", "root_label_he": "תכנון ובנייה", "keywords": ["תכנון", "בנייה", "בניה", "תוכנית", "תכנית", "היתר", "הסכם הגג", "טופס"]},
    {"root_topic_id": "root_transport_safety", "root_label_he": "תחבורה ובטיחות", "keywords": ["תחבורה", "בטיחות", "תמרור", "חניה", "כביש", "אוטובוס", "תאונות"]},
    {"root_topic_id": "root_education", "root_label_he": "חינוך", "keywords": ["חינוך", "בית ספר", "בתי ספר", "גן", "גנים", "תלמידים", "צהרון", "מעונות"]},
    {"root_topic_id": "root_welfare_social", "root_label_he": "רווחה ושירותים חברתיים", "keywords": ["רווחה", "שירותים חברתיים", "נזקקים", "קשישים", "הגיל השלישי", "עריריים", "אלמנים", "אלמנות", "היפוטרמיה"]},
    {"root_topic_id": "root_culture_sport", "root_label_he": "תרבות וספורט", "keywords": ["תרבות", "ספורט", "איצטדיון", "אצטדיון", "כדורסל", "משכן", "אומנויות הבמה", "אמנויות הבמה"]},
    {"root_topic_id": "root_infrastructure_environment", "root_label_he": "תשתיות וסביבה", "keywords": ["תשתיות", "סביבה", "הצפות", "ניקיון", "ביוב", "מים", "פארק", "זיהום"]},
    {"root_topic_id": "root_religious_services", "root_label_he": "דת ושירותי דת", "keywords": ["דת", "דתית", "שירותי דת", "מועצה דתית", "בית כנסת", "מקווה", "מקווה טהרה", "רב", "הרבצת תורה"]},
    {"root_topic_id": "root_administration", "root_label_he": "מנהל עירוני ומינויים", "keywords": ["מינוי", "מינויים", "מורשי חתימה", "האצלת סמכויות", "ועדה", "דירקטוריון", "דירקטוריונים", "תאגידים", "ביקורת", "דוח ביקורת", "דו\"ח ביקורת", "החלטות מועצה", "חילופי גברי", "קריאת רחוב", "שם רחוב", "שמות רחובות"]},
    {"root_topic_id": "root_security_enforcement", "root_label_he": "ביטחון ואכיפה", "keywords": ["ביטחון", "בטחון", "אכיפה", "אלימות", "אלימות במשפחה", "משטרה", "מיגון", "מקלט"]},
    {"root_topic_id": "root_travel_approvals", "root_label_he": "אישורי נסיעות", "keywords": ["אישור נסיעה", "נסיעה", "משלחת", "דוח נסיעה"]},
    {"root_topic_id": "root_guard_services", "root_label_he": "שמירה והיטלים", "keywords": ["שמירה", "היטל שמירה", "שירותי שמירה"]},
    {"root_topic_id": "root_commerce_assets", "root_label_he": "נכסים ומרכזים מסחריים", "keywords": ["נכס", "נכסים", "מרכז מסחרי", "מרכזים מסחריים", "קניון", "מבנה"]},
    {"root_topic_id": "root_hr_labor", "root_label_he": "כוח אדם ועובדים", "keywords": ["כוח אדם", "כח אדם", "עובדים", "עובדי עירייה", "עבודה נוספת", "תחילת עבודה", "תחילת עבודתו", "שכר", "שכרו", "תקן", "מנהל אגף"]},
)

ROOT_BY_ID = {row["root_topic_id"]: row for row in V4_ROOT_TOPICS}
ROOT_BY_NORM = {normalize_for_search(row["root_label_he"]): row for row in V4_ROOT_TOPICS}
PROCEDURAL_ROOT_ONLY_IDS = {"root_agenda_queries"}


@dataclass(slots=True)
class TopicValidationResult:
    status: str
    label: str | None
    cleaned_label: str | None
    reason: str | None = None
    aliases_he: list[str] = field(default_factory=list)
    route: str = "deterministic_validation"


@dataclass(slots=True)
class V4NodeWriteResult:
    root_node_id: int
    child_node_id: int | None
    linked_node_id: int
    status: str


def global_topic_tree_payload(*, existing_tree: dict[str, Any] | None = None, attachment_contexts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    children_by_root = _children_by_root_from_tree(existing_tree or {})
    roots_by_id = _roots_by_id_from_tree(existing_tree or {})
    return {
        "topic_tree_version": TOPIC_TREE_VERSION,
        "backend_version": BACKEND_VERSION,
        "root_topics": [
            {
                "root_topic_id": root["root_topic_id"],
                "root_label_he": root["root_label_he"],
                "keywords": list(root.get("keywords") or []),
                "profile": roots_by_id.get(root["root_topic_id"], {}).get("profile"),
                "children": children_by_root.get(root["root_topic_id"], []),
            }
            for root in V4_ROOT_TOPICS
        ],
        "attachment_contexts": attachment_contexts or [],
    }


def seed_root_nodes(session: Session, *, source_site_id: int, document_version_id: int | None = None) -> dict[str, SemanticNode]:
    now = datetime.utcnow()
    out: dict[str, SemanticNode] = {}
    for root in V4_ROOT_TOPICS:
        node = upsert_semantic_node(
            session,
            source_site_id=source_site_id,
            label=root["root_label_he"],
            root_topic_id=root["root_topic_id"],
            semantic_type="pdf_first_v4_topic_root",
            parent_node=None,
            status="active",
            support_count=0,
            confidence=1.0,
            document_version_id=document_version_id,
            metadata_extra={"seeded_root": True},
            now=now,
        )
        out[root["root_topic_id"]] = node
    session.flush()
    return out


def current_tree_from_db(session: Session, *, source_site_id: int | None = None, include_candidates: bool = True, include_profiles: bool = False, example_limit: int = 3) -> dict[str, Any]:
    stmt = select(SemanticNode).where(SemanticNode.node_kind == "topic")
    if source_site_id is not None:
        stmt = stmt.where(SemanticNode.source_site_id == int(source_site_id))
    statuses = ["active", "candidate"] if include_candidates else ["active"]
    stmt = stmt.where(SemanticNode.status.in_(statuses))
    nodes = session.execute(stmt.order_by(SemanticNode.depth.asc(), SemanticNode.pref_label_norm.asc())).scalars().all()
    roots_by_id = {int(node.id): node for node in nodes if node.parent_node_id is None}
    children_by_parent: dict[int, list[SemanticNode]] = {}
    for node in nodes:
        if node.parent_node_id is not None:
            children_by_parent.setdefault(int(node.parent_node_id), []).append(node)
    profiles_by_node_id = _topic_profiles_by_node_id(session=session, nodes=nodes, example_limit=example_limit) if include_profiles else {}
    roots = []
    for root in roots_by_id.values():
        root_topic_id = semantic_node_root_id(root) or root_id_for_label(root.pref_label_he)
        root_payload = {
            "root_topic_id": root_topic_id,
            "root_label_he": root.pref_label_he,
            "semantic_node_id": int(root.id),
            "support_count": int(root.support_count or 0),
            "status": root.status,
            "children": [],
        }
        if include_profiles:
            root_payload["profile"] = profiles_by_node_id.get(int(root.id), _generic_topic_profile(label=root.pref_label_he, aliases=[], examples=[]))
        children = []
        for child in children_by_parent.get(int(root.id), []):
            child_payload = {
                "child_topic_id": semantic_node_child_id(child) or child_topic_id(root_topic_id or "root", child.pref_label_he),
                "child_label_he": child.pref_label_he,
                "semantic_node_id": int(child.id),
                "support_count": int(child.support_count or 0),
                "status": child.status,
            }
            if include_profiles:
                child_payload["profile"] = profiles_by_node_id.get(int(child.id), _generic_topic_profile(label=child.pref_label_he, aliases=[], examples=[]))
            children.append(child_payload)
        root_payload["children"] = children
        roots.append(root_payload)
    return {"topic_tree_version": TOPIC_TREE_VERSION, "roots": roots}


def tree_lines(tree: dict[str, Any]) -> list[str]:
    roots = tree.get("roots") or global_topic_tree_payload().get("root_topics") or []
    lines: list[str] = []
    for root in roots:
        root_id = root.get("root_topic_id") or root.get("root_id") or "root_unknown"
        label = root.get("root_label_he") or root.get("label_he") or ""
        support = int(root.get("support_count") or root.get("support") or 0)
        lines.append(f"{label} [{root_id}] support={support}")
        for child in root.get("children") or []:
            child_id = child.get("child_topic_id") or child.get("topic_id") or "child_unknown"
            child_label = child.get("child_label_he") or child.get("label_he") or ""
            child_support = int(child.get("support_count") or child.get("support") or 0)
            status = str(child.get("status") or "active")
            lines.append(f"  {child_label} [{child_id}] support={child_support} status={status}")
    return lines


def alias_lines(session: Session, *, source_site_id: int | None = None) -> list[str]:
    stmt = select(SemanticAlias, SemanticNode).join(SemanticNode, SemanticNode.id == SemanticAlias.semantic_node_id)
    if source_site_id is not None:
        stmt = stmt.where(SemanticNode.source_site_id == int(source_site_id))
    rows = session.execute(stmt.order_by(SemanticAlias.alias_label_norm.asc())).all()
    return [f"{alias.alias_label_he} -> {node.pref_label_he}" for alias, node in rows]


def _topic_profiles_by_node_id(*, session: Session, nodes: list[SemanticNode], example_limit: int) -> dict[int, dict[str, Any]]:
    node_ids = [int(node.id) for node in nodes]
    if not node_ids:
        return {}
    aliases_by_node: dict[int, list[str]] = {int(node.id): [] for node in nodes}
    examples_by_node: dict[int, list[dict[str, Any]]] = {int(node.id): [] for node in nodes}
    for alias in session.execute(select(SemanticAlias).where(SemanticAlias.semantic_node_id.in_(node_ids)).order_by(SemanticAlias.alias_label_norm.asc())).scalars().all():
        aliases_by_node.setdefault(int(alias.semantic_node_id), []).append(alias.alias_label_he)
    rows = session.execute(
        select(ArtifactSemanticLink, RetrievalArtifact)
        .join(RetrievalArtifact, RetrievalArtifact.artifact_id == ArtifactSemanticLink.artifact_id)
        .where(ArtifactSemanticLink.semantic_node_id.in_(node_ids))
        .order_by(ArtifactSemanticLink.semantic_node_id.asc(), RetrievalArtifact.document_version_id.asc(), RetrievalArtifact.ordinal.asc())
    ).all()
    for link, artifact in rows:
        node_id = int(link.semantic_node_id)
        examples = examples_by_node.setdefault(node_id, [])
        if len(examples) >= max(0, int(example_limit)):
            continue
        example = _topic_profile_example(link=link, artifact=artifact)
        if example:
            examples.append(example)
    return {int(node.id): _generic_topic_profile(label=node.pref_label_he, aliases=aliases_by_node.get(int(node.id), []), examples=examples_by_node.get(int(node.id), []), metadata=_loads_dict(node.metadata_json)) for node in nodes}


def _generic_topic_profile(*, label: str, aliases: list[str], examples: list[dict[str, Any]], metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    metadata = metadata or {}
    summary = str(metadata.get("summary_he") or metadata.get("description_he") or "").strip()
    if not summary:
        summary = f"נושא מוניציפלי שהראיות שלו עוסקות ב{label}."
    return {
        "summary_he": summary,
        "aliases_he": _dedupe_labels(aliases),
        "positive_examples": examples,
        "negative_examples": [row for row in metadata.get("negative_examples") or [] if isinstance(row, dict)][:5],
        "curation_status": str(metadata.get("curation_status") or "auto_profile"),
    }


def _topic_profile_example(*, link: ArtifactSemanticLink, artifact: RetrievalArtifact) -> dict[str, Any] | None:
    artifact_metadata = _loads_dict(artifact.metadata_json)
    link_metadata = _loads_dict(link.metadata_json)
    evidence = artifact_metadata.get("evidence_contract") if isinstance(artifact_metadata.get("evidence_contract"), dict) else {}
    quote = str(evidence.get("quote_he") or "").strip()
    if not quote:
        for ref in artifact_metadata.get("evidence_refs") or []:
            if isinstance(ref, dict) and str(ref.get("quote_he") or "").strip():
                quote = str(ref.get("quote_he") or "").strip()
                break
    if not quote:
        quote = str(artifact.body_text or artifact.retrieval_text or "").strip()
    quote = " ".join(quote.split())[:500]
    if not quote:
        return None
    return {
        "artifact_id": artifact.artifact_id,
        "source_kind": artifact.source_kind,
        "source_page": artifact.start_page,
        "quote_he": quote,
        "route": link_metadata.get("route") or artifact_metadata.get("topic_assignment_route"),
    }


def validate_child_label(
    *,
    raw_label: Any,
    root_label_he: str,
    evidence_text: str,
    selected_existing: bool = False,
    structural_role: str | None = None,
) -> TopicValidationResult:
    raw = compact_label(raw_label)
    if structural_role and structural_role in {"body", "continuation", "outline_item", "metadata", "task_row"} and not raw:
        return TopicValidationResult("rejected", None, None, "structural_role_without_label")
    if not raw:
        return TopicValidationResult("rejected", None, None, "empty_label")
    if raw in STRUCTURAL_ROLE_LABELS:
        return TopicValidationResult("rejected", raw, None, "structural_role_label")
    cleaned = clean_topic_label(raw)
    if not cleaned:
        return TopicValidationResult("rejected", raw, None, "empty_after_cleaning")
    cleaned_aliases = [raw] if normalize_for_search(raw) != normalize_for_search(cleaned) else []
    if normalize_for_search(cleaned) == normalize_for_search("נושא כללי"):
        return TopicValidationResult("rejected", raw, None, "general_topic_forbidden")
    if normalize_for_search(cleaned) == normalize_for_search(root_label_he):
        return TopicValidationResult("rejected", raw, None, "child_same_as_root")
    if is_low_quality_topic_label(cleaned):
        return TopicValidationResult("rejected", raw, cleaned, "low_quality_label")
    reason = noisy_label_reason(cleaned)
    if reason:
        stripped = strip_decision_prose(cleaned)
        if stripped and stripped != cleaned and not noisy_label_reason(stripped) and evidence_supports_label(stripped, evidence_text):
            return TopicValidationResult("active", raw, stripped, None, aliases_he=_dedupe_labels([*cleaned_aliases, cleaned]), route="cleaned_decision_prose")
        return TopicValidationResult("rejected", raw, cleaned, reason)
    if selected_existing or evidence_supports_label(cleaned, evidence_text):
        return TopicValidationResult("active", raw, cleaned, None, aliases_he=cleaned_aliases)
    return TopicValidationResult("rejected", raw, cleaned, "label_not_supported_by_evidence")


def derive_child_candidate(*, text: str, canonical_label_he: str | None = None, outline_title_he: str | None = None) -> tuple[str | None, str]:
    compact = " ".join(str(text or "").split())
    patterns = [
        (r"(?:^|\s)(?:הנדון|נדון)\s*[:\-–]?\s+([^.;\n]{4,150})", "subject_heading"),
        (r"(?:^|\s)(?:בנושא|נושא)\s*[:\-–]?\s+([^.;\n]{4,150})", "subject_heading"),
        (r"שאיל(?:תא|תה)\s+[^.;\n]{0,140}?בנושא\s+[\"'׳״”]*([^\"'׳״”();.\n]{3,140})", "question_subject"),
        (r"פרוטוקול\s+מישיבת\s+ועדת\s+([^.;:\n]{3,120})", "committee_protocol"),
        (r"פרוטוקול\s+ועדת\s+([^.;:\n]{3,120})", "committee_protocol"),
        (r"((?:סמינר|השתלמות|כנס|קורס|הכשרה)\s+[^.;\n]{6,150}?)(?=\s+בין\s+התאריכים|\s+בתאריכים|\s+מיום|\s+בתאריך|[.;\n]|$)", "activity_subject"),
        (r"((?:אישור|אשרו|מאשרים)\s+[^.;\n]{0,80}?(?:סמינר|השתלמות|כנס|קורס|הכשרה)\s+[^.;\n]{4,130}?)(?=\s+בין\s+התאריכים|\s+בתאריכים|\s+מיום|\s+בתאריך|[.;\n]|$)", "approved_activity_subject"),
        (r"((?:הסכם|אישור\s+הסכם|התקשרות)\s+[^.;\n]{6,150}?)(?=\s+מחליטים|\s+מאשרים|\s+מצ[\"'״]?ל|[.;\n]|$)", "agreement_subject"),
        (r"((?:הקצאה|הקצאת|רשות\s+שימוש|שימוש\s+במקרקעין)\s+[^.;\n]{6,150}?)(?=\s+מחליטים|\s+מאשרים|\s+מצ[\"'״]?ל|[.;\n]|$)", "allocation_subject"),
    ]
    for pattern, route in patterns:
        match = re.search(pattern, compact)
        if not match:
            continue
        label = clean_topic_label(match.group(1))
        if label:
            if route == "committee_protocol" and not label.startswith("ועדת"):
                label = f"ועדת {label}"
            return label, route
    for value, route in ((canonical_label_he, "canonical_label"), (outline_title_he, "outline_title")):
        label = clean_topic_label(value)
        if label:
            return label, route
    return None, "root_only"


def infer_root_topic_id(text: str, *, fallback: str | None = None) -> str:
    normalized = normalize_for_search(text)
    policy_root = _policy_root_override(normalized)
    if policy_root:
        return policy_root
    tokens = set(_search_token_variants(normalized))
    best_id = fallback if fallback in ROOT_BY_ID else None
    best_score = 0.0
    for root in V4_ROOT_TOPICS:
        score = sum(_root_keyword_score(keyword=str(keyword), normalized=normalized, tokens=tokens) for keyword in root.get("keywords") or [])
        if score > best_score:
            best_id = root["root_topic_id"]
            best_score = score
    return best_id or "root_agenda_queries"


def _policy_root_override(normalized: str) -> str | None:
    match = best_topic_policy_match(normalized)
    root_topic_id = str((match or {}).get("root_topic_id") or "")
    return root_topic_id if root_topic_id in ROOT_BY_ID else None


def _root_keyword_score(*, keyword: str, normalized: str, tokens: set[str]) -> float:
    keyword_norm = normalize_for_search(keyword)
    if not keyword_norm or not normalized:
        return 0.0
    keyword_tokens = _search_tokens(keyword_norm)
    if len(keyword_tokens) >= 2:
        return float(len(keyword_tokens)) if keyword_norm in normalized else 0.0
    if not keyword_tokens:
        return 0.0
    token = keyword_tokens[0]
    if token in tokens:
        return 1.0
    # Avoid short substrings such as "דת" matching inside unrelated words like "עבודתו".
    if len(token) >= 4 and any(value.startswith(token) for value in tokens):
        return 0.9
    return 0.0


def _search_tokens(value: str) -> list[str]:
    return re.findall(r"[\w\u0590-\u05FF]+", normalize_for_search(value))


def _search_token_variants(value: str) -> list[str]:
    tokens = _search_tokens(value)
    variants = set(tokens)
    for token in tokens:
        if len(token) >= 4 and token[0] in "ובכלמהש" and len(token[1:]) >= 3:
            variants.add(token[1:])
    return list(variants)


def resolve_child_topic_assignment(
    *,
    root_topic_id: str,
    root_label_he: str,
    child_label_he: str | None,
    evidence_text: str,
    structural_role: str | None = None,
) -> dict[str, Any]:
    root_id = root_topic_id if root_topic_id in ROOT_BY_ID else infer_root_topic_id(evidence_text)
    root_label = root_label_for_id(root_id) or root_label_he or ""
    validation = validate_child_label(
        raw_label=child_label_he,
        root_label_he=root_label,
        evidence_text=evidence_text,
        structural_role=structural_role,
    ) if child_label_he else None
    if not validation or validation.status != "active" or not validation.cleaned_label:
        return {
            "root_topic_id": root_id,
            "root_label_he": root_label,
            "child_label_he": None,
            "child_topic_id": None,
            "status": "active",
            "reason": validation.reason if validation else None,
            "route_suffix": f"child_demoted:{validation.reason}" if validation and validation.reason else "root_only",
            "aliases_he": validation.aliases_he if validation else [],
        }
    child_label = canonical_child_label(validation.cleaned_label, evidence_text=evidence_text) or validation.cleaned_label
    aliases_he = list(validation.aliases_he)
    if normalize_for_search(child_label) != normalize_for_search(validation.cleaned_label):
        aliases_he.append(validation.cleaned_label)
    semantic_root_id = semantic_root_for_child_label(child_label, evidence_text=evidence_text, fallback=root_id)
    procedural_reason = procedural_child_label_reason(child_label, evidence_text=evidence_text)
    if procedural_reason:
        target_root_id = semantic_root_id if semantic_root_id not in PROCEDURAL_ROOT_ONLY_IDS else root_id
        return {
            "root_topic_id": target_root_id,
            "root_label_he": root_label_for_id(target_root_id) or root_label,
            "child_label_he": None,
            "child_topic_id": None,
            "status": "active",
            "reason": procedural_reason,
            "route_suffix": f"child_demoted:{procedural_reason}",
            "aliases_he": aliases_he,
        }
    unsupported_reason = unsupported_child_domain_reason(child_label, evidence_text=evidence_text)
    if unsupported_reason:
        return {
            "root_topic_id": root_id,
            "root_label_he": root_label,
            "child_label_he": None,
            "child_topic_id": None,
            "status": "active",
            "reason": unsupported_reason,
            "route_suffix": f"child_demoted:{unsupported_reason}",
            "aliases_he": aliases_he,
        }
    if root_id in PROCEDURAL_ROOT_ONLY_IDS:
        if semantic_root_id in PROCEDURAL_ROOT_ONLY_IDS:
            return {
                "root_topic_id": root_id,
                "root_label_he": root_label,
                "child_label_he": None,
                "child_topic_id": None,
                "status": "active",
                "reason": "procedural_root_child_demoted",
                "route_suffix": "procedural_root_child_demoted",
                "aliases_he": aliases_he,
            }
        root_id = semantic_root_id
        root_label = root_label_for_id(root_id) or root_label
    elif semantic_root_id != root_id and _semantic_root_override_is_strong(child_label, evidence_text):
        root_id = semantic_root_id
        root_label = root_label_for_id(root_id) or root_label
    return {
        "root_topic_id": root_id,
        "root_label_he": root_label,
        "child_label_he": child_label,
        "child_topic_id": child_topic_id(root_id, child_label),
        "status": "active",
        "reason": None,
        "route_suffix": "semantic_reparent" if root_id != root_topic_id else validation.route,
        "aliases_he": aliases_he,
    }


def semantic_root_for_child_label(label: str | None, *, evidence_text: str = "", fallback: str | None = None) -> str:
    normalized = normalize_for_search(" ".join([str(label or ""), str(evidence_text or "")]))
    label_norm = normalize_for_search(label or "")
    if label_norm == normalize_for_search("ניקיון ואכיפה סביבתית במרחב הציבורי"):
        return "root_infrastructure_environment"
    if label_norm == normalize_for_search("שימוש ארעי במגרשים ריקים"):
        return "root_allocations"
    if any(term in normalized for term in ["תחבורה ציבורית", "פרויקט תחבורה", "כיכר רמון", "משרד התחבורה"]):
        return "root_transport_safety"
    if any(term in normalized for term in ["גללי כלבים", "ניקיון העיר", "אכיפה סביבתית", "סכנה תברואתית"]):
        return "root_infrastructure_environment"
    if any(term in normalized for term in ["מלח", "מל ח", "מל\"ח", "חירום", "פקער", "פיקוד העורף", "מקלטים חכמים", "ביטחון"]):
        return "root_security_enforcement"
    if any(term in normalized for term in ["בית ספר", "חינוך", "למידה חדשנית", "סמינר מקצועי", "מערכת החינוך"]):
        return "root_education"
    if "ועדת ביקורת" in label_norm or "מבקר" in normalized:
        return "root_administration"
    if "מלגות" in normalized:
        return "root_education"
    if any(term in normalized for term in ["תמיכות", "ועדת משנה לתמיכות"]):
        return "root_supports"
    if any(term in normalized for term in ["הקצאות", "הקצאת", "קרקע", "שימוש ארעי במגרשים"]):
        return "root_allocations"
    if any(term in normalized for term in ["האצלת סמכויות", "מורשי חתימה", "מינוי", "מינויים", "גזבר העירייה"]):
        return "root_administration"
    return infer_root_topic_id(" ".join([str(label or ""), str(evidence_text or "")]), fallback=fallback)


def root_id_for_label(label: str | None) -> str | None:
    normalized = normalize_for_search(label or "")
    row = ROOT_BY_NORM.get(normalized)
    return str(row["root_topic_id"]) if row else None


def root_label_for_id(root_topic_id: str | None) -> str | None:
    row = ROOT_BY_ID.get(str(root_topic_id or ""))
    return str(row["root_label_he"]) if row else None


def child_topic_id(root_topic_id: str, label: str) -> str:
    digest = hashlib.sha1(f"{TOPIC_TREE_VERSION}|{root_topic_id}|{normalize_for_search(label)}".encode("utf-8")).hexdigest()[:14]
    return f"child_{digest}"


def evidence_span_id(*, artifact_id: str, role: str, page: int | None, quote: str) -> str:
    digest = hashlib.sha1(f"{artifact_id}|{role}|{page}|{quote}".encode("utf-8")).hexdigest()[:16]
    return f"ev_{digest}"


def evidence_payload(
    *,
    artifact_id: str,
    document_version_id: int | None,
    source_kind: str,
    page: int | None,
    quote_he: str,
    role: str,
    source_region_ids: list[Any] | None = None,
    source_block_ids: list[Any] | None = None,
    start_offset: int | None = None,
    end_offset: int | None = None,
    confidence: float = 0.7,
) -> dict[str, Any]:
    quote = " ".join(str(quote_he or "").split())[:500]
    return {
        "evidence_span_id": evidence_span_id(artifact_id=artifact_id, role=role, page=page, quote=quote),
        "artifact_id": artifact_id,
        "document_version_id": document_version_id,
        "source_kind": source_kind,
        "page": page,
        "start_offset": start_offset,
        "end_offset": end_offset,
        "source_region_ids": [str(value) for value in (source_region_ids or []) if str(value).strip()],
        "source_block_ids": [str(value) for value in (source_block_ids or []) if str(value).strip()],
        "quote_he": quote,
        "evidence_role": role,
        "confidence": max(0.0, min(1.0, float(confidence))),
    }


def confidence_label(confidence: Any) -> str:
    value = _safe_float(confidence)
    if value is None:
        return "unknown"
    if value >= 0.78:
        return "high"
    if value >= 0.5:
        return "medium"
    return "low"


def evidence_reference(
    *,
    source_type: str,
    source_title: str | None,
    source_url: str | None,
    retrieval_artifact_id: str,
    artifact_kind: str,
    retrieval_set_id: str | None,
    header_path: list[str],
    page_span: dict[str, int | None],
    offsets: dict[str, int | None],
    confidence: Any,
    extraction_warnings: list[str] | None = None,
    bbox: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "source_type": source_type,
        "source_title": source_title,
        "source_url": source_url,
        "retrieval_artifact_id": retrieval_artifact_id,
        "artifact_kind": artifact_kind,
        "retrieval_set_id": retrieval_set_id,
        "header_path": header_path,
        "page_span": {"start_page": page_span.get("start_page"), "end_page": page_span.get("end_page")},
        "offsets": {"start_offset": offsets.get("start_offset"), "end_offset": offsets.get("end_offset")},
        "bbox": bbox,
        "confidence": _safe_float(confidence),
        "confidence_label": confidence_label(confidence),
        "extraction_warnings": extraction_warnings or [],
    }


def topic_node_contract(
    *,
    root_topic_id: str,
    root_label_he: str,
    child_topic_id_value: str | None,
    child_label_he: str | None,
    status: str,
    confidence: Any,
    evidence_refs: list[dict[str, Any]],
    decision_count: int = 0,
    recent_activity_at: str | None = None,
) -> dict[str, Any]:
    node_id = child_topic_id_value or root_topic_id
    label = child_label_he or root_label_he
    depth = 1 if child_topic_id_value else 0
    return {
        "id": node_id,
        "parent_id": root_topic_id if child_topic_id_value else None,
        "child_ids": [],
        "sibling_ids": [],
        "path_ids": [root_topic_id, child_topic_id_value] if child_topic_id_value else [root_topic_id],
        "depth": depth,
        "sort_order": 0,
        "generated_label": label,
        "curated_label": None,
        "origin": V4_PROVENANCE,
        "curation_status": "candidate" if status == "candidate" else "approved" if status == "active" else "rejected",
        "merged_from_topic_ids": [],
        "split_from_topic_id": None,
        "hidden_from_public": status != "active",
        "category_ids": [root_topic_id],
        "primary_category_id": root_topic_id,
        "mention_count": 1,
        "decision_count": max(0, int(decision_count)),
        "recent_activity_at": recent_activity_at,
        "time_rollup": {},
        "decision_rollups": {},
        "evidence_refs": evidence_refs,
        "confidence": _safe_float(confidence),
    }


def decision_contract_fields(
    *,
    text: str,
    topic_root_id: str,
    topic_child_id: str | None,
    evidence_refs: list[dict[str, Any]],
    evidence_span_ids: list[str],
    confidence: Any,
    entity_ids: list[str] | None = None,
) -> dict[str, Any]:
    compact = " ".join(str(text or "").split())
    is_decision = _looks_decision_like(compact)
    decision_id = None
    if is_decision:
        decision_id = "dc_" + hashlib.sha1(f"{TOPIC_TREE_VERSION}|{topic_root_id}|{topic_child_id or ''}|{compact[:240]}".encode("utf-8")).hexdigest()[:16]
    return {
        "decision_candidate_id": decision_id,
        "decision_type": "municipal_action" if is_decision else None,
        "decision_kind": "candidate",
        "decision_outcome_type": "approved" if is_decision and any(term in normalize_for_search(compact) for term in ["מאשרים", "הוחלט", "פה אחד", "ברוב קולות"]) else "unknown" if is_decision else None,
        "outcome_status": "candidate" if is_decision else None,
        "legal_effect": "unknown" if is_decision else None,
        "primary_time": None,
        "time_anchors": [],
        "decision_summary_he": compact[:240] if is_decision else None,
        "decision_text_he": compact if is_decision else None,
        "raw_decision_text": compact if is_decision else None,
        "decision_evidence_span_ids": evidence_span_ids if is_decision else [],
        "decision_supporting_quote_he": compact[:500] if is_decision else None,
        "decision_citation_chunk_ids": [],
        "decision_confidence": _safe_float(confidence) if is_decision else None,
        "decision_topic_root_id": topic_root_id if is_decision else None,
        "decision_topic_child_id": topic_child_id if is_decision else None,
        "normalized_by": "pdf_first_v4_deterministic_contract" if is_decision else None,
        "curation_status": "candidate" if is_decision else None,
        "topic_ids": [value for value in [topic_root_id, topic_child_id] if value] if is_decision else [],
        "entity_ids": entity_ids or [],
        "timeline_event_ids": [],
        "evidence_refs": evidence_refs if is_decision else [],
    }


def missing_contract_counts(chunks: list[dict[str, Any]]) -> dict[str, int]:
    topic_required = [
        "topic_tree_version",
        "topic_assignment_backend",
        "root_topic_id",
        "root_label_he",
        "topic_node_status",
        "topic_assignment_route",
        "topic_assignment_confidence",
        "topic_evidence_span_ids",
        "topic_supporting_quote_he",
        "source_region_ids",
        "source_block_ids",
        "source_page",
    ]
    evidence_required = [
        "evidence_span_id",
        "artifact_id",
        "document_version_id",
        "source_kind",
        "page",
        "source_region_ids",
        "source_block_ids",
        "quote_he",
        "evidence_role",
        "confidence",
    ]
    decision_required = [
        "decision_candidate_id",
        "decision_type",
        "decision_outcome_type",
        "decision_summary_he",
        "decision_text_he",
        "decision_evidence_span_ids",
        "decision_supporting_quote_he",
        "decision_citation_chunk_ids",
        "decision_confidence",
        "decision_topic_root_id",
    ]
    missing_topic = 0
    missing_evidence = 0
    missing_decision = 0
    for chunk in chunks:
        if any(_empty_contract_value(chunk.get(key)) for key in topic_required):
            missing_topic += 1
        evidence = chunk.get("evidence_contract")
        if not isinstance(evidence, dict) or any(_empty_contract_value(evidence.get(key)) for key in evidence_required):
            missing_evidence += 1
        is_decision_like = bool(chunk.get("decision_candidate_id")) or _looks_decision_like(chunk.get("raw_text") or chunk.get("chunk_text") or "")
        if is_decision_like and not chunk.get("decision_reject_reason"):
            missing_required = any(_empty_contract_value(chunk.get(key)) for key in decision_required)
            has_child_topic = not _empty_contract_value(chunk.get("child_topic_id")) or not _empty_contract_value(chunk.get("child_label_he"))
            missing_child_link = has_child_topic and _empty_contract_value(chunk.get("decision_topic_child_id"))
            if missing_required or missing_child_link:
                missing_decision += 1
    return {
        "missing_topic_contract_count": missing_topic,
        "missing_evidence_contract_count": missing_evidence,
        "missing_decision_contract_count": missing_decision,
    }


def compact_label(value: Any) -> str:
    return " ".join(str(value or "").replace("‫", " ").replace("‬", " ").split()).strip()


def _dedupe_labels(values: list[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        label = compact_label(value)
        if not label:
            continue
        key = normalize_for_search(label)
        if key in seen:
            continue
        seen.add(key)
        out.append(label)
    return out


def clean_topic_label(value: Any) -> str | None:
    label = compact_label(value)
    if not label or label.casefold() in {"none", "null"}:
        return None
    label = re.sub(r"^(?:הנדון|נדון|בנושא|נושא)\s*[:\-–]?\s*", "", label).strip()
    label = re.sub(r"^שאיל(?:תא|תה)\s+(?:רקע\s+)?", "", label).strip()
    label = re.sub(r"^סעיף\s*\d+(?:\.\d+)?\s*[:.)-]*\s*", "", label).strip()
    label = re.sub(r"^[\d\s'.:()\-–]+", "", label).strip()
    label = re.sub(r"^פרוטוקול\s+מישיבת\s+", "", label).strip()
    label = re.sub(r"^מישיבת\s+", "", label).strip()
    label = re.sub(r"^פרוטוקול\s+", "", label).strip()
    label = re.sub(r"^אישור\s+(?=הסכם\b)", "", label).strip()
    label = re.sub(r"^שאיל(?:תא|תה)\s+(?:של\s+[^\n]{2,80}?\s+)?בנושא\s+", "", label).strip()
    label = re.sub(r"^הצעה\s+לסדר\s*[-:]*\s*", "", label).strip()
    label = re.sub(r"^נושא\s+לדיון\s*[-:]*\s*", "", label).strip()
    label = re.split(r"\s+מחליטים\b|\s+מאשרים\b|\s+הוחלט\b|\s+הצביעו\b", label, maxsplit=1)[0].strip()
    label = re.split(r"\d{1,2}\.\d{1,2}(?:\.\d{2,4})?", label, maxsplit=1)[0].strip()
    label = re.split(r"\s+מס\s*\d|\s+מספר\s*\d|\s+מיום\s*\d|\s+מתאריך\s*\d", label, maxsplit=1)[0].strip()
    label = re.split(r"\s+[–-]\s+מצ[\"'״]?ל", label, maxsplit=1)[0].strip()
    label = re.split(r"\s+[–-]\s+", label, maxsplit=1)[0].strip()
    label = re.sub(r"\b(?:גוש|חלקה|מגרש)\b.*$", "", label).strip()
    label = re.sub(r"\b(?:תנועת\s+אשדודים|אין\s+לנו\s+עוד\s+אשדוד)\b.*$", "", label).strip()
    label = re.sub(r"\b(?:בסך\s*)?\d+\s*(?:מיליון|מל(?:יון)?|שח|ש\"ח|₪)\b", "", label).strip()
    label = re.sub(r"\b(?:שח|ש\"ח|₪)\b", "", label).strip()
    label = re.sub(r"\d+$", "", label).strip()
    label = label.strip(" .,:;()[]{}\"'׳״-–*")
    label = re.sub(r"\s+", " ", label).strip()
    label = canonical_child_label(label)
    return label or None


def canonical_child_label(value: str | None, *, evidence_text: str = "") -> str | None:
    label = compact_label(value)
    normalized = normalize_for_search(" ".join([label, evidence_text]))
    label_norm = normalize_for_search(label)
    if not label:
        return None
    if any(term in normalized for term in ["מקור לכיסוי", "כיסוי גרעון", "כיסוי גירעון", "גרעון", "גירעון"]) and any(term in normalized for term in ["תחבורה", "תחבורה ציבורית"]):
        return "מימון פרויקט תחבורה ציבורית"
    if "גללי כלבים" in normalized:
        return "ניקיון ואכיפה סביבתית במרחב הציבורי"
    if "ועדת ביקורת" in label_norm:
        return "ועדת ביקורת"
    if "ועדת מלגות" in label_norm or label_norm.startswith("מלגות"):
        return "ועדת מלגות"
    if "ועדת מל ח" in label_norm or "ועדת מלח" in label_norm or "ועדת מל\"ח" in label_norm:
        return "ועדת מל\"ח"
    if "ועדת משנה לתמיכות" in label_norm or "ועדת משנה תמיכות" in label_norm:
        return "ועדת משנה לתמיכות"
    if "שימוש ארעי במגרשים ריקים" in normalized:
        return "שימוש ארעי במגרשים ריקים"
    if "האצלת סמכויות" in label_norm:
        return "האצלת סמכויות חתימה"
    if "למידה חדשנית" in normalized and any(term in normalized for term in ["סמינר", "נסיעה"]):
        return "סמינר למידה חדשנית"
    if "עתיד בית ספר יד שבתאי" in normalized:
        return "עתיד בית ספר יד שבתאי"
    if "העתקת" in normalized and "אלתא" in normalized:
        return "העתקת פעילות מפעל אלתא"
    return label


def procedural_child_label_reason(label: str | None, *, evidence_text: str = "") -> str | None:
    label_norm = normalize_for_search(label or "")
    evidence_norm = normalize_for_search(evidence_text or "")
    if not label_norm:
        return "empty_label"
    if label_norm in {
        normalize_for_search("ועדת משנה לתמיכות"),
        normalize_for_search("ועדת המשנה להקצאות קרקע"),
        normalize_for_search("ועדת ביקורת"),
        normalize_for_search("ועדת מל\"ח"),
        normalize_for_search("ועדת מלגות"),
    }:
        return "procedural_committee_label"
    if re.fullmatch(r"ועדת\s+[^\s]+(?:\s+[^\s]+){0,3}", label_norm):
        return "procedural_committee_label"
    if any(term in label_norm for term in ["פרוטוקול ועדת", "פרוטוקול מישיבת ועדת"]):
        return "procedural_protocol_label"
    if label_norm.startswith("ועדת ") and any(term in evidence_norm for term in ["פרוטוקול", "מישיבת", "מס", "מיום", "מצ ל", "מצל"]):
        return "procedural_committee_context"
    return None


def unsupported_child_domain_reason(label: str | None, *, evidence_text: str = "") -> str | None:
    normalized = normalize_for_search(" ".join([str(label or ""), str(evidence_text or "")]))
    if any(term in normalized for term in ["מקור לכיסוי", "כיסוי גרעון", "כיסוי גירעון", "גרעון", "גירעון"]) and not any(term in normalized for term in ["תחבורה", "תחבורה ציבורית", "משרד התחבורה"]):
        return "unsupported_semantic_domain"
    if "אלתא" in normalized:
        return "unsupported_semantic_domain"
    return None


def noisy_label_reason(value: str | None) -> str | None:
    label = compact_label(value)
    normalized = normalize_for_search(label)
    if not label:
        return "empty_label"
    if normalized == normalize_for_search("נושא כללי"):
        return "general_topic_forbidden"
    if label in STRUCTURAL_ROLE_LABELS or normalized in {normalize_for_search(item) for item in STRUCTURAL_ROLE_LABELS}:
        return "structural_role_label"
    if re.fullmatch(r"[\d\s./:;,'\"()\-–]+", label):
        return "mostly_numbers"
    if len(re.findall(r"\d", label)) > max(3, len(label) // 4):
        return "mostly_numbers_dates_or_pages"
    if re.fullmatch(r"סעיף\s*\d+(?:\.\d+)?", normalized):
        return "section_only"
    if len(label) > 90 or len(label.split()) > 10:
        return "overlong_sentence_fragment"
    if "משא ומתן" in normalized and any(term in normalized for term in ["להיכנס", "כנס", "לתקן"]):
        return "verb_phrase_label"
    if any(term in normalized for term in ["שנוגע", "שנוגעת", "שקשור", "שקשורה"]):
        return "clause_fragment_label"
    procedural_reason = procedural_child_label_reason(label)
    if procedural_reason:
        return procedural_reason
    if _looks_like_person_only_label(label):
        return "person_only_label"
    if _looks_like_clause_fragment(label):
        return "clause_fragment_label"
    if _looks_like_dialogue_fragment(label):
        return "dialogue_fragment_label"
    if _looks_like_procedural_fragment(label):
        return "procedural_fragment_label"
    if any(term in normalized for term in ["עמוד", "מיום", "מתאריך", "תחילת תוקף", "תוכן ההחלטה", "נושא ההחלטה", "מכותבים"]):
        return "metadata_or_page_reference"
    if _has_repeated_phrase(label):
        return "ocr_repeated_phrase"
    if _looks_like_broken_ocr(label):
        return "ocr_garbage_broken_tokens"
    if any(term in normalized for term in ["מחליטים", "מאשרים", "הוחלט"]):
        return "decision_prose_in_label"
    return None


def strip_decision_prose(value: str) -> str | None:
    label = re.split(r"\s+מחליטים\b|\s+מאשרים\b|\s+הוחלט\b", compact_label(value), maxsplit=1)[0].strip()
    return clean_topic_label(label)


def evidence_supports_label(label: str, evidence_text: str) -> bool:
    label_norm = normalize_for_search(label)
    evidence_norm = normalize_for_search(evidence_text)
    if not label_norm or not evidence_norm:
        return False
    if label_norm == normalize_for_search("ניקיון ואכיפה סביבתית במרחב הציבורי") and any(term in evidence_norm for term in ["גללי כלבים", "ניקיון העיר", "אכיפה", "סכנה תברואתית", "פגיעה סביבתית"]):
        return True
    if label_norm in evidence_norm:
        return True
    label_tokens = [token for token in re.findall(r"[\u0590-\u05FF]{2,}", label_norm) if len(token) >= 2]
    if not label_tokens:
        return False
    hit_count = sum(1 for token in label_tokens if token in evidence_norm)
    return hit_count >= min(len(label_tokens), max(2, len(label_tokens) - 1))


def referenced_attachment_contexts(*, text: str, attachment_contexts: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    if not attachment_contexts:
        return []
    normalized_text = normalize_for_search(text)
    scored: list[tuple[float, dict[str, Any]]] = []
    local_subject, _route = derive_child_candidate(text=text)
    local_tokens = set(_topic_tokens(local_subject or ""))
    for item in attachment_contexts:
        label_values = [
            item.get("pdf_path"),
            item.get("file_stem"),
            item.get("root_label_he"),
            item.get("child_label_he"),
            item.get("topic_supporting_quote_he"),
        ]
        haystack = normalize_for_search(" ".join(str(value or "") for value in label_values))
        if not haystack:
            continue
        score = 0.0
        if "מצ ל" in normalized_text or "מצל" in normalized_text:
            score += 0.15
        for token in local_tokens:
            if token in haystack:
                score += 0.22
        child_norm = normalize_for_search(str(item.get("child_label_he") or ""))
        if child_norm and (child_norm in normalized_text or normalized_text in child_norm):
            score += 0.65
        root_norm = normalize_for_search(str(item.get("root_label_he") or ""))
        if root_norm and root_norm in normalized_text:
            score += 0.18
        if score >= 0.65:
            payload = dict(item)
            payload["attachment_match_score"] = round(min(1.0, score), 4)
            scored.append((score, payload))
    scored.sort(key=lambda row: row[0], reverse=True)
    return [payload for _score, payload in scored[:limit]]


def attachment_contexts_from_retrieval_chunks(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    rows = []
    for chunk in payload.get("retrieval_chunks") or []:
        if not isinstance(chunk, dict):
            continue
        if chunk.get("topic_node_status") == "rejected":
            continue
        rows.append(
            {
                "source_chunk_id": chunk.get("chunk_id"),
                "pdf_path": payload.get("input_pdf") or payload.get("pdf_path"),
                "file_stem": Path(str(payload.get("input_pdf") or payload.get("pdf_path") or "")).stem,
                "root_topic_id": chunk.get("root_topic_id"),
                "root_label_he": chunk.get("root_label_he"),
                "child_topic_id": chunk.get("child_topic_id"),
                "child_label_he": chunk.get("child_label_he"),
                "topic_node_status": chunk.get("topic_node_status"),
                "topic_supporting_quote_he": chunk.get("topic_supporting_quote_he"),
                "source_page": chunk.get("source_page"),
            }
        )
    return rows


def persist_v4_semantic_links(
    session: Session,
    *,
    source_site_id: int,
    artifact: RetrievalArtifact,
    chunk: dict[str, Any],
    semantic_run: SemanticDocumentRun,
) -> V4NodeWriteResult | None:
    root_topic_id = str(chunk.get("root_topic_id") or "")
    root_label = str(chunk.get("root_label_he") or root_label_for_id(root_topic_id) or "").strip()
    if not root_topic_id or not root_label:
        return None
    status = str(chunk.get("topic_node_status") or "active").strip().casefold()
    if status not in {"active", "candidate", "rejected"}:
        status = "candidate"
    root_node = upsert_semantic_node(
        session,
        source_site_id=source_site_id,
        label=root_label,
        root_topic_id=root_topic_id,
        semantic_type="pdf_first_v4_topic_root",
        parent_node=None,
        status="active",
        support_count=1,
        confidence=float(chunk.get("topic_assignment_confidence") or 0.8),
        document_version_id=int(artifact.document_version_id),
        metadata_extra={"root_topic_id": root_topic_id},
    )
    child_label = str(chunk.get("child_label_he") or "").strip()
    child_status = status if child_label else "active"
    child_node: SemanticNode | None = None
    if child_label and child_status != "rejected":
        child_node = upsert_semantic_node(
            session,
            source_site_id=source_site_id,
            label=child_label,
            root_topic_id=root_topic_id,
            child_topic_id=str(chunk.get("child_topic_id") or child_topic_id(root_topic_id, child_label)),
            semantic_type="pdf_first_v4_topic_child",
            parent_node=root_node,
            status=child_status,
            support_count=1,
            confidence=float(chunk.get("topic_assignment_confidence") or 0.7),
            document_version_id=int(artifact.document_version_id),
            metadata_extra={"root_topic_id": root_topic_id, "topic_tree_version": TOPIC_TREE_VERSION},
        )
        for alias in chunk.get("topic_aliases_he") or []:
            upsert_alias(session, semantic_node=child_node, alias_label=str(alias), document_version_id=int(artifact.document_version_id))
    elif child_label and child_status == "rejected":
        add_candidate_reject(session, semantic_run=semantic_run, label=child_label, reason=str(chunk.get("topic_reject_reason") or "rejected_child"), metadata=chunk)
    linked_node = child_node if child_node is not None and child_node.status == "active" else root_node
    link_metadata = {
        "provenance": V4_PROVENANCE,
        "backend_version": BACKEND_VERSION,
        "topic_tree_version": TOPIC_TREE_VERSION,
        "root_topic_id": root_topic_id,
        "child_topic_id": chunk.get("child_topic_id"),
        "topic_node_status": status,
        "route": chunk.get("topic_assignment_route"),
        "evidence_span_ids": chunk.get("topic_evidence_span_ids") or [],
    }
    upsert_artifact_link(session, artifact_id=str(artifact.artifact_id), semantic_node=linked_node, confidence=float(chunk.get("topic_assignment_confidence") or 0.7), metadata=link_metadata)
    return V4NodeWriteResult(root_node_id=int(root_node.id), child_node_id=int(child_node.id) if child_node is not None else None, linked_node_id=int(linked_node.id), status=status)


def upsert_semantic_node(
    session: Session,
    *,
    source_site_id: int,
    label: str,
    root_topic_id: str,
    semantic_type: str,
    parent_node: SemanticNode | None,
    status: str,
    support_count: int,
    confidence: float,
    document_version_id: int | None,
    child_topic_id: str | None = None,
    metadata_extra: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> SemanticNode:
    now = now or datetime.utcnow()
    label_norm = normalize_for_search(label)
    parent_norm = normalize_for_search(parent_node.pref_label_he) if parent_node is not None else ""
    node_hash = hashlib.sha1(f"{TOPIC_TREE_VERSION}|{semantic_type}|{root_topic_id}|{child_topic_id or ''}|{parent_norm}|{label_norm}".encode("utf-8")).hexdigest()
    row = session.execute(select(SemanticNode).where(SemanticNode.source_site_id == source_site_id, SemanticNode.node_key_hash == node_hash)).scalar_one_or_none()
    metadata = {
        "provenance": V4_PROVENANCE,
        "backend_version": BACKEND_VERSION,
        "topic_tree_version": TOPIC_TREE_VERSION,
        "root_topic_id": root_topic_id,
        "child_topic_id": child_topic_id,
        **(metadata_extra or {}),
    }
    if row is None:
        row = SemanticNode(
            source_site_id=source_site_id,
            node_key_hash=node_hash,
            node_kind="topic",
            semantic_type=semantic_type,
            pref_label_he=label,
            pref_label_norm=label_norm,
            parent_node_id=parent_node.id if parent_node is not None else None,
            depth=1 if parent_node is not None else 0,
            specificity_score=0.78 if parent_node is not None else 0.52,
            confidence=max(0.0, min(1.0, confidence)),
            support_count=max(0, int(support_count)),
            status=status,
            first_seen_document_version_id=document_version_id,
            last_seen_document_version_id=document_version_id,
            metadata_json=json.dumps(metadata, ensure_ascii=False),
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.flush()
        return row
    row.pref_label_he = label
    row.pref_label_norm = label_norm
    row.node_kind = "topic"
    row.semantic_type = semantic_type
    row.parent_node_id = parent_node.id if parent_node is not None else None
    row.depth = 1 if parent_node is not None else 0
    row.specificity_score = max(float(row.specificity_score or 0.0), 0.78 if parent_node is not None else 0.52)
    row.confidence = max(float(row.confidence or 0.0), max(0.0, min(1.0, confidence)))
    row.support_count = int(row.support_count or 0) + max(0, int(support_count))
    if row.status != "active":
        row.status = status
    elif status == "active":
        row.status = "active"
    row.last_seen_document_version_id = document_version_id or row.last_seen_document_version_id
    row.metadata_json = json.dumps({**_loads_dict(row.metadata_json), **metadata}, ensure_ascii=False)
    row.updated_at = now
    session.flush()
    return row


def upsert_alias(session: Session, *, semantic_node: SemanticNode, alias_label: str, document_version_id: int | None) -> None:
    raw_alias = compact_label(alias_label)
    cleaned_alias = clean_topic_label(raw_alias)
    alias = cleaned_alias if cleaned_alias and normalize_for_search(cleaned_alias) != normalize_for_search(semantic_node.pref_label_he) else raw_alias
    if not alias or normalize_for_search(alias) == normalize_for_search(semantic_node.pref_label_he):
        return
    if noisy_label_reason(alias) or is_low_quality_topic_label(alias):
        return
    alias_norm = normalize_for_search(alias)
    alias_hash = hashlib.sha1(f"{semantic_node.id}|{alias_norm}".encode("utf-8")).hexdigest()
    existing = session.execute(select(SemanticAlias).where(SemanticAlias.semantic_node_id == semantic_node.id, SemanticAlias.alias_hash == alias_hash)).scalar_one_or_none()
    metadata = json.dumps({"provenance": V4_PROVENANCE, "backend_version": BACKEND_VERSION, "topic_tree_version": TOPIC_TREE_VERSION}, ensure_ascii=False)
    if existing is None:
        session.add(
            SemanticAlias(
                semantic_node_id=semantic_node.id,
                alias_hash=alias_hash,
                alias_label_he=alias,
                alias_label_norm=alias_norm,
                alias_kind="v4_alias",
                confidence=0.76,
                first_seen_document_version_id=document_version_id,
                last_seen_document_version_id=document_version_id,
                metadata_json=metadata,
            )
        )
    else:
        existing.alias_label_he = alias
        existing.alias_label_norm = alias_norm
        existing.confidence = max(float(existing.confidence or 0.0), 0.76)
        existing.last_seen_document_version_id = document_version_id or existing.last_seen_document_version_id
        existing.metadata_json = metadata


def upsert_artifact_link(session: Session, *, artifact_id: str, semantic_node: SemanticNode, confidence: float, metadata: dict[str, Any]) -> None:
    existing = session.execute(select(ArtifactSemanticLink).where(ArtifactSemanticLink.artifact_id == artifact_id, ArtifactSemanticLink.semantic_node_id == semantic_node.id)).scalar_one_or_none()
    metadata_json = json.dumps(metadata, ensure_ascii=False)
    if existing is None:
        session.add(
            ArtifactSemanticLink(
                artifact_id=artifact_id,
                semantic_node_id=semantic_node.id,
                confidence=max(0.0, min(1.0, confidence)),
                source_mention_id=None,
                metadata_json=metadata_json,
            )
        )
    else:
        existing.confidence = max(float(existing.confidence or 0.0), max(0.0, min(1.0, confidence)))
        existing.metadata_json = metadata_json


def semantic_node_root_id(node: SemanticNode) -> str | None:
    metadata = _loads_dict(node.metadata_json)
    root_id = str(metadata.get("root_topic_id") or "").strip()
    return root_id or root_id_for_label(node.pref_label_he)


def semantic_node_child_id(node: SemanticNode) -> str | None:
    metadata = _loads_dict(node.metadata_json)
    child_id = str(metadata.get("child_topic_id") or "").strip()
    return child_id or None


def add_candidate_reject(session: Session, *, semantic_run: SemanticDocumentRun, label: str | None, reason: str, metadata: dict[str, Any]) -> None:
    session.add(
        SemanticCandidateReject(
            semantic_document_run_id=semantic_run.id,
            candidate_label_he=label,
            candidate_label_norm=normalize_for_search(label or "") or None,
            node_kind="topic",
            semantic_type="pdf_first_v4_topic_child",
            reason_code=reason[:64] or "rejected",
            model_confidence=_safe_float(metadata.get("topic_assignment_confidence")),
            metadata_json=json.dumps({"provenance": V4_PROVENANCE, "backend_version": BACKEND_VERSION, **metadata}, ensure_ascii=False)[:20000],
        )
    )


def _children_by_root_from_tree(tree: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for root in tree.get("roots") or tree.get("root_topics") or []:
        root_id = str(root.get("root_topic_id") or root.get("root_id") or "")
        if not root_id:
            continue
        out[root_id] = [dict(child) for child in root.get("children") or []]
    return out


def _roots_by_id_from_tree(tree: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for root in tree.get("roots") or tree.get("root_topics") or []:
        root_id = str(root.get("root_topic_id") or root.get("root_id") or "")
        if root_id:
            out[root_id] = dict(root)
    return out


def _has_repeated_phrase(label: str) -> bool:
    tokens = label.split()
    for size in (1, 2, 3):
        if len(tokens) < size * 2:
            continue
        for index in range(0, len(tokens) - size):
            phrase = tokens[index : index + size]
            if phrase == tokens[index + size : index + (size * 2)]:
                return True
    return False


def _looks_like_broken_ocr(label: str) -> bool:
    tokens = label.split()
    if any(len(token) == 1 for token in tokens):
        return True
    punctuation = sum(1 for ch in label if ch in "'\"().,:;-/")
    if punctuation > max(3, len(label) // 5):
        return True
    hebrew_tokens = re.findall(r"[\u0590-\u05FF]{2,}", label)
    return len(hebrew_tokens) < 2 and normalize_for_search(label) not in {normalize_for_search("חינוך"), normalize_for_search("תמיכות")}


def _looks_like_clause_fragment(label: str) -> bool:
    normalized = normalize_for_search(label)
    first = (normalized.split() or [""])[0]
    if first in {"זה", "הזה", "זו", "זאת", "כדי", "ככל", "כאשר", "אם", "ש", "של", "את", "המחייב", "שמפריע"}:
        return True
    if any(term in normalized for term in ["כדי לבחון", "הוא יטופל", "מבוצעת בשוטף", "לאורך כל השנה", "אני מצוי", "אני מאציל את סמכויותיי"]):
        return True
    if re.search(r"\b(?:כדי|ככל|כאשר|לפיכך|ולפיכך)\b", normalized) and "," in label:
        return True
    return False


def _looks_like_dialogue_fragment(label: str) -> bool:
    if re.search(r":[\s\u200e\u200f]*(?:עו[\"'״]?ד|מר|גב[\'׳]?|ראש|מנכ[\"'״]?ל)\b", label):
        return True
    normalized = normalize_for_search(label)
    return any(term in normalized for term in ["בבקשה מר", "בבקשה עוד", "כנס לפרוטוקול", "קורא אותך לסדר"])


def _looks_like_procedural_fragment(label: str) -> bool:
    normalized = normalize_for_search(label)
    return any(
        term in normalized
        for term in [
            "נוכחים",
            "חברי האופוזיציה עזבו",
            "שישה חברי האופוזיציה",
            "מי שבעד",
            "מי נמנע",
            "מי נגד",
            "ללא שישה חברי האופוזיציה",
        ]
    )


def _looks_like_person_only_label(label: str) -> bool:
    normalized = normalize_for_search(label)
    tokens = normalized.split()
    if len(tokens) <= 3 and tokens and tokens[0] in {"מר", "גברת", "גב", "עוד", "דוקטור"}:
        return True
    return False


def _semantic_root_override_is_strong(label: str, evidence_text: str) -> bool:
    normalized = normalize_for_search(" ".join([label, evidence_text]))
    return any(
        term in normalized
        for term in [
            "תחבורה ציבורית",
            "פרויקט תחבורה",
            "גללי כלבים",
            "ועדת מל ח",
            "ועדת מל\"ח",
            "ועדת מלח",
            "למידה חדשנית",
            "האצלת סמכויות",
            "ועדת ביקורת",
            "מלגות",
            "הקצאות",
        ]
    )


def _looks_decision_like(value: str) -> bool:
    normalized = normalize_for_search(value)
    return any(term in normalized for term in ["מחליטים", "מאשרים", "הוחלט", "הצביעו", "פה אחד", "ברוב קולות"])


def _empty_contract_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list):
        return len(value) == 0
    return False


def _topic_tokens(value: str) -> list[str]:
    return [token for token in re.findall(r"[\u0590-\u05FF]{2,}", normalize_for_search(value)) if len(token) >= 2]


def _loads_dict(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
