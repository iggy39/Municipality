from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from municipality.gis_normalization import normalize_hebrew_text


_CONTEXT_LAYER_KEYS = {
    "municipal_boundaries",
    "neighborhoods_and_statistics",
    "public_buildings_assets",
    "resident_location",
    "public_transport_access",
}
_STATUS_STAGE = {
    "rejected": -1,
    "removed": -1,
    "unknown": 0,
    "open_request": 1,
    "reported": 2,
    "discussed": 2,
    "referred": 2,
    "deferred": 2,
    "approved": 3,
}
_STATUS_TRAFFIC = {
    "open_request": "red",
    "rejected": "red",
    "removed": "red",
    "reported": "yellow",
    "discussed": "yellow",
    "referred": "yellow",
    "deferred": "yellow",
    "unknown": "yellow",
    "approved": "green",
}
_STATUS_LABEL_HE = {
    "open_request": "בקשה פתוחה",
    "reported": "דיווח",
    "discussed": "נדון",
    "referred": "הועבר לטיפול",
    "deferred": "נדחה להמשך",
    "approved": "אושר",
    "rejected": "נדחה",
    "removed": "הוסר",
    "unknown": "סטטוס לא ידוע",
}
_TOKEN_STOPWORDS = {
    "אישור",
    "אחר",
    "את",
    "בין",
    "בלי",
    "במסגרת",
    "בנושא",
    "בפני",
    "בשל",
    "בקשה",
    "בקשתו",
    "גם",
    "דיון",
    "האם",
    "הוחלט",
    "העיר",
    "העירייה",
    "העיריה",
    "הצעה",
    "ההצעה",
    "הרשות",
    "הוא",
    "היא",
    "ועדת",
    "ועדה",
    "חברי",
    "כאן",
    "כללי",
    "להם",
    "לאחר",
    "לגבי",
    "להלן",
    "ליום",
    "מועצה",
    "מועצת",
    "מחלקת",
    "מיליון",
    "מליון",
    "מספר",
    "נושא",
    "נוסח",
    "סעיף",
    "עבור",
    "עיריית",
    "פרוטוקול",
    "ראש",
    "שאילתה",
    "שלום",
    "שלו",
    "תאריך",
    "the",
    "and",
    "for",
    "municipality",
}
_DATE_PATTERNS = (
    re.compile(r"(?<!\d)(?P<year>20\d{2}|19\d{2})[-/](?P<month>\d{1,2})[-/](?P<day>\d{1,2})(?!\d)"),
    re.compile(r"(?<!\d)(?P<day>\d{1,2})[./-](?P<month>\d{1,2})[./-](?P<year>\d{2,4})(?!\d)"),
)
_MOCK_BASE_DATE = date(2024, 1, 1)


@dataclass
class _StoryEvent:
    uid: str
    index: int
    raw: Mapping[str, Any]
    event_id: str
    artifact_id: str
    municipality_slug: str
    source_artifact_file: str
    source_row: str
    validation_status: str
    event_status: str
    action_type_he: str
    matter_he: str
    matter_display_he: str
    matter_identifiers: tuple[dict[str, Any], ...]
    outcome_he: str
    source_text: str
    evidence_quotes: tuple[str, ...]
    layer_keys: tuple[str, ...]
    layer_names: dict[str, str]
    ready_query_count: int
    blocked_query_count: int
    archetype_keys: tuple[str, ...]
    archetype_topics: tuple[str, ...]
    location_labels: tuple[str, ...]
    topic_family: str
    topic_family_label_he: str
    tokens: frozenset[str]
    subject_terms: tuple[str, ...]
    protocol_date: date | None
    date_source: str
    source_occurrences: list[str] = field(default_factory=list)


@dataclass
class _StoryCluster:
    municipality_slug: str
    topic_family: str
    topic_family_label_he: str
    events: list[_StoryEvent] = field(default_factory=list)
    token_counts: Counter[str] = field(default_factory=Counter)
    layer_counts: Counter[str] = field(default_factory=Counter)
    archetype_counts: Counter[str] = field(default_factory=Counter)
    location_counts: Counter[str] = field(default_factory=Counter)

    def add(self, event: _StoryEvent) -> None:
        self.events.append(event)
        self.token_counts.update(event.tokens)
        self.layer_counts.update(event.layer_keys)
        self.archetype_counts.update(event.archetype_keys)
        self.location_counts.update(event.location_labels)

    @property
    def tokens(self) -> set[str]:
        return set(self.token_counts)

    @property
    def layer_keys(self) -> set[str]:
        return set(self.layer_counts)

    @property
    def archetype_keys(self) -> set[str]:
        return set(self.archetype_counts)

    @property
    def location_labels(self) -> set[str]:
        return set(self.location_counts)


