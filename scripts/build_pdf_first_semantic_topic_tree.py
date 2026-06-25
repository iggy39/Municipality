from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from sqlalchemy.orm import Session

from municipality.db import build_engine
from municipality.migrations import apply_all
from municipality.pdf_first_semantic_bridge import BridgeConfig, build_pdf_first_semantic_topic_tree


def main() -> int:
    parser = argparse.ArgumentParser(description="Bridge PDF-first topic annotations into semantic topic tree")
    parser.add_argument("--muni", default="ashdod", help="Municipality slug")
    parser.add_argument("--write", action="store_true", help="Mutate the database; default is dry-run")
    parser.add_argument("--replace-links", action="store_true", help="Replace existing bridge artifact links")
    parser.add_argument("--min-artifact-count", type=int, default=1, help="Only bridge labels seen in at least N artifacts")
    parser.add_argument("--llm-cleanup", action="store_true", help="Use DictaLM/Ollama to clean labels before persistence")
    parser.add_argument("--model", default="dicta-il/DictaLM-3.0-24B-Thinking:bf16")
    parser.add_argument("--ollama-base-url", default="http://localhost:11434")
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument("--llm-batch-size", type=int, default=35)
    args = parser.parse_args()

    engine = build_engine()
    apply_all(engine, Path("migrations"))
    with Session(engine) as session:
        result = build_pdf_first_semantic_topic_tree(
            session,
            config=BridgeConfig(
                municipality_slug=args.muni,
                min_artifact_count=int(args.min_artifact_count),
                write=bool(args.write),
                replace_links=bool(args.replace_links),
                llm_cleanup=bool(args.llm_cleanup),
                model_name=args.model,
                ollama_base_url=args.ollama_base_url,
                timeout_seconds=float(args.timeout_seconds),
                llm_batch_size=int(args.llm_batch_size),
            ),
        )
    print(json.dumps(result.as_dict(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
