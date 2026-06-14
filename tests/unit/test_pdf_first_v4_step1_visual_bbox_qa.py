from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_step1_qa_module():
    path = Path(__file__).resolve().parents[2] / "rag_eval/runs/ashdod_2025_regular_3_short_pdf/pdf_first_pipeline/scripts/step1_visual_bbox_qa.py"
    spec = importlib.util.spec_from_file_location("step1_visual_bbox_qa", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


step1_qa = _load_step1_qa_module()


def test_block_summary_compacts_bbox_coordinates() -> None:
    summary = step1_qa._block_summary(
        {
            "blocks": [
                {
                    "block_id": "p32_b1",
                    "bbox": [176.78, 56.714, 513.58, 162.332],
                    "text": "ignored to keep visual prompt compact",
                }
            ]
        }
    )

    assert summary == [{"block_id": "p32_b1", "bbox": [177, 57, 514, 162]}]
