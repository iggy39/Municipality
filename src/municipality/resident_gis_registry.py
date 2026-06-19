from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import yaml


DEFAULT_RESIDENT_GIS_REGISTRY_PATH = Path(__file__).resolve().parents[2] / "config" / "gis" / "resident_gis_registry.yaml"


class ResidentGisRegistryError(ValueError):
    pass


@dataclass(frozen=True)
class ResidentGisLayerGroup:
    layer_key: str
    display_name_he: str
    govmap_category_he: str
    govmap_aliases: tuple[str, ...]
    geometry: str
    tags: tuple[str, ...]
    protocol_signal_types: tuple[str, ...]
    resident_synergy_he: str
    caveats_he: str
    local_layer_keys: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> ResidentGisLayerGroup:
        return cls(
            layer_key=_required_text(row, "layer_key"),
            display_name_he=_required_text(row, "display_name_he"),
            govmap_category_he=_required_text(row, "govmap_category_he"),
            govmap_aliases=tuple(_text_list(row.get("govmap_aliases"))),
            geometry=_required_text(row, "geometry"),
            tags=tuple(_text_list(row.get("tags"))),
            protocol_signal_types=tuple(_text_list(row.get("protocol_signal_types"))),
            resident_synergy_he=_required_text(row, "resident_synergy_he"),
            caveats_he=_required_text(row, "caveats_he"),
            local_layer_keys=tuple(_text_list(row.get("local_layer_keys"))),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "layer_key": self.layer_key,
            "display_name_he": self.display_name_he,
            "govmap_category_he": self.govmap_category_he,
            "govmap_aliases": list(self.govmap_aliases),
            "local_layer_keys": list(self.local_layer_keys),
            "geometry": self.geometry,
            "tags": list(self.tags),
            "protocol_signal_types": list(self.protocol_signal_types),
            "resident_synergy_he": self.resident_synergy_he,
            "caveats_he": self.caveats_he,
        }


@dataclass(frozen=True)
class ResidentQuestionArchetype:
    question_id: str
    resident_question_he: str
    user_context_needed: tuple[str, ...]
    protocol_evidence_needs: tuple[str, ...]
    gis_need_tags: tuple[str, ...]
    layer_keys: tuple[str, ...]
    synergy_he: str
    answer_shape_he: str
    topic_affinity_examples_he: tuple[str, ...]

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> ResidentQuestionArchetype:
        return cls(
            question_id=_required_text(row, "question_id"),
            resident_question_he=_required_text(row, "resident_question_he"),
            user_context_needed=tuple(_text_list(row.get("user_context_needed"))),
            protocol_evidence_needs=tuple(_text_list(row.get("protocol_evidence_needs"))),
            gis_need_tags=tuple(_text_list(row.get("gis_need_tags"))),
            layer_keys=tuple(_text_list(row.get("layer_keys"))),
            synergy_he=_required_text(row, "synergy_he"),
            answer_shape_he=_required_text(row, "answer_shape_he"),
            topic_affinity_examples_he=tuple(_text_list(row.get("topic_affinity_examples_he"))),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "resident_question_he": self.resident_question_he,
            "user_context_needed": list(self.user_context_needed),
            "protocol_evidence_needs": list(self.protocol_evidence_needs),
            "gis_need_tags": list(self.gis_need_tags),
            "layer_keys": list(self.layer_keys),
            "synergy_he": self.synergy_he,
            "answer_shape_he": self.answer_shape_he,
            "topic_affinity_examples_he": list(self.topic_affinity_examples_he),
        }


@dataclass(frozen=True)
class ResidentGisRegistry:
    version: int
    purpose_he: str
    linking_modes: tuple[dict[str, Any], ...]
    protocol_signal_types: tuple[dict[str, Any], ...]
    layer_groups: tuple[ResidentGisLayerGroup, ...]
    question_archetypes: tuple[ResidentQuestionArchetype, ...]

    @property
    def layer_by_key(self) -> dict[str, ResidentGisLayerGroup]:
        return {layer.layer_key: layer for layer in self.layer_groups}

    @property
    def question_by_id(self) -> dict[str, ResidentQuestionArchetype]:
        return {question.question_id: question for question in self.question_archetypes}

    def to_payload(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "purpose_he": self.purpose_he,
            "linking_modes": list(self.linking_modes),
            "protocol_signal_types": list(self.protocol_signal_types),
            "layer_groups": [layer.to_payload() for layer in self.layer_groups],
            "question_archetypes": [question.to_payload() for question in self.question_archetypes],
        }


def load_resident_gis_registry(path: Path | None = None) -> ResidentGisRegistry:
    registry_path = path or DEFAULT_RESIDENT_GIS_REGISTRY_PATH
    raw = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ResidentGisRegistryError("resident GIS registry must contain a mapping")

    layers = tuple(ResidentGisLayerGroup.from_mapping(row) for row in _mapping_list(raw.get("layer_groups"), "layer_groups"))
    questions = tuple(
        ResidentQuestionArchetype.from_mapping(row)
        for row in _mapping_list(raw.get("question_archetypes"), "question_archetypes")
    )
    registry = ResidentGisRegistry(
        version=int(raw.get("version") or 1),
        purpose_he=_required_text(raw, "purpose_he"),
        linking_modes=tuple(dict(row) for row in _mapping_list(raw.get("linking_modes"), "linking_modes")),
        protocol_signal_types=tuple(
            dict(row) for row in _mapping_list(raw.get("protocol_signal_types"), "protocol_signal_types")
        ),
        layer_groups=layers,
        question_archetypes=questions,
    )
    validate_resident_gis_registry(registry)
    return registry


