from __future__ import annotations

from html import escape
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from municipality.source_type_taxonomy import (
    PUBLIC_SOURCE_TYPE_FILTER_CODES,
    source_type_code_from_label,
    source_type_metadata,
    source_type_metadata_payload,
)


EXPLICIT_COVERAGE_TABLE = "ingestion_source_coverage"
VALID_COVERAGE_STATUSES = {"available", "missing", "unknown"}
SOURCE_TYPE_ALIASES = {
    "pdf_first_protocol": "protocol",
    "pdf_first_v4_protocol": "protocol",
    "protocol_full": "protocol",
    "protocol_document": "protocol",
    "pdf_first_attachment": "attachment",
    "pdf_first_v4_attachment": "attachment",
    "attachment_full": "attachment",
    "attachment_document": "attachment",
}
MUNICIPALITY_LABELS_HE = {
    "ashdod": "אשדוד",
    "tel_aviv": "תל אביב-יפו",
    "jerusalem": "ירושלים",
    "haifa": "חיפה",
    "beer_sheva": "באר שבע",
    "yeruham": "ירוחם",
}
STATUS_LABELS = {
    "available": {"symbol": "✓", "label_en": "Available", "label_he": "זמין"},
    "missing": {"symbol": "✕", "label_en": "Missing", "label_he": "חסר"},
    "unknown": {"symbol": "?", "label_en": "Unknown", "label_he": "לא נבדק"},
}


def canonical_source_type_code(value: str | None) -> str:
    normalized = " ".join(str(value or "").split()).strip()
    if not normalized:
        return "unknown"
    alias = SOURCE_TYPE_ALIASES.get(normalized)
    if alias is not None:
        return alias
    label_code = source_type_code_from_label(normalized)
    return label_code or normalized


def build_admin_ingestion_coverage(db: Any) -> dict[str, Any]:
    explicit_rows = _explicit_coverage_rows(db)
    available_rows = _available_coverage_rows(db)
    source_sites = _source_site_rows(db)

    explicit_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    municipalities: dict[str, dict[str, Any]] = {}
    source_type_codes = list(PUBLIC_SOURCE_TYPE_FILTER_CODES)

    for site in source_sites:
        slug = str(site.get("municipality_slug") or "").strip()
        if slug:
            municipalities.setdefault(slug, _municipality_base(slug, source_site_name=site.get("name")))

    for row in explicit_rows:
        slug = str(row.get("municipality_slug") or "").strip()
        source_type = canonical_source_type_code(str(row.get("source_type") or ""))
        if not slug:
            continue
        municipalities.setdefault(slug, _municipality_base(slug, municipality_name_he=row.get("municipality_name_he")))
        if row.get("municipality_name_he"):
            municipalities[slug]["municipality_name_he"] = str(row.get("municipality_name_he"))
        if source_type not in source_type_codes:
            source_type_codes.append(source_type)
        explicit_by_key[(slug, source_type)] = {**row, "source_type": source_type}

    available_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in available_rows:
        slug = str(row.get("municipality_slug") or "").strip()
        source_type = canonical_source_type_code(str(row.get("source_type") or ""))
        if not slug:
            continue
        municipalities.setdefault(slug, _municipality_base(slug, source_site_name=row.get("source_site_name")))
        if source_type not in source_type_codes:
            source_type_codes.append(source_type)
        key = (slug, source_type)
        current = available_by_key.setdefault(
            key,
            {
                "document_count": 0,
                "document_version_count": 0,
                "artifact_count": 0,
                "last_ingested_at": None,
            },
        )
        current["document_count"] += int(row.get("document_count") or 0)
        current["document_version_count"] += int(row.get("document_version_count") or 0)
        current["artifact_count"] += int(row.get("artifact_count") or 0)
        current["last_ingested_at"] = _latest_value(current.get("last_ingested_at"), row.get("last_ingested_at"))

    ordered_source_types = _ordered_source_type_codes(source_type_codes)
    municipality_payloads = []
    for slug in sorted(municipalities):
        rows = []
        summary = {"available": 0, "missing": 0, "unknown": 0}
        for source_type in ordered_source_types:
            explicit = explicit_by_key.get((slug, source_type))
            available = available_by_key.get((slug, source_type))
            coverage_row = _coverage_row(source_type=source_type, explicit=explicit, available=available)
            summary[coverage_row["status"]] += 1
            rows.append(coverage_row)
        municipality_payloads.append(
            {
                **municipalities[slug],
                "summary": {
                    **summary,
                    "source_type_count": len(ordered_source_types),
                    "readiness": _readiness_payload(summary),
                },
                "source_types": rows,
            }
        )

    total_summary = {"available": 0, "missing": 0, "unknown": 0}
    for municipality in municipality_payloads:
        for status in total_summary:
            total_summary[status] += int(municipality["summary"].get(status) or 0)

    return {
        "status_labels": STATUS_LABELS,
        "source_type_options": [source_type_metadata_payload(code) for code in ordered_source_types],
        "summary": {
            **total_summary,
            "municipality_count": len(municipality_payloads),
            "source_type_count": len(ordered_source_types),
            "cell_count": len(municipality_payloads) * len(ordered_source_types),
        },
        "municipalities": municipality_payloads,
    }


