from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import httpx
from pyproj import Transformer

from municipality.govmap_client import GovMapClient, GovMapLayerSpec
from municipality.resident_gis_registry import load_resident_gis_registry


DEFAULT_GIS_LINK_REPORT_PATH = Path(__file__).resolve().parents[2] / "eval" / "reports" / "v3_gis_links_all_artifacts_20260624.json"
DEFAULT_GIS_STORY_REPORT_PATH = Path(__file__).resolve().parents[2] / "eval" / "reports" / "v3_gis_stories_all_artifacts_20260624.json"


@dataclass(frozen=True)
class StoryGovMapExecutionOptions:
    story_ids: tuple[str, ...] = ()
    max_stories: int = 1
    max_queries_per_story: int = 8
    radius_m: float = 3000.0
    live: bool = False
    osm_fallback: bool = True
    osm_user_agent: str = "municipality-research-gis/0.1"
    osm_transport: httpx.BaseTransport | None = None


def execute_story_govmap_queries(
    *,
    story_report: Mapping[str, Any],
    link_report: Mapping[str, Any],
    options: StoryGovMapExecutionOptions | None = None,
    client: GovMapClient | None = None,
) -> dict[str, Any]:
    """Run selected story GIS query plans against GovMap and return an artifact-only report."""

    opts = options or StoryGovMapExecutionOptions()
    client = client or GovMapClient()
    registry = load_resident_gis_registry()
    layer_labels = _layer_labels(registry)
    stories = _select_stories(story_report.get("stories") or [], story_ids=opts.story_ids, max_stories=opts.max_stories)
    links_by_event = _links_by_event(link_report.get("links") or [])
    story_results = []
    for story in stories:
        story_results.append(
            _execute_story(
                story,
                links_by_event=links_by_event,
                layer_labels=layer_labels,
                client=client,
                options=opts,
            )
        )
    status_counts: dict[str, int] = {}
    for story_result in story_results:
        for query in story_result.get("queries", []):
            status = str(query.get("status") or "unknown")
            status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "source": {
            "story_count": len(stories),
            "link_count": len([row for row in link_report.get("links", []) if isinstance(row, Mapping)]),
            "live_execution": opts.live,
            "max_queries_per_story": opts.max_queries_per_story,
            "radius_m": opts.radius_m,
        },
        "provider": "govmap",
        "status_counts": status_counts,
        "stories": story_results,
    }


def story_govmap_execution_markdown(report: Mapping[str, Any]) -> str:
    lines = ["# Story GovMap Execution", ""]
    source = report.get("source") if isinstance(report.get("source"), Mapping) else {}
    lines.append(f"- Provider: {report.get('provider', 'govmap')}")
    lines.append(f"- Live execution: {source.get('live_execution')}")
    lines.append(f"- Stories: {source.get('story_count', 0)}")
    lines.append(f"- Status counts: {json.dumps(report.get('status_counts') or {}, ensure_ascii=False)}")
    lines.append("")
    for story in report.get("stories", []):
        if not isinstance(story, Mapping):
            continue
        lines.append(f"## {story.get('title_he') or story.get('story_id')}")
        lines.append(f"- Story ID: `{story.get('story_id')}`")
        lines.append(f"- Municipality: `{story.get('municipality_slug')}`")
        lines.append(f"- Queries: {story.get('query_count', 0)}")
        lines.append("")
        lines.append("| Layer | Search | Status | Features | Note |")
        lines.append("|---|---|---:|---:|---|")
        for query in story.get("queries", []):
            if not isinstance(query, Mapping):
                continue
            note = query.get("error") or query.get("blocked_reason") or " / ".join(str(value) for value in (query.get("geocode_provider"), query.get("geocode_match_quality")) if value) or query.get("center_source") or ""
            lines.append(
                f"| {query.get('layer_display_name_he') or query.get('layer_key') or ''} | {query.get('search_text') or ''} | {query.get('status') or ''} | {query.get('feature_count', 0)} | {note} |"
            )
        lines.append("")
    return "\n".join(lines).rstrip()


def load_default_story_and_link_reports(
    *,
    story_path: Path = DEFAULT_GIS_STORY_REPORT_PATH,
    link_path: Path = DEFAULT_GIS_LINK_REPORT_PATH,
) -> tuple[dict[str, Any], dict[str, Any]]:
    return json.loads(story_path.read_text(encoding="utf-8")), json.loads(link_path.read_text(encoding="utf-8"))