def validate_resident_gis_registry(registry: ResidentGisRegistry) -> None:
    layer_keys = [layer.layer_key for layer in registry.layer_groups]
    duplicate_layers = _duplicates(layer_keys)
    if duplicate_layers:
        raise ResidentGisRegistryError(f"duplicate layer keys: {', '.join(duplicate_layers)}")

    question_ids = [question.question_id for question in registry.question_archetypes]
    duplicate_questions = _duplicates(question_ids)
    if duplicate_questions:
        raise ResidentGisRegistryError(f"duplicate question ids: {', '.join(duplicate_questions)}")

    known_layers = set(layer_keys)
    for question in registry.question_archetypes:
        unknown = [layer_key for layer_key in question.layer_keys if layer_key not in known_layers]
        if unknown:
            raise ResidentGisRegistryError(f"{question.question_id} references unknown layers: {', '.join(unknown)}")


def recommend_layers_for_question(
    question: ResidentQuestionArchetype | str,
    registry: ResidentGisRegistry | None = None,
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Rank compatible GIS layers from conceptual needs, not from hardcoded Hebrew topic rules."""

    registry = registry or load_resident_gis_registry()
    question_obj = registry.question_by_id.get(question) if isinstance(question, str) else question
    if question_obj is None:
        raise ResidentGisRegistryError(f"unknown resident question archetype: {question}")

    explicit_layer_keys = set(question_obj.layer_keys)
    needed_tags = set(question_obj.gis_need_tags)
    needed_signals = set(question_obj.protocol_evidence_needs)
    ranked: list[dict[str, Any]] = []

    for layer in registry.layer_groups:
        matching_tags = sorted(needed_tags.intersection(layer.tags))
        matching_signals = sorted(needed_signals.intersection(layer.protocol_signal_types))
        explicit_score = 5 if layer.layer_key in explicit_layer_keys else 0
        score = explicit_score + (2 * len(matching_tags)) + len(matching_signals)
        if score <= 0:
            continue
        ranked.append(
            {
                "layer_key": layer.layer_key,
                "display_name_he": layer.display_name_he,
                "score": score,
                "explicit_question_layer": layer.layer_key in explicit_layer_keys,
                "matching_tags": matching_tags,
                "matching_protocol_signals": matching_signals,
                "govmap_aliases": list(layer.govmap_aliases),
                "local_layer_keys": list(layer.local_layer_keys),
                "synergy_he": layer.resident_synergy_he,
                "caveats_he": layer.caveats_he,
            }
        )

    ranked.sort(key=lambda row: (-int(row["score"]), str(row["display_name_he"]), str(row["layer_key"])))
    return ranked[:limit] if limit is not None else ranked


def resident_question_recommendation_matrix(
    registry: ResidentGisRegistry | None = None,
    *,
    layer_limit: int = 5,
) -> list[dict[str, Any]]:
    registry = registry or load_resident_gis_registry()
    rows: list[dict[str, Any]] = []
    for question in registry.question_archetypes:
        recommendations = recommend_layers_for_question(question, registry, limit=layer_limit)
        rows.append(
            {
                "question_id": question.question_id,
                "resident_question_he": question.resident_question_he,
                "protocol_evidence_needs": list(question.protocol_evidence_needs),
                "gis_need_tags": list(question.gis_need_tags),
                "recommended_layers": recommendations,
                "synergy_he": question.synergy_he,
                "answer_shape_he": question.answer_shape_he,
                "topic_affinity_examples_he": list(question.topic_affinity_examples_he),
            }
        )
    return rows


def find_questions_for_layer_tags(
    tags: Iterable[str],
    registry: ResidentGisRegistry | None = None,
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    registry = registry or load_resident_gis_registry()
    tag_set = {tag for tag in tags if tag}
    rows: list[dict[str, Any]] = []
    for question in registry.question_archetypes:
        matches = sorted(tag_set.intersection(question.gis_need_tags))
        if not matches:
            continue
        rows.append(
            {
                "question_id": question.question_id,
                "resident_question_he": question.resident_question_he,
                "matching_tags": matches,
                "score": len(matches),
            }
        )
    rows.sort(key=lambda row: (-int(row["score"]), str(row["question_id"])))
    return rows[:limit] if limit is not None else rows


def _mapping_list(value: Any, field_name: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        raise ResidentGisRegistryError(f"{field_name} must be a list")
    out: list[Mapping[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ResidentGisRegistryError(f"{field_name}[{index}] must be a mapping")
        out.append(item)
    return out


def _required_text(row: Mapping[str, Any], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ResidentGisRegistryError(f"missing required text field: {key}")
    return value.strip()


def _text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ResidentGisRegistryError("expected a list of strings")
    return [str(item).strip() for item in value if str(item).strip()]


def _duplicates(values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in values:
        if value in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(value)
    return duplicates


def _markdown_table(rows: list[dict[str, Any]]) -> str:
    lines = ["| Question | Top GIS Layers | Why GIS Helps |", "|---|---|---|"]
    for row in rows:
        layer_names = ", ".join(layer["display_name_he"] for layer in row["recommended_layers"][:4])
        lines.append(f"| {row['resident_question_he']} | {layer_names} | {row['synergy_he']} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print Resident GIS RAG question/layer recommendations.")
    parser.add_argument("--registry", type=Path, default=DEFAULT_RESIDENT_GIS_REGISTRY_PATH)
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    parser.add_argument("--question-limit", type=int, default=8)
    parser.add_argument("--layer-limit", type=int, default=5)
    args = parser.parse_args(argv)

    registry = load_resident_gis_registry(args.registry)
    rows = resident_question_recommendation_matrix(registry, layer_limit=args.layer_limit)[: max(0, args.question_limit)]
    if args.format == "json":
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print(_markdown_table(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
