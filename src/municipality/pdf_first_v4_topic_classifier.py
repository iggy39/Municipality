from __future__ import annotations

import re
from typing import Any

from municipality.chunking import normalize_for_search
from municipality.pdf_first_v4_topic_tree import ROOT_BY_ID, V4_ROOT_TOPICS, root_label_for_id
from municipality.topic_label_quality import is_low_quality_topic_label


AUTO_ACCEPT_SCORE = 0.88
AUTO_ACCEPT_MARGIN = 0.12
PROCEDURAL_CARRIER_ROOTS = {"root_agenda_queries", "root_order_proposals"}
PROCEDURAL_CARRIER_TERMS = {"שאילתה", "שאילתא", "הצעה לסדר", "סדר יום", "אישור", "החלטות"}


def build_topic_profile_index(topic_tree: dict[str, Any] | None) -> dict[str, Any]:
    """Build a compact inverted index for reusable topic-tree profiles.

    This is a lightweight local index: roots/children/profiles/examples are read once
    from the tree snapshot and converted into token -> topic-entry postings. Runtime
    classification then searches a small candidate set instead of scanning the full
    tree profile for every agenda row.
    """
    entries: list[dict[str, Any]] = []
    postings: dict[str, set[int]] = {}
    roots = (topic_tree or {}).get("root_topics") or (topic_tree or {}).get("roots") or list(V4_ROOT_TOPICS)
    for root in roots:
        root_topic_id = str(root.get("root_topic_id") or "")
        if root_topic_id not in ROOT_BY_ID:
            continue
        root_label = str(root.get("root_label_he") or root_label_for_id(root_topic_id) or "")
        root_profile = root.get("profile") if isinstance(root.get("profile"), dict) else {}
        fields = _profile_fields(root_label, root.get("keywords") or [], root_profile)
        entry = _index_entry(root_topic_id=root_topic_id, root_label=root_label, child_choice_id=None, child_label=None, fields=fields, source="topic_profile_root", support_count=int(root.get("support_count") or 0))
        _append_index_entry(entries=entries, postings=postings, entry=entry)
        for child in root.get("children") or []:
            if not _importable_tree_child(child=child, root_topic_id=root_topic_id):
                continue
            child_label = str(child.get("child_label_he") or child.get("label_he") or "")
            child_profile = child.get("profile") if isinstance(child.get("profile"), dict) else {}
            child_fields = _profile_fields(child_label, child_profile.get("aliases_he") or [], child_profile)
            child_entry = _index_entry(root_topic_id=root_topic_id, root_label=root_label, child_choice_id=child.get("child_topic_id") or child.get("topic_id"), child_label=child_label, fields=child_fields, source="topic_profile_child", support_count=int(child.get("support_count") or 0))
            _append_index_entry(entries=entries, postings=postings, entry=child_entry)
    return {"method": "topic_profile_inverted_index_v1", "entries": entries, "postings": {key: sorted(value) for key, value in postings.items()}}


def find_topic_candidates(
    *,
    text: str,
    evidence_text: str = "",
    topic_tree: dict[str, Any] | None = None,
    topic_index: dict[str, Any] | None = None,
    policy_matches: list[dict[str, Any]] | None = None,
    child_candidates: list[dict[str, Any]] | None = None,
    is_topic_bearing: bool = True,
    limit: int = 6,
) -> dict[str, Any]:
    """Score topic-tree candidates without an LLM.

    The matcher is intentionally deterministic: exact normalized phrases, Hebrew
    token overlap, policy hits, aliases, and previous child examples all produce
    explainable scores. Dicta should only judge cases where these signals are weak
    or conflicting.
    """
    search_text = _search_blob(text=text, evidence_text=evidence_text)
    rows: list[dict[str, Any]] = []
    rows.extend(_policy_root_candidates(policy_matches or []))
    rows.extend(_child_root_candidates(child_candidates or []))
    rows.extend(_indexed_topic_candidates(topic_index or {}, search_text=search_text))
    if not rows:
        rows.extend(_tree_root_candidates(topic_tree or {}, search_text=search_text))
    rows = [_apply_carrier_penalty(row, search_text=search_text) for row in rows]
    rows = _dedupe_candidates(rows)
    best = rows[0] if rows else None
    second = rows[1] if len(rows) > 1 else None
    decision = _decision(best=best, second=second, is_topic_bearing=is_topic_bearing)
    return {
        "method": "deterministic_topic_profile_index_v2" if topic_index else "deterministic_topic_tree_candidate_finder_v1",
        "candidate_count": len(rows),
        "candidates": rows[: max(1, int(limit))],
        "decision": decision,
    }