def build_protocol_gis_stories(
    payload: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    *,
    min_similarity: float = 0.46,
) -> dict[str, Any]:
    """Build UI-ready story timelines from artifact-only V3 GIS links.

    The grouping is intentionally generic: it uses municipality, layer overlap,
    resident archetype overlap, location hints, and normalized subject terms.
    It does not assume a municipality-specific protocol structure.
    """

    links = _links_from_payload(payload)
    unique_events, excluded = _unique_story_events(links)
    clusters = _cluster_events(unique_events, min_similarity=min_similarity)
    stories = [_story_payload(cluster, ordinal=index + 1) for index, cluster in enumerate(clusters)]
    stories.sort(key=lambda row: (_traffic_rank(str(row["traffic_light"])), -int(row["event_count"]), str(row["title_he"])))
    return {
        "schema_version": 1,
        "source": {
            "kind": "topic_subject_v3_gis_links",
            "input_link_count": len(links),
            "unique_event_count": len(unique_events),
            "excluded_link_count": sum(len(values) for values in excluded.values()),
            "excluded_by_reason": {key: len(values) for key, values in sorted(excluded.items())},
        },
        "story_count": len(stories),
        "traffic_light_counts": dict(Counter(str(story["traffic_light"]) for story in stories)),
        "human_judgement_counts": dict(Counter(str(story["human_judgement"]["judgement"]) for story in stories)),
        "stories": stories,
        "excluded_links": excluded,
    }


def protocol_gis_stories_markdown(story_report: Mapping[str, Any]) -> str:
    stories = [story for story in story_report.get("stories", []) if isinstance(story, Mapping)]
    lines = [
        "# V3 GIS Stories",
        "",
        f"Story count: {len(stories)}",
        "",
        "| Story ID | Title | Municipality | Traffic | Progression | Events | Protocols | Main Layers | Human Judge |",
        "|---|---|---|---|---|---:|---:|---|---|",
    ]
    for story in stories:
        layers = ", ".join(layer.get("layer_key", "") for layer in story.get("gis_summary", {}).get("top_layers", [])[:4])
        lines.append(
            "| "
            + " | ".join(
                _md_cell(value)
                for value in (
                    story.get("story_id", ""),
                    story.get("title_he", ""),
                    story.get("municipality_slug", ""),
                    story.get("traffic_light", ""),
                    story.get("progression", ""),
                    story.get("event_count", 0),
                    story.get("source_protocol_count", 0),
                    layers,
                    story.get("human_judgement", {}).get("judgement", ""),
                )
            )
            + " |"
        )

    low_confidence = [
        story
        for story in stories
        if str(story.get("human_judgement", {}).get("judgement")) != "strong_story"
    ]
    if low_confidence:
        lines.extend(
            [
                "",
                "## Human-Judge Review Items",
                "",
                "| Full Source/Text | Model Story Prediction | Human-Judge Ground Truth | Reason |",
                "|---|---|---|---|",
            ]
        )
        for story in low_confidence[:80]:
            first_event = next(iter(story.get("timeline_events", []) or []), {})
            lines.append(
                "| "
                + " | ".join(
                    _md_cell(value)
                    for value in (
                        _shorten(str(first_event.get("source_text") or first_event.get("summary") or ""), 220),
                        f"{story.get('title_he', '')} / {story.get('traffic_light', '')}",
                        story.get("human_judgement", {}).get("ground_truth", ""),
                        story.get("human_judgement", {}).get("reason", ""),
                    )
                )
                + " |"
            )
    return "\n".join(lines)


