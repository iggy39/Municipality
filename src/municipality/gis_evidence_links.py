from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

PLAN_NUMBER_RE = re.compile(r"(?<!\d)(\d{3,}-\d{3,})(?!\d)")


@dataclass(frozen=True)
class GisEvidenceLinkSummary:
    scanned_decisions: int
    inserted_or_updated: int
    matched_decisions: int


def extract_plan_numbers(*texts: str | None) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for text_value in texts:
        for match in PLAN_NUMBER_RE.finditer(str(text_value or "")):
            plan_number = match.group(1).strip()
            if plan_number and plan_number not in seen:
                seen.add(plan_number)
                out.append(plan_number)
    return out


def ensure_decision_gis_feature_link_table(session: Session) -> None:
    bind = session.get_bind()
    if not _has_tables(session, ["decision", "retrieval_artifact"]):
        return
    dialect = bind.dialect.name
    if inspect(bind).has_table("decision_gis_feature_link"):
        return
    statements = _postgres_link_table_sql() if dialect == "postgresql" else _sqlite_link_table_sql()
    for statement in statements:
        session.execute(text(statement))


def link_decisions_to_gis_plans(session: Session, *, limit: int | None = None) -> GisEvidenceLinkSummary:
    if not _has_tables(session, ["decision", "retrieval_artifact", "plans"]):
        return GisEvidenceLinkSummary(scanned_decisions=0, inserted_or_updated=0, matched_decisions=0)
    ensure_decision_gis_feature_link_table(session)
    if not _has_link_table(session):
        return GisEvidenceLinkSummary(scanned_decisions=0, inserted_or_updated=0, matched_decisions=0)
    decisions = _decision_rows(session, limit=limit)
    inserted = 0
    matched_decision_ids: set[int] = set()
    for decision in decisions:
        artifact_rows = _artifact_rows(session, _source_artifact_ids(decision))
        text_sources = _decision_text_sources(decision, artifact_rows)
        plan_numbers = extract_plan_numbers(*(source["text"] for source in text_sources))
        if not plan_numbers:
            continue
        plan_rows = _plan_rows(session, plan_numbers)
        for plan in plan_rows:
            source = _best_source_for_plan(plan["plan_number"], text_sources)
            _upsert_decision_plan_link(session, decision=decision, plan=plan, source=source)
            inserted += 1
            matched_decision_ids.add(int(decision["decision_id"]))
    return GisEvidenceLinkSummary(scanned_decisions=len(decisions), inserted_or_updated=inserted, matched_decisions=len(matched_decision_ids))


def decision_gis_feature_links(session: Session, *, decision_id: int) -> list[dict[str, Any]]:
    if not _has_link_table(session):
        return []
    rows = session.execute(
        text(
            """
            SELECT id, decision_id, artifact_id, feature_table, feature_id, feature_type,
                   source_id, plan_number, feature_label, link_type, match_text,
                   confidence, confidence_label, is_uncertain, metadata_json, created_at
            FROM decision_gis_feature_link
            WHERE decision_id = :decision_id
            ORDER BY confidence DESC, feature_type ASC, plan_number ASC, id ASC
            """
        ),
        {"decision_id": decision_id},
    ).mappings().all()
    return [_link_payload(row) for row in rows]


def plan_related_decisions(session: Session, *, plan_number: str) -> list[dict[str, Any]]:
    if not _has_link_table(session) or not _has_tables(session, ["decision", "meeting", "document"]):
        return []
    rows = session.execute(
        text(
            f"""
            SELECT l.id, l.decision_id, l.artifact_id, l.plan_number, l.feature_label,
                   l.link_type, l.match_text, l.confidence, l.confidence_label,
                   l.is_uncertain, l.metadata_json, d.decision_number, d.agenda_item,
                   d.decision_text, m.meeting_date, m.meeting_external_id,
                   doc.id AS document_id, doc.title_he AS document_title, doc.canonical_url AS document_url
            FROM decision_gis_feature_link l
            JOIN decision d ON d.id = l.decision_id
            JOIN meeting m ON m.id = d.meeting_id
            JOIN document doc ON doc.id = d.source_document_id
            WHERE l.feature_table = 'plans'
              AND l.plan_number = :plan_number
              AND d.is_public = {_public_true_sql(session)}
            ORDER BY l.confidence DESC, m.meeting_date DESC, d.id DESC
            """
        ),
        {"plan_number": plan_number},
    ).mappings().all()
    return [_related_decision_payload(row) for row in rows]


