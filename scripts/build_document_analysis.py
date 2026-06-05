#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


SIDECARS = {
    "quality_report": ".quality_report.json",
    "sections": ".sections.json",
    "tables": ".tables.json",
    "metadata_facts": ".metadata_facts.json",
    "topics_entities": ".topics_entities.json",
}

OPTIONAL_SIDECARS = {
    "repair_summary": ".repair_summary.json",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build one document-level analysis JSON from OCR analysis sidecars")
    parser.add_argument("ocr_jsonl", help="Path to <pdf>.ocr.jsonl")
    parser.add_argument("--output", help="Output path. Default: <prefix>.document_analysis.json")
    parser.add_argument("--output-dir", help="Directory containing sidecars and default output. Default: OCR JSONL directory")
    parser.add_argument("--prefix", help="Sidecar filename prefix. Default: OCR JSONL stem without .ocr")
    parser.add_argument("--source-pdf", help="Original source PDF path to record in the final analysis")
    parser.add_argument("--allow-missing", action="store_true", help="Write output even when one or more sidecars are missing")
    args = parser.parse_args()

    ocr_jsonl_path = Path(args.ocr_jsonl).expanduser().resolve()
    if not ocr_jsonl_path.exists():
        print(f"OCR JSONL not found: {ocr_jsonl_path}", file=sys.stderr)
        return 1

    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else ocr_jsonl_path.parent
    prefix = args.prefix or _default_prefix(ocr_jsonl_path)
    output_path = Path(args.output).expanduser().resolve() if args.output else output_dir / f"{prefix}.document_analysis.json"

    pages = _load_jsonl(ocr_jsonl_path)
    sidecars: dict[str, dict[str, Any] | None] = {}
    missing = []
    for name, suffix in SIDECARS.items():
        path = output_dir / f"{prefix}{suffix}"
        if not path.exists():
            missing.append(str(path))
            sidecars[name] = None
            continue
        sidecars[name] = _load_json(path)
    optional_sidecars = {}
    for name, suffix in OPTIONAL_SIDECARS.items():
        path = output_dir / f"{prefix}{suffix}"
        if path.exists():
            optional_sidecars[name] = _load_json(path)

    if missing and not args.allow_missing:
        print("Missing required sidecars:", file=sys.stderr)
        for path in missing:
            print(f"- {path}", file=sys.stderr)
        print("Use --allow-missing to write a partial document analysis.", file=sys.stderr)
        return 1

    analysis = _build_analysis(
        ocr_jsonl_path=ocr_jsonl_path,
        prefix=prefix,
        source_pdf_path=Path(args.source_pdf).expanduser().resolve() if args.source_pdf else None,
        pages=pages,
        sidecars=sidecars,
        optional_sidecars=optional_sidecars,
        missing_sidecars=missing,
    )
    output_path.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
    _print_summary(output_path=output_path, analysis=analysis)
    return 0


def _default_prefix(path: Path) -> str:
    if path.name.endswith(".ocr.jsonl"):
        return path.name[: -len(".ocr.jsonl")]
    return path.stem


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"Expected JSON object in {path}")
    return payload


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
        if isinstance(payload, dict):
            records.append(payload)
    records.sort(key=lambda row: int(row.get("page_number") or 0))
    return records


def _build_analysis(
    *,
    ocr_jsonl_path: Path,
    prefix: str,
    source_pdf_path: Path | None,
    pages: list[dict[str, Any]],
    sidecars: dict[str, dict[str, Any] | None],
    optional_sidecars: dict[str, dict[str, Any]],
    missing_sidecars: list[str],
) -> dict[str, Any]:
    source_pdf = source_pdf_path or ocr_jsonl_path.with_name(f"{prefix}.pdf")
    return {
        "source_pdf": str(source_pdf) if source_pdf.exists() else None,
        "source_ocr_jsonl": str(ocr_jsonl_path),
        "page_count": len(pages),
        "missing_sidecars": missing_sidecars,
        "quality_report": _normalize_quality_report(_merged(sidecars.get("quality_report"))),
        "sections": _merged(sidecars.get("sections")),
        "tables": _merged(sidecars.get("tables")),
        "metadata_facts": _merged(sidecars.get("metadata_facts")),
        "topics_entities": _merged(sidecars.get("topics_entities")),
        "repair_summary": optional_sidecars.get("repair_summary"),
        "generation_notes": {
            name: _generation_note(payload)
            for name, payload in sidecars.items()
            if payload is not None
        },
    }