def _policy_root_candidates(policy_matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, match in enumerate(policy_matches[:3]):
        root_topic_id = str(match.get("root_topic_id") or "")
        if root_topic_id not in ROOT_BY_ID:
            continue
        score = 0.98 - index * 0.03
        rows.append(
            {
                "root_topic_id": root_topic_id,
                "root_label_he": root_label_for_id(root_topic_id),
                "child_choice_id": None,
                "child_label_he": None,
                "score": round(score, 4),
                "source": "topic_policy",
                "reason": "policy_match",
                "policy_id": match.get("policy_id"),
                "matched_terms": match.get("matched_terms") or [],
            }
        )
    return rows


def _child_root_candidates(child_candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for child in child_candidates[:12]:
        root_topic_id = str(child.get("root_topic_id") or "")
        if root_topic_id not in ROOT_BY_ID:
            continue
        confidence = _float(child.get("confidence_hint"), default=0.0)
        if confidence <= 0.0:
            continue
        source = str(child.get("evidence_source") or "child_candidate")
        source_bonus = 0.04 if source in {"existing_tree", "referenced_attachment"} else 0.0
        score = min(0.94, confidence + source_bonus)
        rows.append(
            {
                "root_topic_id": root_topic_id,
                "root_label_he": child.get("root_label_he") or root_label_for_id(root_topic_id),
                "child_choice_id": child.get("candidate_child_id"),
                "child_label_he": child.get("label_he"),
                "score": round(score, 4),
                "source": source,
                "reason": "child_candidate_match",
                "matched_terms": _tokens(str(child.get("label_he") or ""))[:6],
            }
        )
    return rows


def _tree_root_candidates(topic_tree: dict[str, Any], *, search_text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    roots = topic_tree.get("root_topics") or topic_tree.get("roots") or list(V4_ROOT_TOPICS)
    for root in roots:
        root_topic_id = str(root.get("root_topic_id") or "")
        if root_topic_id not in ROOT_BY_ID:
            continue
        label = str(root.get("root_label_he") or root_label_for_id(root_topic_id) or "")
        keyword_scores = [_phrase_score(str(keyword), search_text) for keyword in root.get("keywords") or []]
        label_score = _phrase_score(label, search_text) * 0.92
        alias_scores = []
        profile = root.get("profile") if isinstance(root.get("profile"), dict) else {}
        for alias in profile.get("aliases_he") or []:
            alias_scores.append(_phrase_score(str(alias), search_text) * 0.9)
        child_scores = []
        for child in root.get("children") or []:
            if not _importable_tree_child(child=child, root_topic_id=root_topic_id):
                continue
            child_label = str(child.get("child_label_he") or child.get("label_he") or "")
            child_scores.append(_phrase_score(child_label, search_text) * 0.84)
            child_profile = child.get("profile") if isinstance(child.get("profile"), dict) else {}
            for alias in child_profile.get("aliases_he") or []:
                child_scores.append(_phrase_score(str(alias), search_text) * 0.82)
            for example in child_profile.get("positive_examples") or []:
                if isinstance(example, dict):
                    child_scores.append(_example_overlap_score(str(example.get("quote_he") or ""), search_text))
        score = max([label_score, *keyword_scores, *alias_scores, *child_scores, 0.0])
        if score < 0.36:
            continue
        rows.append(
            {
                "root_topic_id": root_topic_id,
                "root_label_he": label,
                "child_choice_id": None,
                "child_label_he": None,
                "score": round(min(0.86, score), 4),
                "source": "topic_tree_root",
                "reason": "root_label_keyword_or_profile_match",
                "matched_terms": _matched_keywords(root, search_text),
            }
        )
    return rows


def _indexed_topic_candidates(topic_index: dict[str, Any], *, search_text: str) -> list[dict[str, Any]]:
    entries = topic_index.get("entries") if isinstance(topic_index.get("entries"), list) else []
    postings = topic_index.get("postings") if isinstance(topic_index.get("postings"), dict) else {}
    if not entries or not postings:
        return []
    candidate_ids: set[int] = set()
    for token in _token_variants(search_text):
        for entry_id in postings.get(token, [])[:80]:
            candidate_ids.add(int(entry_id))
    rows: list[dict[str, Any]] = []
    for entry_id in candidate_ids:
        if entry_id < 0 or entry_id >= len(entries):
            continue
        entry = entries[entry_id]
        score, matched_terms = _indexed_entry_score(entry=entry, search_text=search_text)
        if score < 0.34:
            continue
        rows.append(
            {
                "root_topic_id": entry.get("root_topic_id"),
                "root_label_he": entry.get("root_label_he"),
                "child_choice_id": entry.get("child_choice_id"),
                "child_label_he": entry.get("child_label_he"),
                "score": round(min(0.9, score), 4),
                "source": entry.get("source") or "topic_profile_index",
                "reason": "indexed_topic_profile_match",
                "matched_terms": matched_terms,
            }
        )
    return rows


def _indexed_entry_score(*, entry: dict[str, Any], search_text: str) -> tuple[float, list[str]]:
    weighted_phrases = entry.get("weighted_phrases") if isinstance(entry.get("weighted_phrases"), list) else []
    scores: list[float] = []
    matched_terms: list[str] = []
    for row in weighted_phrases:
        if not isinstance(row, dict):
            continue
        phrase = str(row.get("phrase") or "")
        weight = _float(row.get("weight"), default=1.0)
        phrase_score = _phrase_score(phrase, search_text)
        if phrase_score <= 0.0:
            continue
        scores.append(phrase_score * weight)
        if len(matched_terms) < 8:
            matched_terms.append(phrase)
    token_overlap = _profile_token_overlap_score(entry=entry, search_text=search_text)
    support_bonus = min(0.05, _float(entry.get("support_count"), default=0.0) / 200.0)
    return max([*scores, token_overlap, 0.0]) + support_bonus, _dedupe_terms(matched_terms)


def _profile_token_overlap_score(*, entry: dict[str, Any], search_text: str) -> float:
    profile_tokens = set(entry.get("tokens") or [])
    search_tokens = set(_token_variants(search_text))
    if not profile_tokens or not search_tokens:
        return 0.0
    hits = profile_tokens & search_tokens
    if len(hits) < 2:
        return 0.0
    ratio = len(hits) / max(1, min(len(profile_tokens), len(search_tokens)))
    return min(0.74, 0.36 + ratio * 0.36)


def _apply_carrier_penalty(row: dict[str, Any], *, search_text: str) -> dict[str, Any]:
    root_topic_id = str(row.get("root_topic_id") or "")
    if root_topic_id not in PROCEDURAL_CARRIER_ROOTS:
        return row
    substantive_tokens = [token for token in _token_variants(search_text) if token not in {normalize_for_search(term) for term in PROCEDURAL_CARRIER_TERMS}]
    if len(substantive_tokens) < 3:
        return row
    adjusted = dict(row)
    adjusted["score"] = round(min(_float(row.get("score"), default=0.0), 0.54), 4)
    adjusted["reason"] = f"{row.get('reason') or 'match'}:procedural_carrier_downranked"
    adjusted["carrier_penalty"] = True
    return adjusted


def _profile_fields(label: str, keywords: list[Any], profile: dict[str, Any]) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    _add_field(fields, label, weight=1.0)
    for keyword in keywords:
        _add_field(fields, str(keyword), weight=0.98)
    _add_field(fields, str(profile.get("summary_he") or ""), weight=0.72)
    for alias in profile.get("aliases_he") or []:
        _add_field(fields, str(alias), weight=0.94)
    for example in profile.get("positive_examples") or []:
        if isinstance(example, dict):
            _add_field(fields, str(example.get("quote_he") or ""), weight=0.42)
    for example in profile.get("negative_examples") or []:
        if isinstance(example, dict):
            _add_field(fields, str(example.get("quote_he") or example.get("text_he") or ""), weight=-0.4)
    return fields


def _importable_tree_child(*, child: dict[str, Any], root_topic_id: str) -> bool:
    if str(child.get("status") or "active") != "active":
        return False
    child_root_id = str(child.get("root_topic_id") or root_topic_id)
    if child_root_id in ROOT_BY_ID and child_root_id != root_topic_id:
        return False
    label = str(child.get("child_label_he") or child.get("label_he") or "")
    return bool(label.strip()) and not is_low_quality_topic_label(label)


def _add_field(fields: list[dict[str, Any]], text: str, *, weight: float) -> None:
    compact = " ".join(str(text or "").split())
    if compact:
        fields.append({"text": compact[:600], "weight": weight})


def _index_entry(*, root_topic_id: str, root_label: str, child_choice_id: Any, child_label: str | None, fields: list[dict[str, Any]], source: str, support_count: int) -> dict[str, Any]:
    weighted_phrases: list[dict[str, Any]] = []
    tokens: set[str] = set()
    for field in fields:
        text = str(field.get("text") or "")
        weight = _float(field.get("weight"), default=1.0)
        if weight < 0:
            continue
        for phrase in _candidate_phrases(text):
            weighted_phrases.append({"phrase": phrase, "weight": weight})
        tokens.update(_token_variants(normalize_for_search(text)))
    return {
        "root_topic_id": root_topic_id,
        "root_label_he": root_label,
        "child_choice_id": child_choice_id,
        "child_label_he": child_label,
        "source": source,
        "support_count": support_count,
        "weighted_phrases": _dedupe_weighted_phrases(weighted_phrases)[:80],
        "tokens": sorted(tokens),
    }


def _append_index_entry(*, entries: list[dict[str, Any]], postings: dict[str, set[int]], entry: dict[str, Any]) -> None:
    entry_id = len(entries)
    entries.append(entry)
    for token in entry.get("tokens") or []:
        postings.setdefault(str(token), set()).add(entry_id)


def _candidate_phrases(text: str) -> list[str]:
    normalized = normalize_for_search(text)
    tokens = _tokens(normalized)
    phrases = [normalized] if 1 <= len(tokens) <= 8 else []
    for size in (2, 3, 4):
        for index in range(0, max(0, len(tokens) - size + 1)):
            phrases.append(" ".join(tokens[index : index + size]))
    return _dedupe_terms(phrases)


def _dedupe_weighted_phrases(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best: dict[str, float] = {}
    for row in rows:
        phrase = normalize_for_search(str(row.get("phrase") or ""))
        if not phrase:
            continue
        best[phrase] = max(best.get(phrase, 0.0), _float(row.get("weight"), default=1.0))
    return [{"phrase": phrase, "weight": weight} for phrase, weight in sorted(best.items(), key=lambda item: (item[1], len(item[0])), reverse=True)]


def _dedupe_terms(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        key = normalize_for_search(text)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _decision(*, best: dict[str, Any] | None, second: dict[str, Any] | None, is_topic_bearing: bool) -> dict[str, Any]:
    if not is_topic_bearing:
        return {"action": "non_topic", "needs_dicta": False, "confidence": 0.35, "reason": "not_topic_bearing"}
    if not best:
        return {"action": "low_confidence", "needs_dicta": True, "confidence": 0.0, "reason": "no_candidate"}
    best_score = _float(best.get("score"), default=0.0)
    second_score = _float((second or {}).get("score"), default=0.0)
    margin = best_score - second_score
    source = str(best.get("source") or "")
    if source == "topic_policy" and best_score >= 0.92:
        return _decision_from_best(best, action="choose_existing_topic", confidence=best_score, reason="strong_policy_match")
    if best.get("child_choice_id") and best_score >= 0.86 and margin >= 0.06:
        return _decision_from_best(best, action="choose_existing_topic", confidence=best_score, reason="strong_existing_child_match")
    if best_score >= AUTO_ACCEPT_SCORE and margin >= AUTO_ACCEPT_MARGIN:
        return _decision_from_best(best, action="choose_existing_topic", confidence=best_score, reason="strong_root_match")
    if best_score >= 0.72:
        return _decision_from_best(best, action="needs_judge", confidence=best_score, reason="ambiguous_candidates", needs_dicta=True)
    return _decision_from_best(best, action="low_confidence", confidence=best_score, reason="weak_candidate", needs_dicta=True)


def _decision_from_best(best: dict[str, Any], *, action: str, confidence: float, reason: str, needs_dicta: bool = False) -> dict[str, Any]:
    return {
        "action": action,
        "needs_dicta": needs_dicta,
        "confidence": round(max(0.0, min(1.0, confidence)), 4),
        "reason": reason,
        "root_topic_id": best.get("root_topic_id"),
        "root_label_he": best.get("root_label_he"),
        "child_choice_id": best.get("child_choice_id"),
        "child_label_he": best.get("child_label_he"),
        "source": best.get("source"),
        "policy_id": best.get("policy_id"),
        "matched_terms": best.get("matched_terms") or [],
    }


def _search_blob(*, text: str, evidence_text: str) -> str:
    return normalize_for_search(" ".join(str(value or "") for value in [text, evidence_text]))


def _phrase_score(phrase: str, search_text: str) -> float:
    phrase_norm = normalize_for_search(phrase)
    if not phrase_norm or not search_text:
        return 0.0
    phrase_tokens = _tokens(phrase_norm)
    if len(phrase_tokens) == 1 and len(phrase_tokens[0]) <= 3:
        return 0.86 if _short_hebrew_keyword_supported(phrase_tokens[0], search_text) else 0.0
    if phrase_norm in search_text:
        return 0.86 if len(_tokens(phrase_norm)) <= 1 else 0.92
    if not phrase_tokens:
        return 0.0
    search_tokens = set(_token_variants(search_text))
    hits = [token for token in phrase_tokens if token in search_tokens]
    ratio = len(hits) / max(1, len(phrase_tokens))
    if len(hits) >= min(len(phrase_tokens), max(2, len(phrase_tokens) - 1)):
        return 0.58 + ratio * 0.22
    if len(hits) >= 2:
        return 0.46 + ratio * 0.16
    return 0.0


def _short_hebrew_keyword_supported(keyword: str, search_text: str) -> bool:
    """Match short Hebrew keywords as tokens, while allowing attached prefixes.

    This prevents words like "עתודת" from matching the keyword "דת", but still
    allows ordinary Hebrew prefix forms such as "הדת" or "ובדת".
    """

    if not keyword:
        return False
    for token in _tokens(search_text):
        if token == keyword:
            return True
        stripped = token
        while len(stripped) > len(keyword) and stripped[0] in "ובכלמהש":
            stripped = stripped[1:]
            if stripped == keyword:
                return True
    return False


def _example_overlap_score(example: str, search_text: str) -> float:
    example_tokens = set(_tokens(normalize_for_search(example)))
    search_tokens = set(_token_variants(search_text))
    if len(example_tokens) < 3 or not search_tokens:
        return 0.0
    hits = example_tokens & search_tokens
    ratio = len(hits) / max(1, len(example_tokens))
    return 0.38 + ratio * 0.18 if len(hits) >= 3 else 0.0


def _matched_keywords(root: dict[str, Any], search_text: str) -> list[str]:
    out = []
    for keyword in root.get("keywords") or []:
        keyword_norm = normalize_for_search(str(keyword))
        if keyword_norm and _phrase_score(keyword_norm, search_text) >= 0.46:
            out.append(str(keyword))
    return out[:8]


def _dedupe_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        root_topic_id = str(row.get("root_topic_id") or "")
        child_key = str(row.get("child_choice_id") or row.get("child_label_he") or "")
        key = (root_topic_id, normalize_for_search(child_key))
        if not root_topic_id:
            continue
        previous = best_by_key.get(key)
        if previous is None or _float(row.get("score"), default=0.0) > _float(previous.get("score"), default=0.0):
            best_by_key[key] = row
    rows = list(best_by_key.values())
    rows.sort(key=lambda row: (_float(row.get("score"), default=0.0), bool(row.get("child_choice_id"))), reverse=True)
    return rows


def _tokens(value: str) -> list[str]:
    return [token for token in re.findall(r"[\u0590-\u05FF]{2,}", value) if len(token) >= 2]


def _token_variants(value: str) -> list[str]:
    tokens = _tokens(value)
    variants = set(tokens)
    for token in tokens:
        stripped = token
        while len(stripped) >= 3 and stripped[0] in "ובכלמהש":
            stripped = stripped[1:]
            if len(stripped) >= 2:
                variants.add(stripped)
    return list(variants)


def _float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