def _decision_rows(session: Session, *, limit: int | None) -> list[Mapping[str, Any]]:
    limit_sql = "LIMIT :limit" if limit is not None else ""
    params = {"limit": int(limit)} if limit is not None else {}
    return list(
        session.execute(
            text(
                f"""
                SELECT d.id AS decision_id, d.decision_text, d.agenda_item,
                       d.metadata_json AS decision_metadata_json,
                       rc.request_subject_he, rc.subject_topic_he, rc.address_he,
                       rc.source_artifact_ids_json, rc.metadata_json AS context_metadata_json
                FROM decision d
                LEFT JOIN decision_request_context rc ON rc.decision_id = d.id
                WHERE d.is_public = {_public_true_sql(session)}
                ORDER BY d.id ASC
                {limit_sql}
                """
            ),
            params,
        ).mappings().all()
    )


def _source_artifact_ids(decision: Mapping[str, Any]) -> list[str]:
    values = _loads_json(decision.get("source_artifact_ids_json"))
    if not isinstance(values, list):
        metadata = _loads_json(decision.get("context_metadata_json"))
        raw_values = metadata.get("source_artifact_ids") if isinstance(metadata, dict) else None
        values = raw_values if isinstance(raw_values, list) else []
    return [str(value).strip() for value in values if str(value).strip()]


def _artifact_rows(session: Session, artifact_ids: Iterable[str]) -> list[Mapping[str, Any]]:
    normalized_ids = [artifact_id for artifact_id in artifact_ids if artifact_id]
    if not normalized_ids:
        return []
    placeholders = ", ".join(f":artifact_{index}" for index, _ in enumerate(normalized_ids))
    params = {f"artifact_{index}": artifact_id for index, artifact_id in enumerate(normalized_ids)}
    return list(
        session.execute(
            text(
                f"""
                SELECT artifact_id, body_text, retrieval_text, title_he, header_path_json,
                       start_page, end_page, citation_label
                FROM retrieval_artifact
                WHERE artifact_id IN ({placeholders})
                ORDER BY ordinal ASC, artifact_id ASC
                """
            ),
            params,
        ).mappings().all()
    )


