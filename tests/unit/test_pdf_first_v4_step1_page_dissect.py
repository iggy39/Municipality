from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_step1_module():
    path = (
        Path(__file__).resolve().parents[2]
        / "rag_eval/runs/ashdod_2025_regular_3_short_pdf/pdf_first_pipeline/scripts/step1_page_dissect.py"
    )
    spec = importlib.util.spec_from_file_location("step1_page_dissect", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Rect:
    width = 595.0
    height = 842.0


class _Page:
    rect = _Rect()


step1 = _load_step1_module()


def test_page_record_uses_word_reconstructed_line_text() -> None:
    raw = {
        "blocks": [
            {
                "type": 0,
                "bbox": [286.37, 404.51, 516.022, 416.51],
                "lines": [
                    {
                        "bbox": [286.37, 404.51, 516.022, 416.51],
                        "spans": [
                            {
                                "text": ". דקות של אמיר בדראן לתת לך10 שולה, אני לקחתי",
                                "size": 12,
                                "flags": 0,
                            }
                        ],
                    }
                ],
            }
        ]
    }
    page_words = [
        (291.9, 404.5, 294.9, 416.5, ".", 0, 0, 0),
        (295.0, 404.5, 306.0, 416.5, "לך", 0, 0, 1),
        (308.7, 404.5, 327.9, 416.5, "לתת", 0, 0, 2),
        (330.5, 404.5, 358.1, 416.5, "בדראן", 0, 0, 3),
        (360.7, 404.5, 383.9, 416.5, "אמיר", 0, 0, 4),
        (386.6, 404.5, 399.1, 416.5, "של", 0, 0, 5),
        (401.9, 404.5, 425.2, 416.5, "דקות", 0, 0, 6),
        (427.9, 404.5, 438.5, 416.5, "10", 0, 0, 7),
        (441.2, 404.5, 470.7, 416.5, "לקחתי", 0, 0, 8),
        (473.3, 404.5, 487.6, 416.5, "אני", 0, 0, 9),
        (490.3, 404.5, 493.2, 416.5, ",", 0, 0, 10),
        (493.2, 404.5, 516.0, 416.5, "שולה", 0, 0, 11),
    ]

    record = step1._page_record(
        page=_Page(),
        page_number=23,
        raw=raw,
        page_words=page_words,
        image_path="page_images/page_023.png",
    )

    expected = "שולה, אני לקחתי 10 דקות של אמיר בדראן לתת לך."
    line = record["blocks"][0]["lines"][0]
    assert line["text"] == expected
    assert line["raw_extractor_text"] == ". דקות של אמיר בדראן לתת לך10 שולה, אני לקחתי"
    assert line["text_reconstructed_from_words"] is True
    assert line["reconstruction_source"] == "pymupdf_words_directional_runs"
    assert record["blocks"][0]["text"] == expected
    assert record["plain_text"] == expected