def render_admin_ingestion_coverage_page(payload: dict[str, Any]) -> str:
    municipality_cards = "\n".join(_municipality_card(municipality) for municipality in payload.get("municipalities") or [])
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    generated_at = _escape(_now_label(summary))
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Admin ingestion coverage</title>
  <style>
    :root {{
      --ink: #17211b;
      --paper: #f4efe3;
      --paper-strong: #fffaf0;
      --grid: rgba(23, 33, 27, 0.16);
      --muted: #6f766b;
      --available: #126c45;
      --missing: #a33b2b;
      --unknown: #7b6b43;
      --shadow: 0 20px 60px rgba(35, 32, 24, 0.12);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background:
        linear-gradient(90deg, rgba(23,33,27,0.055) 1px, transparent 1px) 0 0 / 34px 34px,
        linear-gradient(0deg, rgba(23,33,27,0.045) 1px, transparent 1px) 0 0 / 34px 34px,
        radial-gradient(circle at 10% -10%, rgba(18,108,69,0.18), transparent 38rem),
        var(--paper);
      color: var(--ink);
      font-family: "Avenir Next", "Noto Sans Hebrew", "Helvetica Neue", sans-serif;
      line-height: 1.45;
    }}
    a {{ color: inherit; }}
    .shell {{ width: min(1380px, calc(100% - 32px)); margin: 0 auto; padding: 34px 0 54px; }}
    .hero {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 28px;
      align-items: end;
      border: 1px solid var(--grid);
      background: rgba(255, 250, 240, 0.78);
      box-shadow: var(--shadow);
      padding: clamp(24px, 4vw, 48px);
      position: relative;
      overflow: hidden;
    }}
    .hero::before {{
      content: "";
      position: absolute;
      inset: 14px;
      border: 1px dashed rgba(23, 33, 27, 0.18);
      pointer-events: none;
    }}
    .eyebrow {{ margin: 0 0 12px; color: var(--muted); text-transform: uppercase; letter-spacing: .16em; font-size: 12px; font-weight: 800; }}
    h1 {{ margin: 0; max-width: 780px; font-family: "Iowan Old Style", "Noto Serif Hebrew", Georgia, serif; font-size: clamp(36px, 6vw, 76px); line-height: .95; letter-spacing: -.045em; }}
    .heroText {{ position: relative; z-index: 1; }}
    .heroText p {{ max-width: 760px; margin: 18px 0 0; color: #3f493f; font-size: 17px; }}
    .actions {{ display: flex; gap: 10px; flex-wrap: wrap; justify-content: flex-end; position: relative; z-index: 1; }}
    .button {{ border: 1px solid var(--ink); background: var(--ink); color: var(--paper-strong); padding: 11px 14px; text-decoration: none; font-weight: 800; }}
    .button.secondary {{ background: transparent; color: var(--ink); }}
    .stats {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin: 18px 0 22px; }}
    .stat {{ border: 1px solid var(--grid); background: rgba(255, 250, 240, .72); padding: 16px; }}
    .stat strong {{ display: block; font-family: "Iowan Old Style", Georgia, serif; font-size: 34px; line-height: 1; }}
    .stat span {{ color: var(--muted); font-size: 12px; letter-spacing: .08em; text-transform: uppercase; font-weight: 800; }}
    .legend {{ display: flex; gap: 10px; flex-wrap: wrap; margin: 0 0 26px; color: #39433a; }}
    .legend .statusPill {{ margin-inline-end: 4px; }}
    .muniCard {{ background: rgba(255, 250, 240, .88); border: 1px solid var(--grid); box-shadow: 0 14px 36px rgba(35, 32, 24, .08); margin-top: 18px; }}
    .muniTop {{ display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 16px; align-items: center; padding: 18px 18px 14px; border-bottom: 1px solid var(--grid); }}
    .muniTop h2 {{ margin: 0; font-size: 24px; }}
    .slug {{ color: var(--muted); font-size: 12px; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }}
    .readiness {{ border: 1px solid var(--grid); padding: 9px 11px; background: #fffaf0; font-weight: 800; }}
    .coverageTable {{ width: 100%; border-collapse: collapse; }}
    .coverageTable th, .coverageTable td {{ padding: 10px 12px; border-bottom: 1px solid rgba(23, 33, 27, .1); text-align: left; vertical-align: top; }}
    .coverageTable th {{ font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: .09em; background: rgba(23, 33, 27, .035); }}
    .sourceHe {{ font-weight: 900; font-size: 15px; }}
    .sourceCode {{ color: var(--muted); font-size: 12px; font-family: "SFMono-Regular", ui-monospace, monospace; }}
    .statusPill {{ display: inline-flex; align-items: center; gap: 7px; border: 1px solid currentColor; padding: 4px 9px; border-radius: 999px; font-weight: 900; white-space: nowrap; }}
    .status-available {{ color: var(--available); background: rgba(18, 108, 69, .08); }}
    .status-missing {{ color: var(--missing); background: rgba(163, 59, 43, .08); }}
    .status-unknown {{ color: var(--unknown); background: rgba(123, 107, 67, .1); }}
    .metric {{ font-variant-numeric: tabular-nums; font-weight: 800; }}
    .notes {{ color: #475346; max-width: 380px; }}
    .empty {{ padding: 32px; border: 1px dashed var(--grid); background: rgba(255,250,240,.72); }}
    footer {{ margin-top: 24px; color: var(--muted); font-size: 12px; }}
    @media (max-width: 900px) {{
      .hero, .muniTop {{ grid-template-columns: 1fr; }}
      .actions {{ justify-content: flex-start; }}
      .stats {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .coverageTable {{ min-width: 760px; }}
      .tableWrap {{ overflow-x: auto; }}
    }}
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      <div class="heroText">
        <p class="eyebrow">Admin only · ingestion coverage</p>
        <h1>Source coverage checklist</h1>
        <p>This page shows which municipal source types are already ingested, explicitly missing, or still unknown. It is intentionally separate from resident-facing screens.</p>
      </div>
      <nav class="actions" aria-label="Admin coverage actions">
        <a class="button" href="/api/admin/ingestion-coverage">Open JSON</a>
        <a class="button secondary" href="/health">Health check</a>
      </nav>
    </section>
    <section class="stats" aria-label="Coverage summary">
      {_stat_card(summary.get("municipality_count"), "Municipalities")}
      {_stat_card(summary.get("source_type_count"), "Tracked source types")}
      {_stat_card(summary.get("available"), "Available cells")}
      {_stat_card(summary.get("unknown"), "Unknown cells")}
    </section>
    <section class="legend" aria-label="Status legend">
      {_status_pill("available")} <span>ingested or explicitly tracked as available</span>
      {_status_pill("missing")} <span>checked and not found</span>
      {_status_pill("unknown")} <span>not checked yet</span>
    </section>
    {municipality_cards or '<section class="empty">No municipalities were found in the ingestion database.</section>'}
    <footer>Generated from current database state. Last refresh: {generated_at}</footer>
  </main>
</body>
</html>"""


def _explicit_coverage_rows(db: Any) -> list[dict[str, Any]]:
    if not _table_exists(db, EXPLICIT_COVERAGE_TABLE):
        return []
    rows = db.execute(
        text(
            """
            SELECT municipality_slug, municipality_name_he, source_type, status, notes,
                   source_url, checked_at, updated_at
            FROM ingestion_source_coverage
            ORDER BY municipality_slug, source_type
            """
        )
    ).mappings().all()
    out = []
    for row in rows:
        payload = dict(row)
        status = str(payload.get("status") or "unknown")
        payload["status"] = status if status in VALID_COVERAGE_STATUSES else "unknown"
        out.append(payload)
    return out


def _available_coverage_rows(db: Any) -> list[dict[str, Any]]:
    try:
        rows = db.execute(
            text(
                """
                SELECT ss.municipality_slug,
                       MIN(ss.name) AS source_site_name,
                       COALESCE(NULLIF(ra.source_kind, ''), NULLIF(d.doc_kind, ''), 'unknown') AS source_type,
                       COUNT(DISTINCT d.id) AS document_count,
                       COUNT(DISTINCT dv.id) AS document_version_count,
                       COUNT(ra.id) AS artifact_count,
                       MAX(COALESCE(ra.created_at, d.last_seen_at, d.created_at)) AS last_ingested_at
                FROM document d
                JOIN source_site ss ON ss.id = d.source_site_id
                LEFT JOIN document_version dv ON dv.document_id = d.id
                LEFT JOIN retrieval_artifact ra ON ra.document_id = d.id
                GROUP BY ss.municipality_slug, COALESCE(NULLIF(ra.source_kind, ''), NULLIF(d.doc_kind, ''), 'unknown')
                ORDER BY ss.municipality_slug, source_type
                """
            )
        ).mappings().all()
    except SQLAlchemyError:
        return []
    return [dict(row) for row in rows]


def _source_site_rows(db: Any) -> list[dict[str, Any]]:
    try:
        rows = db.execute(
            text(
                """
                SELECT municipality_slug, MIN(name) AS name
                FROM source_site
                GROUP BY municipality_slug
                ORDER BY municipality_slug
                """
            )
        ).mappings().all()
    except SQLAlchemyError:
        return []
    return [dict(row) for row in rows]


def _table_exists(db: Any, table_name: str) -> bool:
    try:
        return inspect(db.get_bind()).has_table(table_name)
    except SQLAlchemyError:
        return False


def _municipality_base(slug: str, *, municipality_name_he: Any = None, source_site_name: Any = None) -> dict[str, Any]:
    return {
        "municipality_slug": slug,
        "municipality_name_he": _municipality_name_he(slug, municipality_name_he=municipality_name_he, source_site_name=source_site_name),
    }


def _municipality_name_he(slug: str, *, municipality_name_he: Any = None, source_site_name: Any = None) -> str:
    explicit = str(municipality_name_he or "").strip()
    if explicit:
        return explicit
    site_name = str(source_site_name or "").strip()
    if _contains_hebrew(site_name):
        return site_name
    return MUNICIPALITY_LABELS_HE.get(slug, slug.replace("_", " ").strip() or "unknown")


def _contains_hebrew(value: str) -> bool:
    return any("\u0590" <= character <= "\u05ff" for character in value)


def _ordered_source_type_codes(codes: list[str]) -> list[str]:
    ordered: list[str] = []
    for code in [*PUBLIC_SOURCE_TYPE_FILTER_CODES, *codes]:
        canonical = canonical_source_type_code(code)
        if canonical not in ordered:
            ordered.append(canonical)
    return ordered


def _coverage_row(source_type: str, explicit: dict[str, Any] | None, available: dict[str, Any] | None) -> dict[str, Any]:
    metadata = source_type_metadata(source_type)
    has_available_data = bool(available and (int(available.get("document_count") or 0) > 0 or int(available.get("artifact_count") or 0) > 0))
    explicit_status = str((explicit or {}).get("status") or "unknown")
    if has_available_data:
        status = "available"
        status_source = "ingested_data"
    elif explicit_status in {"available", "missing"}:
        status = explicit_status
        status_source = "admin_tracking"
    else:
        status = "unknown"
        status_source = "not_checked"
    return {
        "source_type": source_type,
        "source_type_label_he": metadata.label_he,
        "source_type_plural_label_he": metadata.plural_label_he,
        "source_type_filter_label_he": metadata.filter_label_he,
        "status": status,
        "status_label": STATUS_LABELS[status],
        "status_source": status_source,
        "document_count": int((available or {}).get("document_count") or 0),
        "document_version_count": int((available or {}).get("document_version_count") or 0),
        "artifact_count": int((available or {}).get("artifact_count") or 0),
        "last_ingested_at": _string_or_none((available or {}).get("last_ingested_at")),
        "notes": _string_or_none((explicit or {}).get("notes")),
        "source_url": _string_or_none((explicit or {}).get("source_url")),
        "checked_at": _string_or_none((explicit or {}).get("checked_at")),
        "updated_at": _string_or_none((explicit or {}).get("updated_at")),
    }


def _readiness_payload(summary: dict[str, int]) -> dict[str, str]:
    available = int(summary.get("available") or 0)
    if available <= 0:
        return {"code": "none", "label_en": "No ingested source types", "label_he": "אין מקורות מעובדים"}
    if available < 3:
        return {"code": "partial", "label_en": "Partial POC evidence base", "label_he": "כיסוי חלקי ל-POC"}
    return {"code": "broad", "label_en": "Broad POC evidence base", "label_he": "כיסוי רחב ל-POC"}


def _latest_value(left: Any, right: Any) -> Any:
    if left in (None, ""):
        return right
    if right in (None, ""):
        return left
    return max(str(left), str(right))


def _string_or_none(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _municipality_card(municipality: dict[str, Any]) -> str:
    name = _escape(municipality.get("municipality_name_he"))
    slug = _escape(municipality.get("municipality_slug"))
    summary = municipality.get("summary") if isinstance(municipality.get("summary"), dict) else {}
    readiness = summary.get("readiness") if isinstance(summary.get("readiness"), dict) else {}
    rows = "\n".join(_table_row(row) for row in municipality.get("source_types") or [])
    return f"""
    <section class="muniCard" aria-labelledby="municipality-{slug}">
      <header class="muniTop">
        <div>
          <h2 id="municipality-{slug}" dir="rtl">{name}</h2>
          <div class="slug">{slug}</div>
        </div>
        <div class="readiness" dir="rtl">{_escape(readiness.get("label_he") or readiness.get("label_en") or "Review")}</div>
      </header>
      <div class="tableWrap">
        <table class="coverageTable">
          <thead>
            <tr>
              <th>Source type</th>
              <th>Status</th>
              <th>Documents</th>
              <th>Artifacts</th>
              <th>Source</th>
              <th>Notes</th>
            </tr>
          </thead>
          <tbody>{rows}</tbody>
        </table>
      </div>
    </section>"""


def _table_row(row: dict[str, Any]) -> str:
    status = str(row.get("status") or "unknown")
    source_type = _escape(row.get("source_type"))
    label = _escape(row.get("source_type_filter_label_he") or row.get("source_type_plural_label_he") or source_type)
    status_source = _escape(row.get("status_source"))
    notes = _notes_html(row)
    return f"""
            <tr>
              <td><div class="sourceHe" dir="rtl">{label}</div><div class="sourceCode">{source_type}</div></td>
              <td>{_status_pill(status)}</td>
              <td class="metric">{int(row.get("document_count") or 0)}</td>
              <td class="metric">{int(row.get("artifact_count") or 0)}</td>
              <td>{status_source}</td>
              <td class="notes">{notes}</td>
            </tr>"""


def _notes_html(row: dict[str, Any]) -> str:
    parts = []
    if row.get("notes"):
        parts.append(_escape(row.get("notes")))
    if row.get("last_ingested_at"):
        parts.append(f"Last ingested: {_escape(row.get('last_ingested_at'))}")
    if row.get("checked_at"):
        parts.append(f"Checked: {_escape(row.get('checked_at'))}")
    if row.get("source_url"):
        url = _escape(row.get("source_url"))
        parts.append(f'<a href="{url}">tracked source</a>')
    return "<br />".join(parts) if parts else "-"


def _status_pill(status: str) -> str:
    normalized = status if status in STATUS_LABELS else "unknown"
    meta = STATUS_LABELS[normalized]
    return (
        f'<span class="statusPill status-{normalized}">'
        f'<span aria-hidden="true">{_escape(meta["symbol"])}</span>'
        f'<span>{_escape(meta["label_en"])}</span>'
        "</span>"
    )


def _stat_card(value: Any, label: str) -> str:
    return f'<div class="stat"><strong>{_escape(value if value is not None else 0)}</strong><span>{_escape(label)}</span></div>'


def _now_label(summary: dict[str, Any]) -> str:
    return f'{summary.get("cell_count", 0)} checklist cells'


def _escape(value: Any) -> str:
    return escape(str(value or ""), quote=True)
