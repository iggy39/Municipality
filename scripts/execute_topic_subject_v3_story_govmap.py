#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from municipality.protocol_gis_story_govmap_executor import (
    DEFAULT_GIS_LINK_REPORT_PATH,
    DEFAULT_GIS_STORY_REPORT_PATH,
    StoryGovMapExecutionOptions,
    execute_story_govmap_queries,
    live_execution_enabled_from_env,
    story_govmap_execution_markdown,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Execute selected Topic Subject V3 GIS story query plans against GovMap as artifact-only output.")
    parser.add_argument("--stories-json", default=str(DEFAULT_GIS_STORY_REPORT_PATH), help="Path to v3 GIS stories report JSON.")
    parser.add_argument("--links-json", default=str(DEFAULT_GIS_LINK_REPORT_PATH), help="Path to v3 GIS links report JSON.")
    parser.add_argument("--story-id", action="append", default=[], help="Story ID to execute. May be repeated. Defaults to first story.")
    parser.add_argument("--max-stories", type=int, default=1, help="Number of stories to execute when --story-id is not supplied.")
    parser.add_argument("--max-queries-per-story", type=int, default=8, help="Maximum deduplicated query plans per story.")
    parser.add_argument("--radius-m", type=float, default=3000.0, help="GovMap radius in meters for spatial layer lookup.")
    parser.add_argument("--live", action="store_true", help="Actually call GovMap. If omitted, only writes not-executed query plans unless GOVMAP_STORY_EXECUTION_LIVE=1.")
    parser.add_argument("--no-osm-fallback", action="store_true", help="Disable OpenStreetMap/Nominatim fallback when GovMap exact geocoding fails.")
    parser.add_argument("--output-json", required=True, help="Output execution report JSON path.")
    parser.add_argument("--output-md", required=True, help="Output execution report Markdown path.")
    args = parser.parse_args(argv)

    stories_path = Path(args.stories_json)
    links_path = Path(args.links_json)
    story_report = json.loads(stories_path.read_text(encoding="utf-8"))
    link_report = json.loads(links_path.read_text(encoding="utf-8"))
    report = execute_story_govmap_queries(
        story_report=story_report,
        link_report=link_report,
        options=StoryGovMapExecutionOptions(
            story_ids=tuple(args.story_id),
            max_stories=args.max_stories,
            max_queries_per_story=args.max_queries_per_story,
            radius_m=args.radius_m,
            live=bool(args.live or live_execution_enabled_from_env()),
            osm_fallback=not args.no_osm_fallback,
        ),
    )

    output_json = Path(args.output_json)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output_md = Path(args.output_md)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(story_govmap_execution_markdown(report) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "provider": report["provider"],
                "live_execution": report["source"]["live_execution"],
                "story_count": report["source"]["story_count"],
                "status_counts": report["status_counts"],
                "json": str(output_json),
                "markdown": str(output_md),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