def build_gis_connection_summary(payload: Mapping[str, Any] | Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    links = _links_from_payload(payload)
    rows: dict[str, dict[str, Any]] = {}
    for link in links:
        seen_in_event: set[str] = set()
        for candidate in link.get("candidates", []) or []:
            if not isinstance(candidate, Mapping):
                continue
            layer_key = str(candidate.get("layer_key") or "")
            if not layer_key:
                continue
            row = rows.setdefault(
                layer_key,
                {
                    "layer_key": layer_key,
                    "display_name_he": str(candidate.get("layer_display_name_he") or ""),
                    "event_count": 0,
                    "connection_count": 0,
                    "ready_govmap_query_count": 0,
                    "blocked_govmap_query_count": 0,
                    "municipalities": Counter(),
                    "linking_modes": Counter(),
                    "govmap_query_kinds": Counter(),
                    "govmap_aliases": set(),
                },
            )
            row["connection_count"] += 1
            if layer_key not in seen_in_event:
                row["event_count"] += 1
                seen_in_event.add(layer_key)
            status = str((candidate.get("real_gis_query") or {}).get("status") or "")
            if status == "ready":
                row["ready_govmap_query_count"] += 1
            elif status == "blocked":
                row["blocked_govmap_query_count"] += 1
            row["municipalities"].update([str(link.get("municipality_slug") or "")])
            row["linking_modes"].update([str(candidate.get("linking_mode") or "")])
            row["govmap_query_kinds"].update([str((candidate.get("real_gis_query") or {}).get("query_kind") or "")])
            row["govmap_aliases"].update(str(alias) for alias in (candidate.get("real_gis_query") or {}).get("map_layer_aliases") or [] if str(alias))

    summary_rows = []
    for row in rows.values():
        summary_rows.append(
            {
                **{key: value for key, value in row.items() if key not in {"municipalities", "linking_modes", "govmap_query_kinds", "govmap_aliases"}},
                "municipalities": dict(row["municipalities"].most_common()),
                "linking_modes": dict(row["linking_modes"].most_common()),
                "govmap_query_kinds": dict(row["govmap_query_kinds"].most_common()),
                "govmap_aliases": sorted(row["govmap_aliases"]),
            }
        )
    summary_rows.sort(key=lambda row: (-int(row["connection_count"]), str(row["layer_key"])))
    return {"schema_version": 1, "layer_count": len(summary_rows), "layers": summary_rows}


def gis_connection_summary_markdown(summary: Mapping[str, Any]) -> str:
    lines = [
        "# V3 GIS Connection Summary",
        "",
        "| GIS layer key | Hebrew name | Events | Connections | Ready | Blocked | GovMap aliases |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for row in summary.get("layers", []) or []:
        aliases = ", ".join(row.get("govmap_aliases", [])[:8])
        if len(row.get("govmap_aliases", []) or []) > 8:
            aliases += f" +{len(row['govmap_aliases']) - 8}"
        lines.append(
            "| "
            + " | ".join(
                _md_cell(value)
                for value in (
                    row.get("layer_key", ""),
                    row.get("display_name_he", ""),
                    row.get("event_count", 0),
                    row.get("connection_count", 0),
                    row.get("ready_govmap_query_count", 0),
                    row.get("blocked_govmap_query_count", 0),
                    aliases,
                )
            )
            + " |"
        )
    return "\n".join(lines)


def _links_from_payload(payload: Mapping[str, Any] | Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    if isinstance(payload, Mapping):
        raw_links = payload.get("links") or []
    else:
        raw_links = payload
    return [row for row in raw_links if isinstance(row, Mapping)]


def _unique_story_events(links: Sequence[Mapping[str, Any]]) -> tuple[list[_StoryEvent], dict[str, list[dict[str, Any]]]]:
    by_key: dict[tuple[str, str, str], _StoryEvent] = {}
    excluded: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for index, link in enumerate(links):
        reason = _exclusion_reason(link)
        if reason:
            excluded[reason].append(_excluded_link_payload(link))
            continue
        event = _event_from_link(link, index=index)
        key = (event.municipality_slug, event.artifact_id, _event_signature(event))
        existing = by_key.get(key)
        if existing is None or _event_quality_score(event) > _event_quality_score(existing):
            if existing is not None:
                event.source_occurrences.extend(existing.source_occurrences)
            by_key[key] = event
        else:
            existing.source_occurrences.extend(event.source_occurrences)
    events = list(by_key.values())
    events.sort(key=lambda event: (event.municipality_slug, event.topic_family, event.index))
    return events, {key: values for key, values in excluded.items()}


def _exclusion_reason(link: Mapping[str, Any]) -> str:
    if str(link.get("validation_status") or "") == "failed":
        return "failed_validation"
    if str(link.get("municipality_slug") or "") == "mock_unknown_municipality":
        return "missing_real_municipality"
    source_text = str(link.get("source_text") or "")
    matter = str(link.get("matter_he") or link.get("matter_display_he") or "")
    if not source_text.strip() and not matter.strip():
        return "empty_source_and_matter"
    return ""


def _excluded_link_payload(link: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "event_id": str(link.get("event_id") or ""),
        "artifact_id": str(link.get("artifact_id") or ""),
        "municipality_slug": str(link.get("municipality_slug") or ""),
        "validation_status": str(link.get("validation_status") or ""),
        "matter_he": _shorten(str(link.get("matter_he") or ""), 180),
        "matter_display_he": _shorten(str(link.get("matter_display_he") or ""), 180),
        "source_artifact_file": str(link.get("source_artifact_file") or ""),
    }


def _event_from_link(link: Mapping[str, Any], *, index: int) -> _StoryEvent:
    candidates = [candidate for candidate in link.get("candidates", []) or [] if isinstance(candidate, Mapping)]
    layer_keys = tuple(_dedupe_text(str(candidate.get("layer_key") or "") for candidate in candidates if candidate.get("layer_key")))
    layer_names = {str(candidate.get("layer_key") or ""): str(candidate.get("layer_display_name_he") or "") for candidate in candidates if candidate.get("layer_key")}
    ready_count = sum(1 for candidate in candidates if str((candidate.get("real_gis_query") or {}).get("status") or "") == "ready")
    blocked_count = sum(1 for candidate in candidates if str((candidate.get("real_gis_query") or {}).get("status") or "") == "blocked")
    archetypes = [row for row in link.get("supported_archetypes", []) or [] if isinstance(row, Mapping)]
    archetype_keys = tuple(_dedupe_text(str(row.get("key") or "") for row in archetypes if row.get("key")))
    archetype_topics = tuple(_dedupe_text(str(row.get("topic") or "") for row in archetypes if row.get("topic")))
    location_labels = tuple(
        _dedupe_text(
            str(hint.get("label_he") or "")
            for hint in link.get("location_hints", []) or []
            if isinstance(hint, Mapping) and not bool(hint.get("is_mock")) and hint.get("label_he")
        )
    )
    source_text = str(link.get("source_text") or "")
    matter = str(link.get("matter_he") or "")
    matter_display = str(link.get("matter_display_he") or matter)
    matter_identifiers = tuple(dict(item) for item in link.get("matter_identifiers", []) or [] if isinstance(item, Mapping))
    tokens = frozenset(_text_tokens("\n".join((matter_display, matter, source_text))))
    subject_terms = _subject_terms(matter=matter_display or matter, source_text=source_text, archetype_topics=archetype_topics)
    topic_family, topic_family_label = _topic_family(
        layer_keys=layer_keys,
        layer_names=layer_names,
        archetype_keys=archetype_keys,
        archetype_topics=archetype_topics,
        subject_terms=subject_terms,
        source_tokens=tokens,
    )
    protocol_date, date_source = _extract_protocol_date(link)
    uid = str(link.get("event_id") or "") or _hash_text("|".join((str(link.get("artifact_id") or ""), matter, source_text)), length=20)
    source_artifact_file = str(link.get("source_artifact_file") or "")
    return _StoryEvent(
        uid=uid,
        index=index,
        raw=link,
        event_id=str(link.get("event_id") or ""),
        artifact_id=str(link.get("artifact_id") or ""),
        municipality_slug=str(link.get("municipality_slug") or ""),
        source_artifact_file=source_artifact_file,
        source_row=str(link.get("source_row") or ""),
        validation_status=str(link.get("validation_status") or ""),
        event_status=_normalize_event_status(str(link.get("event_status") or "unknown")),
        action_type_he=str(link.get("action_type_he") or ""),
        matter_he=matter,
        matter_display_he=matter_display,
        matter_identifiers=matter_identifiers,
        outcome_he=str(link.get("outcome_he") or ""),
        source_text=source_text,
        evidence_quotes=tuple(_dedupe_text(str(value or "") for value in link.get("evidence_quotes", []) or [])),
        layer_keys=layer_keys,
        layer_names=layer_names,
        ready_query_count=ready_count,
        blocked_query_count=blocked_count,
        archetype_keys=archetype_keys,
        archetype_topics=archetype_topics,
        location_labels=location_labels,
        topic_family=topic_family,
        topic_family_label_he=topic_family_label,
        tokens=tokens,
        subject_terms=subject_terms,
        protocol_date=protocol_date,
        date_source=date_source,
        source_occurrences=[source_artifact_file] if source_artifact_file else [],
    )


def _event_quality_score(event: _StoryEvent) -> int:
    score = 0
    if event.validation_status == "accepted":
        score += 100
    elif event.validation_status == "needs_review":
        score += 50
    if event.protocol_date is not None:
        score += 20
    score += min(len(event.source_text), 500) // 25
    score += len(event.evidence_quotes) * 3
    score += len(event.layer_keys)
    return score


def _event_signature(event: _StoryEvent) -> str:
    identity_text = event.matter_he if event.matter_identifiers else (event.matter_display_he or event.matter_he)
    text = normalize_hebrew_text(identity_text or event.source_text or event.event_id).lower()
    text = re.sub(r"[^0-9a-zA-Z\u0590-\u05FF]+", " ", text)
    compact = " ".join(text.split())[:180]
    return "|".join((event.event_status, event.action_type_he, compact or event.uid))


def _cluster_events(events: Sequence[_StoryEvent], *, min_similarity: float) -> list[_StoryCluster]:
    clusters: list[_StoryCluster] = []
    for event in events:
        best_cluster: _StoryCluster | None = None
        best_score = 0.0
        for cluster in clusters:
            score = _similarity(event, cluster)
            if score > best_score:
                best_score = score
                best_cluster = cluster
        if best_cluster is not None and best_score >= min_similarity:
            best_cluster.add(event)
        else:
            cluster = _StoryCluster(
                municipality_slug=event.municipality_slug,
                topic_family=event.topic_family,
                topic_family_label_he=event.topic_family_label_he,
            )
            cluster.add(event)
            clusters.append(cluster)
    clusters.sort(key=lambda cluster: (cluster.municipality_slug, cluster.topic_family, -len(cluster.events)))
    return clusters


def _similarity(event: _StoryEvent, cluster: _StoryCluster) -> float:
    if event.municipality_slug != cluster.municipality_slug:
        return 0.0
    token_overlap = _jaccard(event.tokens, cluster.tokens)
    archetype_overlap = _jaccard(set(event.archetype_keys), cluster.archetype_keys)
    location_overlap = _jaccard(set(event.location_labels), cluster.location_labels)
    if event.topic_family != cluster.topic_family:
        return 0.0
    if token_overlap == 0.0 and archetype_overlap == 0.0 and location_overlap == 0.0:
        return 0.0
    family_score = 0.28
    token_score = 0.42 * token_overlap
    layer_score = 0.16 * _jaccard(set(event.layer_keys), cluster.layer_keys)
    archetype_score = 0.10 * archetype_overlap
    location_score = 0.04 * location_overlap
    return family_score + token_score + layer_score + archetype_score + location_score


def _story_payload(cluster: _StoryCluster, *, ordinal: int) -> dict[str, Any]:
    events = _timeline_events(cluster)
    traffic_light, progression, reason = _story_progress(events)
    subject_terms = tuple(term for term, _ in cluster.token_counts.most_common(8))
    title, title_is_mock = _story_title(cluster=cluster, subject_terms=subject_terms)
    mock_fields = []
    if title_is_mock:
        mock_fields.append("title_he")
    for event in events:
        if event["date_is_mock"]:
            mock_fields.append(f"timeline_events.{event['id']}.date")
    source_protocols = sorted({event["source_protocol_label"] for event in events if event.get("source_protocol_label")})
    story_id = f"story_{_hash_text('|'.join((cluster.municipality_slug, cluster.topic_family, title, str(ordinal))), length=16)}"
    human_judgement = _human_judgement(cluster=cluster, events=events, traffic_light=traffic_light, progression=progression)
    return {
        "story_id": story_id,
        "title_he": title,
        "municipality_slug": cluster.municipality_slug,
        "topic_family": cluster.topic_family,
        "topic_family_label_he": cluster.topic_family_label_he,
        "subject_terms": list(subject_terms),
        "cluster_method": "generic_layer_archetype_token_location_similarity",
        "traffic_light": traffic_light,
        "progression": progression,
        "traffic_reason_he": reason,
        "event_count": len(events),
        "source_protocol_count": len(source_protocols),
        "source_protocols": source_protocols[:12],
        "has_mock_completion": bool(mock_fields),
        "mock_fields": mock_fields,
        "timeline_events": events,
        "gis_summary": _gis_summary(cluster),
        "resident_archetype_keys": [key for key, _ in cluster.archetype_counts.most_common()],
        "location_labels": [label for label, _ in cluster.location_counts.most_common()],
        "human_judgement": human_judgement,
    }


def _timeline_events(cluster: _StoryCluster) -> list[dict[str, Any]]:
    raw_events = list(cluster.events)
    real_date_count = sum(1 for event in raw_events if event.protocol_date is not None)
    if real_date_count >= 2:
        raw_events.sort(key=lambda event: (event.protocol_date or date.max, _status_stage(event.event_status), event.index))
    else:
        raw_events.sort(key=lambda event: (_status_stage(event.event_status), event.protocol_date or date.max, event.index))
    mock_base = _mock_story_base_date(cluster)
    timeline: list[dict[str, Any]] = []
    for index, event in enumerate(raw_events):
        event_date = event.protocol_date or mock_base + timedelta(days=45 * index)
        date_is_mock = event.protocol_date is None
        event_status = event.event_status
        traffic = _event_traffic(event_status)
        event_id = event.event_id or f"story_event_{_hash_text(event.uid, length=12)}"
        timeline.append(
            {
                "id": event_id,
                "event_id": event.event_id,
                "artifact_id": event.artifact_id,
                "date": event_date.isoformat(),
                "date_label": _date_label(event_date),
                "date_source": "mock_sequence_date" if date_is_mock else event.date_source,
                "date_is_mock": date_is_mock,
                "title": _event_title(event),
                "summary": _event_summary(event),
                "selected": False,
                "event_status": event_status,
                "action_type_he": event.action_type_he,
                "matter_he": event.matter_he,
                "matter_display_he": event.matter_display_he,
                "matter_identifiers": list(event.matter_identifiers),
                "outcome_he": event.outcome_he,
                "source_text": _shorten(event.source_text, 900),
                "evidence_quotes": list(event.evidence_quotes[:4]),
                "source_protocol_label": _source_protocol_label(event),
                "source_artifact_file": event.source_artifact_file,
                "source_occurrence_count": len(set(event.source_occurrences)),
                "gis_layer_keys": list(event.layer_keys),
                "ready_govmap_query_count": event.ready_query_count,
                "blocked_govmap_query_count": event.blocked_query_count,
                "progress": {
                    "status": traffic,
                    "label_he": _STATUS_LABEL_HE.get(event_status, event_status),
                    "title_he": _progress_title_he(event_status),
                    "protocol_date_label": _date_label(event_date),
                    "stage": _status_stage(event_status),
                },
                "mock_fields": ["date"] if date_is_mock else [],
                "real_fields": ["date"] if not date_is_mock and event.date_source in {"explicit_text_date", "source_text_date"} else [],
                "inferred_fields": ["date"] if not date_is_mock and event.date_source not in {"explicit_text_date", "source_text_date"} else [],
            }
        )
    if timeline:
        timeline[-1]["selected"] = True
    return timeline


def _story_progress(events: Sequence[Mapping[str, Any]]) -> tuple[str, str, str]:
    statuses = [str(event.get("event_status") or "unknown") for event in events]
    stages = [_status_stage(status) for status in statuses]
    latest_status = statuses[-1] if statuses else "unknown"
    latest_stage = stages[-1] if stages else 0
    if not events:
        return "yellow", "unknown", "אין אירועים בסיפור ולכן הסטטוס אינו ודאי."
    if latest_status == "approved":
        return "green", "advanced", "האירוע האחרון בסיפור הוא אישור ולכן הנושא התקדם לשלב ירוק."
    if latest_status in {"rejected", "removed"}:
        return "red", "regressed", "האירוע האחרון דחה או הסיר את הנושא ולכן יש חסימה/נסיגה."
    if latest_status == "open_request" and any(stage >= 3 for stage in stages[:-1]):
        return "yellow", "new_follow_up_after_decision", "אחרי החלטה קודמת הופיעה בקשה חדשה, ולכן זה מעקב פתוח ולא סגירה מלאה."
    if latest_status == "open_request":
        if len([status for status in statuses if status == "open_request"]) >= 2:
            return "red", "stalled", "יש כמה בקשות פתוחות בלי החלטה מאוחרת, ולכן הנושא נראה תקוע."
        return "red", "new", "הסיפור מתחיל בבקשה פתוחה ועדיין אין התקדמות מתועדת."
    if latest_stage > min(stages):
        return "yellow", "advanced", "הנושא התקדם מדיון/בקשה לשלב ביניים, אבל אין אישור סופי."
    if all(status == "unknown" for status in statuses):
        return "yellow", "uncertain", "לכל האירועים חסר סטטוס ברור, ולכן הרמזור צהוב עם אזהרת ודאות."
    return "yellow", "stalled", "יש פעילות בנושא, אבל הסיפור לא הגיע לאישור או סגירה ברורה."


def _human_judgement(*, cluster: _StoryCluster, events: Sequence[Mapping[str, Any]], traffic_light: str, progression: str) -> dict[str, Any]:
    real_event_count = len(events)
    unique_protocol_count = len({str(event.get("artifact_id") or event.get("source_protocol_label") or "") for event in events})
    all_mock_dates = all(bool(event.get("date_is_mock")) for event in events)
    has_status = any(str(event.get("event_status") or "unknown") != "unknown" for event in events)
    token_strength = len(cluster.token_counts)
    if real_event_count >= 2 and unique_protocol_count >= 2 and has_status and token_strength >= 3 and not all_mock_dates:
        judgement = "strong_story"
        ground_truth = "Related events can reasonably be presented as one advancing subject story."
        reason = "The grouped events share municipality, GIS/topic signals, and enough subject terms, with multiple source protocol artifacts."
        confidence = "high"
    elif real_event_count >= 2 and token_strength >= 2:
        judgement = "needs_review"
        ground_truth = "Possibly related story, but not strong enough for user-facing presentation without review."
        reason = "The cluster has multiple events, but dates/status/source diversity or subject evidence is weak."
        confidence = "medium"
    else:
        judgement = "mock_seed_only"
        ground_truth = "Single seed event; this should be shown only as a draft story or with mock continuation."
        reason = "There is not enough cross-protocol evidence to prove a real story progression."
        confidence = "low"
    concerns: list[str] = []
    if all_mock_dates:
        concerns.append("all_dates_are_mock")
    if not has_status:
        concerns.append("missing_clear_event_status")
    if traffic_light == "green" and progression != "advanced":
        concerns.append("green_without_clear_advance")
    return {
        "judgement": judgement,
        "confidence": confidence,
        "ground_truth": ground_truth,
        "reason": reason,
        "concerns": concerns,
    }


def _gis_summary(cluster: _StoryCluster) -> dict[str, Any]:
    ready = sum(event.ready_query_count for event in cluster.events)
    blocked = sum(event.blocked_query_count for event in cluster.events)
    top_layers = []
    for layer_key, count in cluster.layer_counts.most_common(8):
        display_name = ""
        for event in cluster.events:
            if event.layer_names.get(layer_key):
                display_name = event.layer_names[layer_key]
                break
        top_layers.append({"layer_key": layer_key, "display_name_he": display_name, "event_count": count})
    return {
        "ready_govmap_query_count": ready,
        "blocked_govmap_query_count": blocked,
        "top_layers": top_layers,
    }


def _topic_family(
    *,
    layer_keys: Sequence[str],
    layer_names: Mapping[str, str],
    archetype_keys: Sequence[str],
    archetype_topics: Sequence[str],
    subject_terms: Sequence[str],
    source_tokens: Iterable[str],
) -> tuple[str, str]:
    if archetype_keys:
        label = next((topic for topic in archetype_topics if topic), archetype_keys[0])
        archetype_tokens = set(_text_tokens(label))
        if len(set(source_tokens) & archetype_tokens) < 2:
            label = " ".join(subject_terms[:3]) if subject_terms else label
        return f"archetype:{archetype_keys[0]}", label
    for layer_key in layer_keys:
        if layer_key not in _CONTEXT_LAYER_KEYS:
            return f"layer:{layer_key}", layer_names.get(layer_key, layer_key)
    if subject_terms:
        signature = "_".join(subject_terms[:2])
        label = " ".join(subject_terms[:3])
        return f"subject:{signature}", f"נושא עירוני: {label}"
    if layer_keys:
        return f"layer:{layer_keys[0]}", layer_names.get(layer_keys[0], layer_keys[0])
    return "unknown", "נושא לא מזוהה"


def _subject_terms(*, matter: str, source_text: str, archetype_topics: Sequence[str]) -> tuple[str, ...]:
    counts: Counter[str] = Counter()
    counts.update(_text_tokens(matter) * 3)
    counts.update(_text_tokens(source_text))
    if not counts:
        counts.update(_text_tokens(" ".join(archetype_topics)))
    return tuple(term for term, _ in counts.most_common(8))


def _text_tokens(text: str) -> list[str]:
    normalized = normalize_hebrew_text(str(text or "")).lower()
    normalized = re.sub(r"[^0-9a-zA-Z\u0590-\u05FF]+", " ", normalized)
    tokens = []
    for token in normalized.split():
        if len(token) < 3 or token.isdigit() or token in _TOKEN_STOPWORDS:
            continue
        if re.fullmatch(r"\d+(?:\s|$)", token):
            continue
        if re.fullmatch(r"[0-9a-f]{6,}", token):
            continue
        if re.search(r"\d", token) and not re.search(r"[\u0590-\u05FF]", token):
            continue
        tokens.append(token)
    return tokens[:120]


def _extract_protocol_date(link: Mapping[str, Any]) -> tuple[date | None, str]:
    primary_time = link.get("primary_time") if isinstance(link.get("primary_time"), Mapping) else {}
    primary_start = str(primary_time.get("start") or "")
    if primary_start:
        parsed_primary = _date_from_iso(primary_start)
        if parsed_primary is not None:
            return parsed_primary, str(primary_time.get("date_source") or primary_time.get("kind") or "primary_time")
    for mention in link.get("time_mentions") or []:
        if isinstance(mention, Mapping):
            parsed_mention = _date_from_iso(str(mention.get("iso_date") or mention.get("start") or ""))
            if parsed_mention is not None:
                return parsed_mention, str(mention.get("date_source") or mention.get("kind") or "time_mentions")
    for mention in link.get("raw_date_mentions") or []:
        if isinstance(mention, Mapping):
            parsed_mention = _date_from_iso(str(mention.get("iso_date") or mention.get("start") or "")) or _first_date(str(mention.get("raw_text") or ""))
            if parsed_mention is not None:
                return parsed_mention, str(mention.get("date_source") or mention.get("kind") or "raw_date_mentions")
    source_provenance = link.get("source_provenance") if isinstance(link.get("source_provenance"), Mapping) else {}
    texts = [
        str(link.get("matter_display_he") or ""),
        str(link.get("matter_he") or ""),
        str(link.get("source_text") or ""),
        str(source_provenance.get("source_title") or ""),
        str(source_provenance.get("source_url") or ""),
    ]
    texts.extend(_source_provenance_texts(source_provenance))
    for text in texts:
        parsed = _first_date(text)
        if parsed is not None:
            return parsed, "source_text_date"
    return None, "missing_protocol_date"


def _date_from_iso(value: str) -> date | None:
    try:
        parsed = date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None
    if 1990 <= parsed.year <= 2035:
        return parsed
    return None


def _first_date(text: str) -> date | None:
    for match in re.finditer(r"(?<!\d)((?:19|20)\d{2})(\d{2})(\d{2})(?!\d)", str(text or "")):
        try:
            parsed = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            continue
        if 1990 <= parsed.year <= 2035:
            return parsed
    for pattern in _DATE_PATTERNS:
        for match in pattern.finditer(str(text or "")):
            try:
                year = int(match.group("year"))
                if year < 100:
                    year = 2000 + year if year <= 35 else 1900 + year
                parsed = date(year, int(match.group("month")), int(match.group("day")))
            except ValueError:
                continue
            if 1990 <= parsed.year <= 2035:
                return parsed
    return None


def _source_provenance_texts(value: Any) -> list[str]:
    texts: list[str] = []
    if isinstance(value, Mapping):
        for nested in value.values():
            texts.extend(_source_provenance_texts(nested))
    elif isinstance(value, list | tuple):
        for nested in value:
            texts.extend(_source_provenance_texts(nested))
    elif value not in (None, ""):
        texts.append(str(value))
    return texts[:40]


def _mock_story_base_date(cluster: _StoryCluster) -> date:
    offset = int(_hash_text("|".join((cluster.municipality_slug, cluster.topic_family)), length=8), 16) % 540
    return _MOCK_BASE_DATE + timedelta(days=offset)


def _story_title(*, cluster: _StoryCluster, subject_terms: Sequence[str]) -> tuple[str, bool]:
    useful_terms = [term for term in subject_terms if term not in cluster.municipality_slug]
    if useful_terms:
        return f"{cluster.topic_family_label_he}: {', '.join(useful_terms[:3])}", False
    return f"סיפור GIS בנושא {cluster.topic_family_label_he}", True


def _event_title(event: _StoryEvent) -> str:
    action = event.action_type_he or _STATUS_LABEL_HE.get(event.event_status, "אירוע")
    matter = event.matter_display_he or event.matter_he or event.topic_family_label_he
    return _shorten(f"{action}: {matter}", 120)


def _event_summary(event: _StoryEvent) -> str:
    text = event.matter_display_he or event.matter_he or event.source_text or event.topic_family_label_he
    return _shorten(text, 170)


def _source_protocol_label(event: _StoryEvent) -> str:
    if event.artifact_id:
        return event.artifact_id
    if event.source_row:
        return event.source_row
    if event.source_artifact_file:
        return Path(event.source_artifact_file).parent.name
    return f"mock_protocol_{event.index + 1}"


def _progress_title_he(status: str) -> str:
    if status == "approved":
        return "התקדמות מאושרת"
    if status in {"discussed", "reported", "referred", "deferred"}:
        return "שלב ביניים"
    if status in {"rejected", "removed"}:
        return "חסימה או נסיגה"
    if status == "open_request":
        return "דורש המשך טיפול"
    return "סטטוס לא ודאי"


def _normalize_event_status(status: str) -> str:
    parts = [part for part in str(status or "unknown").split("|") if part]
    if "approved" in parts:
        return "approved"
    return parts[0] if parts else "unknown"


def _status_stage(status: str) -> int:
    return _STATUS_STAGE.get(_normalize_event_status(status), 0)


def _event_traffic(status: str) -> str:
    return _STATUS_TRAFFIC.get(_normalize_event_status(status), "yellow")


def _traffic_rank(value: str) -> int:
    return {"red": 0, "yellow": 1, "green": 2}.get(value, 3)


def _jaccard(left: Iterable[str], right: Iterable[str]) -> float:
    left_set = {value for value in left if value}
    right_set = {value for value in right if value}
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def _date_label(value: date) -> str:
    return f"{value.day:02d}.{value.month:02d}.{value.year}"


def _hash_text(text: str, *, length: int) -> str:
    return hashlib.sha1(str(text).encode("utf-8")).hexdigest()[:length]


def _dedupe_text(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _shorten(value: str, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _md_cell(value: Any) -> str:
    return str(value).replace("|", "/").replace("\n", " ").strip()


def write_story_report(path: str | Path, report: Mapping[str, Any]) -> None:
    Path(path).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