def _decision_text_sources(decision: Mapping[str, Any], artifact_rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    sources = [
        {"kind": "decision_text", "artifact_id": None, "text": decision.get("decision_text")},
        {"kind": "agenda_item", "artifact_id": None, "text": decision.get("agenda_item")},
        {"kind": "request_subject", "artifact_id": None, "text": decision.get("request_subject_he")},
        {"kind": "subject_topic", "artifact_id": None, "text": decision.get("subject_topic_he")},
        {"kind": "address", "artifact_id": None, "text": decision.get("address_he")},
    ]
    for artifact in artifact_rows:
        sources.append(
            {
                "kind": "retrieval_artifact",
                "artifact_id": artifact.get("artifact_id"),
                "text": "\n".join(
                    part for part in [artifact.get("title_he"), artifact.get("body_text"), artifact.get("retrieval_text")] if part
                ),
            }
        )
    return sources


def _plan_rows(session: Session, plan_numbers: list[str]) -> list[Mapping[str, Any]]:
    if not plan_numbers:
        return []
    placeholders = ", ".join(f":plan_{index}" for index, _ in enumerate(plan_numbers))
    params = {f"plan_{index}": plan_number for index, plan_number in enumerate(plan_numbers)}
    feature_id_sql = "CAST(id AS TEXT)" if session.get_bind().dialect.name == "sqlite" else "id::text"
    return list(
        session.execute(
            text(
                f"""
                SELECT {feature_id_sql} AS feature_id, source_id, plan_number, plan_name, metadata
                FROM plans
                WHERE plan_number IN ({placeholders})
                ORDER BY fetched_at DESC, plan_number ASC
                """
            ),
            params,
        ).mappings().all()
    )


def _best_source_for_plan(plan_number: str, sources: list[dict[str, Any]]) -> dict[str, Any]:
    for source in sources:
        if source.get("artifact_id") and plan_number in str(source.get("text") or ""):
            return source
    for source in sources:
        if plan_number in str(source.get("text") or ""):
            return source
    return {"kind": "unknown", "artifact_id": None, "text": ""}


def _upsert_decision_plan_link(session: Session, *, decision: Mapping[str, Any], plan: Mapping[str, Any], source: Mapping[str, Any]) -> None:
    plan_metadata = _loads_json(plan.get("metadata"))
    metadata = {
        "source_kind": source.get("kind"),
        "plan_metadata": plan_metadata.get("plan") if isinstance(plan_metadata, dict) else {},
    }
    params = {
        "decision_id": int(decision["decision_id"]),
        "artifact_id": source.get("artifact_id"),
        "feature_table": "plans",
        "feature_id": str(plan["feature_id"]),
        "feature_type": "plan",
        "source_id": plan.get("source_id"),
        "plan_number": plan.get("plan_number"),
        "feature_label": plan.get("plan_name") or plan.get("plan_number"),
        "link_type": "plan_number_exact",
        "match_text": plan.get("plan_number"),
        "confidence": 1.0,
        "confidence_label": "high",
        "is_uncertain": False,
        "metadata_json": json.dumps(metadata, ensure_ascii=False, default=str),
    }
    session.execute(text(_upsert_sql(session)), params)


def _upsert_sql(session: Session) -> str:
    if session.get_bind().dialect.name == "postgresql":
        return """
        INSERT INTO decision_gis_feature_link (
          decision_id, artifact_id, feature_table, feature_id, feature_type,
          source_id, plan_number, feature_label, link_type, match_text,
          confidence, confidence_label, is_uncertain, metadata_json
        ) VALUES (
          :decision_id, :artifact_id, :feature_table, :feature_id, :feature_type,
          :source_id, :plan_number, :feature_label, :link_type, :match_text,
          :confidence, :confidence_label, :is_uncertain, :metadata_json
        )
        ON CONFLICT (decision_id, feature_table, feature_id, link_type, match_text) DO UPDATE SET
          artifact_id = EXCLUDED.artifact_id,
          source_id = EXCLUDED.source_id,
          plan_number = EXCLUDED.plan_number,
          feature_label = EXCLUDED.feature_label,
          confidence = EXCLUDED.confidence,
          confidence_label = EXCLUDED.confidence_label,
          is_uncertain = EXCLUDED.is_uncertain,
          metadata_json = EXCLUDED.metadata_json,
          updated_at = now()
        """
    return """
    INSERT INTO decision_gis_feature_link (
      decision_id, artifact_id, feature_table, feature_id, feature_type,
      source_id, plan_number, feature_label, link_type, match_text,
      confidence, confidence_label, is_uncertain, metadata_json
    ) VALUES (
      :decision_id, :artifact_id, :feature_table, :feature_id, :feature_type,
      :source_id, :plan_number, :feature_label, :link_type, :match_text,
      :confidence, :confidence_label, :is_uncertain, :metadata_json
    )
    ON CONFLICT (decision_id, feature_table, feature_id, link_type, match_text) DO UPDATE SET
      artifact_id = excluded.artifact_id,
      source_id = excluded.source_id,
      plan_number = excluded.plan_number,
      feature_label = excluded.feature_label,
      confidence = excluded.confidence,
      confidence_label = excluded.confidence_label,
      is_uncertain = excluded.is_uncertain,
      metadata_json = excluded.metadata_json,
      updated_at = CURRENT_TIMESTAMP
    """


def _link_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "decision_id": int(row["decision_id"]),
        "artifact_id": row.get("artifact_id"),
        "feature_table": row.get("feature_table"),
        "feature_id": str(row.get("feature_id")),
        "feature_type": row.get("feature_type"),
        "source_id": row.get("source_id"),
        "plan_number": row.get("plan_number"),
        "feature_label": row.get("feature_label"),
        "link_type": row.get("link_type"),
        "match_text": row.get("match_text"),
        "confidence": float(row.get("confidence") or 0.0),
        "confidence_label": row.get("confidence_label"),
        "is_uncertain": bool(row.get("is_uncertain")),
        "metadata": _loads_json(row.get("metadata_json")),
    }