def _execute_story(
    story: Mapping[str, Any],
    *,
    links_by_event: Mapping[tuple[str, str], list[Mapping[str, Any]]],
    layer_labels: Mapping[str, str],
    client: GovMapClient,
    options: StoryGovMapExecutionOptions,
) -> dict[str, Any]:
    plans = _story_query_plans(story, links_by_event=links_by_event, layer_labels=layer_labels)
    queries = []
    for plan in plans[: options.max_queries_per_story]:
        queries.append(_execute_plan(plan, client=client, options=options))
    return {
        "story_id": str(story.get("story_id") or ""),
        "title_he": str(story.get("title_he") or ""),
        "municipality_slug": str(story.get("municipality_slug") or ""),
        "traffic_light": str(story.get("traffic_light") or ""),
        "query_count": len(queries),
        "candidate_plan_count": len(plans),
        "queries": queries,
    }


def _execute_plan(plan: Mapping[str, Any], *, client: GovMapClient, options: StoryGovMapExecutionOptions) -> dict[str, Any]:
    base = dict(plan)
    if not options.live:
        return {**base, "status": "not_executed_live_disabled", "feature_count": 0, "features": []}
    query = plan.get("real_gis_query") if isinstance(plan.get("real_gis_query"), Mapping) else {}
    if query.get("status") != "ready" or not query.get("executable"):
        return {**base, "status": "blocked", "blocked_reason": ",".join(str(value) for value in query.get("blocked_reasons", [])), "feature_count": 0, "features": []}
    center, search_attempts = _resolve_center(query, client=client, options=options)
    if center is None:
        return {**base, "status": "query_ready_but_empty", "feature_count": 0, "features": [], "search_attempts": search_attempts}
    aliases = tuple(str(alias) for alias in query.get("map_layer_aliases", []) if str(alias or "").strip())
    specs = tuple(_dynamic_layer_spec(alias, label=base.get("layer_display_name_he") or base.get("layer_key") or alias) for alias in aliases)
    if not specs:
        return {**base, "status": "geocoded_no_layers", "center": center, "feature_count": 0, "features": [], "search_attempts": search_attempts}
    try:
        spatial = client.features_by_location(x=float(center["x"]), y=float(center["y"]), radius_m=options.radius_m, layers=specs)
    except httpx.HTTPError as exc:
        return {**base, "status": "govmap_error", "error": exc.__class__.__name__, "center": center, "feature_count": 0, "features": [], "search_attempts": search_attempts}
    features = _flatten_spatial_features(spatial.get("layers") or {}, max_features=12)
    return {
        **base,
        "status": "loaded_real_geometry" if features else "query_ready_but_empty",
        "center": center,
        "center_source": center.get("provider") or "govmap_search",
        "geocode_match_quality": center.get("match_quality"),
        "geocode_provider": center.get("provider") or "govmap_search",
        "feature_count": len(features),
        "features": features,
        "search_attempts": search_attempts,
    }


def _resolve_center(query: Mapping[str, Any], *, client: GovMapClient, options: StoryGovMapExecutionOptions) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    primary_search_text = str(query.get("search_text") or "")
    raw_texts = [primary_search_text, *[str(value) for value in query.get("alternate_search_texts", [])]]
    search_texts = _search_text_variants(raw_texts)
    datatype_filters = tuple(query.get("search_datatypes") or ())
    search_attempts: list[dict[str, Any]] = []
    fallback_center: dict[str, Any] | None = None
    for search_text in search_texts:
        search_variants: list[tuple[str, tuple[str, ...]]] = [("typed", datatype_filters)]
        if datatype_filters:
            # GovMap search can return no result for typed address/street/settlement searches while
            # the same text is available in the broader catalog search. Keep both attempts explicit.
            search_variants.append(("unfiltered", ()))
        for search_scope, search_layers in search_variants:
            try:
                search_payload = client.search(search_text, max_results=5, layers=search_layers or None)
            except httpx.HTTPError as exc:
                search_attempts.append({"provider": "govmap", "search_text": search_text, "search_scope": search_scope, "status": "error", "error": exc.__class__.__name__})
                continue
            result = _best_search_result(search_payload.get("results") or [])
            point = _point_from_wkt(str((result or {}).get("centroid") or "")) if result else None
            search_attempts.append({"provider": "govmap", "search_text": search_text, "search_scope": search_scope, "status": "found" if point else "empty", "result_count": int(search_payload.get("resultsCount") or len(search_payload.get("results") or []))})
            if point is None:
                continue
            center = {
                "x": point[0],
                "y": point[1],
                "source_text": search_text,
                "search_scope": search_scope,
                "provider": "govmap_search",
                "match_quality": _center_match_quality(search_text=search_text, primary_search_text=primary_search_text, search_scope=search_scope),
            }
            if _is_high_quality_center(center):
                return center, search_attempts
            fallback_center = fallback_center or center
    osm_center = _resolve_osm_center(search_texts, options=options, search_attempts=search_attempts) if options.osm_fallback else None
    if osm_center is not None:
        return osm_center, search_attempts
    return fallback_center, search_attempts