def _merged(sidecar: dict[str, Any] | None) -> dict[str, Any] | None:
    if sidecar is None:
        return None
    merged = sidecar.get("merged_result")
    return merged if isinstance(merged, dict) else sidecar.get("result")


def _normalize_quality_report(report: dict[str, Any] | None) -> dict[str, Any] | None:
    if report is None:
        return None
    copy = dict(report)
    page_quality = []
    scores = []
    for item in copy.get("page_quality") or []:
        if not isinstance(item, dict):
            page_quality.append(item)
            continue
        item_copy = dict(item)
        quality = item_copy.get("quality")
        if isinstance(quality, int | float):
            item_copy["quality"] = _normalize_quality_score(float(quality))
            scores.append(float(item_copy["quality"]))
        page_quality.append(item_copy)
    copy["page_quality"] = page_quality
    if scores:
        copy["overall_quality"] = round(sum(scores) / len(scores), 4)
    else:
        overall = copy.get("overall_quality")
        if isinstance(overall, int | float):
            copy["overall_quality"] = _normalize_quality_score(float(overall))
    return copy


def _normalize_quality_score(value: float) -> float:
    if 0 <= value <= 1:
        return round(value * 100, 4)
    return round(value, 4)


def _generation_note(sidecar: dict[str, Any]) -> dict[str, Any]:
    chunks = sidecar.get("chunks")
    return {
        "status": sidecar.get("status"),
        "pass_name": sidecar.get("pass_name"),
        "model": sidecar.get("model"),
        "elapsed_seconds": sidecar.get("elapsed_seconds"),
        "chunk_pages": sidecar.get("chunk_pages"),
        "chunk_count": len(chunks) if isinstance(chunks, list) else 0,
        "fallback_chunks": sidecar.get("fallback_chunks", 0),
        "partial_jsonl": sidecar.get("partial_jsonl"),
    }


def _print_summary(*, output_path: Path, analysis: dict[str, Any]) -> None:
    quality_report = analysis.get("quality_report") or {}
    sections = (analysis.get("sections") or {}).get("sections") or []
    tables = (analysis.get("tables") or {}).get("tables") or []
    metadata_facts = analysis.get("metadata_facts") or {}
    facts = metadata_facts.get("facts") or []
    canonical = metadata_facts.get("canonical") or {}
    topics_entities = analysis.get("topics_entities") or {}
    topics = topics_entities.get("topics") or []
    entities = topics_entities.get("entities") or []

    print(f"DOCUMENT ANALYSIS WRITTEN: {output_path}")
    print(f"OCR pages: {analysis.get('page_count')}")
    print(f"Quality pages: {len(quality_report.get('page_quality') or [])} overall: {quality_report.get('overall_quality')}")
    print(f"Sections: {len(sections)}")
    print(f"Tables: {len(tables)}")
    print(f"Metadata facts: {len(facts)} canonical: {canonical}")
    print(f"Topics: {len(topics)} Entities: {len(entities)}")

    missing = analysis.get("missing_sidecars") or []
    if missing:
        print("Missing sidecars:")
        for path in missing:
            print(f"- {path}")

    fallback_notes = {
        name: note.get("fallback_chunks")
        for name, note in (analysis.get("generation_notes") or {}).items()
        if note.get("fallback_chunks")
    }
    if fallback_notes:
        print(f"Fallback chunks: {fallback_notes}")


if __name__ == "__main__":
    raise SystemExit(main())
