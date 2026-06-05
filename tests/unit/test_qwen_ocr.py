from __future__ import annotations

import pytest

from municipality.layout_parser import parse_pdf_ocr
from municipality.qwen_ocr import QwenOcrPageResult, build_qwen_ocr_prompt, parse_qwen_ocr_response


def test_qwen_ocr_prompt_forces_thinking_and_json_only() -> None:
    prompt = build_qwen_ocr_prompt(page_number=3)

    assert prompt.startswith("/think")
    assert "final answer must be JSON only" in prompt
    assert "Preserve Hebrew text order" in prompt


def test_parse_qwen_ocr_response_strips_think_block() -> None:
    response = """
<think>internal visual reasoning</think>
{"page_number": 1, "plain_text": "ועדת חינוך", "markdown_layout": "# ועדת חינוך", "tables_markdown": [], "detected_headings": ["ועדת חינוך"], "uncertain_regions": [], "quality_notes": "clear", "ocr_confidence": 0.92}
"""

    result = parse_qwen_ocr_response(response, page_number=1)

    assert result.error_code is None
    assert result.markdown_layout == "# ועדת חינוך"
    assert result.detected_headings == ["ועדת חינוך"]
    assert result.ocr_confidence == 0.92


def test_parse_pdf_ocr_uses_qwen_client_for_rendered_pages() -> None:
    fitz = pytest.importorskip("fitz")
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "placeholder")
    pdf_bytes = document.tobytes()
    document.close()

    class StubQwenClient:
        model_name = "qwen3.5:122b"
        provider = "test"

        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def is_configured(self) -> bool:
            return True

        def ocr_page(self, *, image_bytes: bytes, page_number: int, image_mime: str) -> QwenOcrPageResult:
            self.calls.append({"image_bytes": len(image_bytes), "page_number": page_number, "image_mime": image_mime})
            return QwenOcrPageResult(
                page_number=page_number,
                text="ועדת חינוך\n| נושא | החלטה |",
                detected_headings=["ועדת חינוך"],
                tables_markdown=["| נושא | החלטה |"],
                ocr_confidence=0.98,
            )

    client = StubQwenClient()
    result = parse_pdf_ocr(pdf_bytes, client=client)

    assert result is not None
    assert result.backend_name == "qwen_vision_ocr"
    assert result.full_text.startswith("ועדת חינוך")
    assert result.metadata["ocr_engine"] == "qwen_vision"
    assert client.calls
    assert client.calls[0]["page_number"] == 1
    assert client.calls[0]["image_bytes"]