def _related_decision_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    artifact_id = row.get("artifact_id")
    return {
        "link_id": int(row["id"]),
        "decision_id": int(row["decision_id"]),
        "decision_number": row.get("decision_number"),
        "agenda_item": row.get("agenda_item"),
        "decision_text": row.get("decision_text"),
        "meeting_date": row.get("meeting_date"),
        "meeting_external_id": row.get("meeting_external_id"),
        "artifact_id": artifact_id,
        "evidence_ref": _artifact_evidence_id(str(artifact_id)) if artifact_id else None,
        "plan_number": row.get("plan_number"),
        "feature_label": row.get("feature_label"),
        "link_type": row.get("link_type"),
        "match_text": row.get("match_text"),
        "confidence": float(row.get("confidence") or 0.0),
        "confidence_label": row.get("confidence_label"),
        "is_uncertain": bool(row.get("is_uncertain")),
        "document": {
            "id": row.get("document_id"),
            "title": row.get("document_title"),
            "url": row.get("document_url"),
        },
        "metadata": _loads_json(row.get("metadata_json")),
    }


def _artifact_evidence_id(artifact_id: str) -> str:
    import base64

    encoded = base64.urlsafe_b64encode(artifact_id.encode("utf-8")).decode("ascii").rstrip("=")
    return f"artifact_{encoded}"


def _has_link_table(session: Session) -> bool:
    return inspect(session.get_bind()).has_table("decision_gis_feature_link")


def _has_tables(session: Session, table_names: Iterable[str]) -> bool:
    inspector = inspect(session.get_bind())
    return all(inspector.has_table(table_name) for table_name in table_names)


def _public_true_sql(session: Session) -> str:
    return "true" if session.get_bind().dialect.name == "postgresql" else "1"


def _loads_json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {}


def _sqlite_link_table_sql() -> list[str]:
    return [
        """
        CREATE TABLE IF NOT EXISTS decision_gis_feature_link (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          decision_id INTEGER NOT NULL,
          artifact_id VARCHAR(64),
          feature_table VARCHAR(64) NOT NULL,
          feature_id TEXT NOT NULL,
          feature_type VARCHAR(32) NOT NULL,
          source_id TEXT,
          plan_number TEXT,
          feature_label TEXT,
          link_type VARCHAR(32) NOT NULL,
          match_text TEXT,
          confidence REAL NOT NULL,
          confidence_label VARCHAR(32) NOT NULL,
          is_uncertain INTEGER NOT NULL DEFAULT 0,
          metadata_json TEXT,
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY (decision_id) REFERENCES decision(id),
          FOREIGN KEY (artifact_id) REFERENCES retrieval_artifact(artifact_id),
          CONSTRAINT uq_decision_gis_feature_link UNIQUE (decision_id, feature_table, feature_id, link_type, match_text)
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_decision_gis_feature_link_decision ON decision_gis_feature_link(decision_id)",
        "CREATE INDEX IF NOT EXISTS ix_decision_gis_feature_link_feature ON decision_gis_feature_link(feature_table, feature_id)",
        "CREATE INDEX IF NOT EXISTS ix_decision_gis_feature_link_plan ON decision_gis_feature_link(plan_number)",
    ]


def _postgres_link_table_sql() -> list[str]:
    return [
        """
        CREATE TABLE IF NOT EXISTS decision_gis_feature_link (
          id bigserial PRIMARY KEY,
          decision_id integer NOT NULL REFERENCES decision(id),
          artifact_id varchar(64) REFERENCES retrieval_artifact(artifact_id),
          feature_table varchar(64) NOT NULL,
          feature_id text NOT NULL,
          feature_type varchar(32) NOT NULL,
          source_id text,
          plan_number text,
          feature_label text,
          link_type varchar(32) NOT NULL,
          match_text text,
          confidence double precision NOT NULL,
          confidence_label varchar(32) NOT NULL,
          is_uncertain boolean NOT NULL DEFAULT false,
          metadata_json text,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_decision_gis_feature_link UNIQUE (decision_id, feature_table, feature_id, link_type, match_text)
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_decision_gis_feature_link_decision ON decision_gis_feature_link(decision_id)",
        "CREATE INDEX IF NOT EXISTS ix_decision_gis_feature_link_feature ON decision_gis_feature_link(feature_table, feature_id)",
        "CREATE INDEX IF NOT EXISTS ix_decision_gis_feature_link_plan ON decision_gis_feature_link(plan_number)",
    ]
