#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from municipality.protocol_gis_linker import link_topic_subject_v3_events_to_gis, protocol_v3_gis_link_report_markdown


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate artifact-only GIS link reports for Topic Subject V3 events.")
    parser.add_argument("--input", action="append", default=[], help="Path to v3_events.json or a real-row experiment results.json file. May be repeated.")
    parser.add_argument("--discover-root", action="append", default=[], help="Directory to scan for v3_events.json and results.json. May be repeated.")
    parser.add_argument("--output-dir", default=None, help="Directory for v3_gis_links.json and v3_gis_links.md.")
    parser.add_argument("--output-json", default=None, help="Explicit output JSON file path.")
    parser.add_argument("--output-md", default=None, help="Explicit output Markdown file path.")
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown", help="Stdout format when --output-dir is not supplied.")
    args = parser.parse_args(argv)

    input_paths = _collect_input_paths([Path(value) for value in args.input], [Path(value) for value in args.discover_root])
    if not input_paths:
        raise SystemExit("provide at least one --input or --discover-root")
    links_payload: list[dict[str, Any]] = []
    all_links = []
    errors: list[dict[str, str]] = []
    for input_path in input_paths:
        try:
            payload = json.loads(input_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append({"input": str(input_path), "error": exc.__class__.__name__})
            continue
        if not isinstance(payload, list):
            errors.append({"input": str(input_path), "error": "input_not_json_array"})
            continue
        payload = _prepare_payload_for_input(payload, input_path)
        links = link_topic_subject_v3_events_to_gis(payload)
        all_links.extend(links)
        for link in links:
            row = link.to_payload()
            row["source_artifact_file"] = str(input_path)
            links_payload.append(row)

    output_dir = Path(args.output_dir) if args.output_dir else None
    json_path = Path(args.output_json) if args.output_json else (output_dir / "v3_gis_links.json" if output_dir else None)
    md_path = Path(args.output_md) if args.output_md else (output_dir / "v3_gis_links.md" if output_dir else None)
    if output_dir or json_path or md_path:
        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)
        if json_path:
            json_path.parent.mkdir(parents=True, exist_ok=True)
            json_path.write_text(
                json.dumps(
                    {
                        "input_files": [str(path) for path in input_paths],
                        "input_file_count": len(input_paths),
                        "event_link_count": len(links_payload),
                        "errors": errors,
                        "links": links_payload,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        if md_path:
            md_path.parent.mkdir(parents=True, exist_ok=True)
            md_path.write_text(protocol_v3_gis_link_report_markdown(all_links) + "\n", encoding="utf-8")
        print(
            json.dumps(
                {
                    "input_files": len(input_paths),
                    "links": len(links_payload),
                    "errors": len(errors),
                    "json": str(json_path) if json_path else None,
                    "markdown": str(md_path) if md_path else None,
                },
                ensure_ascii=False,
            )
        )
        return 0

    if args.format == "json":
        print(json.dumps(links_payload, ensure_ascii=False, indent=2))
    else:
        print(protocol_v3_gis_link_report_markdown(all_links))
    return 0


def _collect_input_paths(inputs: Sequence[Path], roots: Sequence[Path]) -> list[Path]:
    paths: list[Path] = []
    for path in inputs:
        if path.is_dir():
            paths.extend(_discover_inputs(path))
        else:
            paths.append(path)
    for root in roots:
        paths.extend(_discover_inputs(root))
    out: list[Path] = []
    seen: set[str] = set()
    for path in sorted(paths, key=lambda item: str(item)):
        resolved = str(path.resolve())
        if resolved in seen:
            continue
        seen.add(resolved)
        out.append(path)
    return out


def _discover_inputs(root: Path) -> list[Path]:
    if not root.exists():
        return []
    if root.is_file():
        return [root] if root.name in {"v3_events.json", "results.json"} else []
    paths: list[Path] = []
    for name in ("v3_events.json", "results.json"):
        paths.extend(root.rglob(name))
    return paths


def _prepare_payload_for_input(payload: list[Any], input_path: Path) -> list[Any]:
    if input_path.name != "v3_events.json":
        return payload
    municipality_slug = _infer_municipality_from_path(input_path)
    if not municipality_slug:
        return payload
    prepared: list[Any] = []
    for item in payload:
        if isinstance(item, dict):
            artifact_id = str(item.get("artifact_id") or item.get("target_artifact_id") or "")
            source_ordinal = str(item.get("source_ordinal") or item.get("event_index") or "")
            prepared.append(
                {
                    "row": f"{municipality_slug}:artifact:{source_ordinal}" if source_ordinal else f"{municipality_slug}:artifact",
                    "artifact_id": artifact_id,
                    "municipality_slug": municipality_slug,
                    "event": item,
                }
            )
        else:
            prepared.append(item)
    return prepared


def _infer_municipality_from_path(path: Path) -> str:
    text = str(path).lower()
    for slug in ("tel_aviv", "beer_sheva", "jerusalem", "ashdod"):
        if slug in text:
            return slug
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