def _story_query_plans(
    story: Mapping[str, Any],
    *,
    links_by_event: Mapping[tuple[str, str], list[Mapping[str, Any]]],
    layer_labels: Mapping[str, str],
) -> list[dict[str, Any]]:
    plans: list[dict[str, Any]] = []
    seen: set[tuple[str, str, tuple[str, ...]]] = set()
    for event in story.get("timeline_events", []):
        if not isinstance(event, Mapping):
            continue
        event_id = str(event.get("event_id") or event.get("id") or "")
        artifact_id = str(event.get("artifact_id") or "")
        for link in links_by_event.get((event_id, artifact_id), []):
            for candidate in link.get("candidates", []):
                if not isinstance(candidate, Mapping):
                    continue
                query = candidate.get("real_gis_query") if isinstance(candidate.get("real_gis_query"), Mapping) else {}
                aliases = tuple(str(alias) for alias in query.get("map_layer_aliases", []) if str(alias or ""))
                key = (str(candidate.get("layer_key") or ""), str(query.get("search_text") or ""), aliases)
                if key in seen:
                    continue
                seen.add(key)
                plans.append(
                    {
                        "event_id": event_id,
                        "artifact_id": artifact_id,
                        "layer_key": str(candidate.get("layer_key") or ""),
                        "layer_display_name_he": str(candidate.get("layer_display_name_he") or layer_labels.get(str(candidate.get("layer_key") or ""), "")),
                        "candidate_confidence_label": str(candidate.get("confidence_label") or ""),
                        "search_text": str(query.get("search_text") or ""),
                        "query_kind": str(query.get("query_kind") or ""),
                        "map_layer_aliases": list(aliases),
                        "real_gis_query": dict(query),
                    }
                )
    return plans


def _select_stories(rows: Iterable[Any], *, story_ids: Sequence[str], max_stories: int) -> list[Mapping[str, Any]]:
    stories = [row for row in rows if isinstance(row, Mapping)]
    if story_ids:
        wanted = set(story_ids)
        return [row for row in stories if str(row.get("story_id") or "") in wanted]
    return stories[: max(0, int(max_stories or 0))]


def _links_by_event(rows: Iterable[Any]) -> dict[tuple[str, str], list[Mapping[str, Any]]]:
    out: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        key = (str(row.get("event_id") or ""), str(row.get("artifact_id") or ""))
        out.setdefault(key, []).append(row)
    return out


def _layer_labels(registry: Any) -> dict[str, str]:
    return {layer.layer_key: layer.display_name_he for layer in registry.layer_groups}


def _dynamic_layer_spec(alias: str, *, label: str) -> GovMapLayerSpec:
    return GovMapLayerSpec(alias=alias, dashboard_key=alias, label_he=str(label or alias), category="story_context", fields=("objectid", "name", "shem", "SHEM", "address"), color="#2563eb")


def _best_search_result(results: Iterable[Any]) -> Mapping[str, Any] | None:
    for item in results:
        if isinstance(item, Mapping) and item.get("centroid"):
            return item
    return None


def _search_text_variants(values: Iterable[str]) -> list[str]:
    variants: list[str] = []
    for value in values:
        text = _clean_search_text(value)
        if not text:
            continue
        candidates = {text}
        replacements = (
            ("אומנויות", "אמנויות"),
            ("אמנויות", "אומנויות"),
            ("לאומנויות", "לאמנויות"),
            ("לאמנויות", "לאומנויות"),
        )
        for source, target in replacements:
            if source in text:
                candidates.add(text.replace(source, target))
        for candidate in list(candidates):
            words = candidate.split()
            if words and words[0].startswith("ה") and len(words[0]) > 2:
                candidates.add(" ".join([words[0][1:], *words[1:]]))
        for candidate in candidates:
            if candidate and candidate not in variants:
                variants.append(candidate)
    return variants


