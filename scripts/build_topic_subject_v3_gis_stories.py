#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from municipality.protocol_gis_story_builder import (
    build_gis_connection_summary,
    build_protocol_gis_stories,
    gis_connection_summary_markdown,
    protocol_gis_stories_markdown,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build UI-ready story timelines from Topic Subject V3 GIS link artifacts.")
    parser.add_argument("--input", required=True, help="Path to v3_gis_links JSON report.")
    parser.add_argument("--output-json", required=True, help="Story report JSON path.")
    parser.add_argument("--output-md", required=True, help="Story report Markdown path.")
    parser.add_argument("--connection-summary-json", default=None, help="Optional grouped GIS connection summary JSON path.")
    parser.add_argument("--connection-summary-md", default=None, help="Optional grouped GIS connection summary Markdown path.")
    parser.add_argument("--min-similarity", type=float, default=0.46, help="Greedy story clustering threshold.")
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    payload: Any = json.loads(input_path.read_text(encoding="utf-8"))
    story_report = build_protocol_gis_stories(payload, min_similarity=args.min_similarity)

    output_json = Path(args.output_json)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(story_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    output_md = Path(args.output_md)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(protocol_gis_stories_markdown(story_report) + "\n", encoding="utf-8")

    connection_json = Path(args.connection_summary_json) if args.connection_summary_json else None
    connection_md = Path(args.connection_summary_md) if args.connection_summary_md else None
    if connection_json or connection_md:
        connection_summary = build_gis_connection_summary(payload)
        if connection_json:
            connection_json.parent.mkdir(parents=True, exist_ok=True)
            connection_json.write_text(json.dumps(connection_summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if connection_md:
            connection_md.parent.mkdir(parents=True, exist_ok=True)
            connection_md.write_text(gis_connection_summary_markdown(connection_summary) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "input_links": story_report["source"]["input_link_count"],
                "unique_events": story_report["source"]["unique_event_count"],
                "stories": story_report["story_count"],
                "traffic_light_counts": story_report["traffic_light_counts"],
                "human_judgement_counts": story_report["human_judgement_counts"],
                "json": str(output_json),
                "markdown": str(output_md),
                "connection_summary_json": str(connection_json) if connection_json else None,
                "connection_summary_md": str(connection_md) if connection_md else None,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