def _clean_search_text(value: str) -> str:
    return " ".join(str(value or "").replace("_", " ").split())


def _is_high_quality_center(center: Mapping[str, Any]) -> bool:
    return str(center.get("match_quality") or "") in {"primary_typed_geocode", "primary_unfiltered_geocode"}


def _resolve_osm_center(search_texts: Sequence[str], *, options: StoryGovMapExecutionOptions, search_attempts: list[dict[str, Any]]) -> dict[str, Any] | None:
    for search_text in _osm_search_text_variants(search_texts):
        if _looks_like_city_only_fallback(search_text):
            continue
        try:
            with httpx.Client(timeout=20.0, transport=options.osm_transport, headers={"User-Agent": options.osm_user_agent}) as client:
                response = client.get("https://nominatim.openstreetmap.org/search", params={"q": search_text, "format": "jsonv2", "limit": 1, "addressdetails": 1})
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            search_attempts.append({"provider": "osm_nominatim", "search_text": search_text, "status": "error", "error": exc.__class__.__name__})
            continue
        rows = payload if isinstance(payload, list) else []
        first = next((row for row in rows if isinstance(row, Mapping) and row.get("lat") and row.get("lon")), None)
        search_attempts.append({"provider": "osm_nominatim", "search_text": search_text, "status": "found" if first else "empty", "result_count": len(rows)})
        if first is None:
            continue
        x, y = _wgs84_to_itm(float(first["lon"]), float(first["lat"]))
        return {
            "x": x,
            "y": y,
            "lon": float(first["lon"]),
            "lat": float(first["lat"]),
            "source_text": search_text,
            "provider": "osm_nominatim",
            "match_quality": "external_open_data_candidate",
            "display_name": str(first.get("display_name") or ""),
            "manual_verification_required": True,
        }
    return None


def _osm_search_text_variants(search_texts: Sequence[str]) -> list[str]:
    out: list[str] = []
    for search_text in search_texts:
        text = _clean_search_text(search_text)
        if not text or _looks_like_city_only_fallback(text):
            continue
        for candidate in (text, f"{text}, ישראל", f"{text}, Israel"):
            if candidate not in out:
                out.append(candidate)
    return out


def _looks_like_city_only_fallback(search_text: str) -> bool:
    text = _clean_search_text(search_text).lower()
    return text in {"ashdod", "אשדוד", "tel aviv", "תל אביב", "תל אביב יפו", "beer sheva", "באר שבע", "jerusalem", "ירושלים"}


def _wgs84_to_itm(lon: float, lat: float) -> tuple[float, float]:
    x, y = Transformer.from_crs("EPSG:4326", "EPSG:2039", always_xy=True).transform(float(lon), float(lat))
    return round(float(x), 2), round(float(y), 2)


def _center_match_quality(*, search_text: str, primary_search_text: str, search_scope: str) -> str:
    exact_text = str(search_text or "").strip() == str(primary_search_text or "").strip()
    if exact_text and search_scope == "typed":
        return "primary_typed_geocode"
    if exact_text:
        return "primary_unfiltered_geocode"
    if search_scope == "typed":
        return "alternate_typed_geocode"
    return "alternate_unfiltered_geocode"


def _point_from_wkt(value: str) -> tuple[float, float] | None:
    compact = value.strip().upper()
    if not compact.startswith("POINT"):
        return None
    raw = value[value.find("(") + 1 : value.rfind(")")]
    parts = raw.replace(",", " ").split()
    if len(parts) < 2:
        return None
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return None


def _flatten_spatial_features(layers: Mapping[str, Any], *, max_features: int) -> list[dict[str, Any]]:
    features: list[dict[str, Any]] = []
    for alias, raw_items in layers.items():
        if not isinstance(raw_items, list):
            continue
        for raw in raw_items:
            if not isinstance(raw, Mapping):
                continue
            attributes = raw.get("attributes") if isinstance(raw.get("attributes"), Mapping) else {}
            features.append({"layer_alias": str(alias), "id": str(raw.get("id") or attributes.get("objectid") or len(features) + 1), "attributes": dict(attributes), "raw_keys": sorted(str(key) for key in raw.keys())})
            if len(features) >= max_features:
                return features
    return features


def live_execution_enabled_from_env() -> bool:
    return os.environ.get("GOVMAP_STORY_EXECUTION_LIVE", "0").strip().lower() in {"1", "true", "yes"}
