from __future__ import annotations

import re
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]

TEXT_FILE_SUFFIXES = {
    ".py",
    ".json",
    ".md",
    ".sql",
    ".toml",
    ".yml",
    ".yaml",
    ".txt",
}
SKIP_DIR_NAMES = {".git", "__pycache__", ".pytest_cache", ".venv", "venv", "local_llm"}
SKIP_PATH_PREFIXES = (Path("rag_eval/data"),)

BIDI_CONTROL_RE = re.compile(r"[\u200E\u200F\u202A-\u202E\u2066-\u2069]")
HEBREW_WORD_RE = re.compile(r"[א-ת]{2,}")
HEBREW_ESCAPE_WORD_RE = re.compile(r"(?:\\u05[0-9a-fA-F]{2}){2,}")

FINAL_HEBREW_LETTERS = {"ך", "ם", "ן", "ף", "ץ"}
FORWARD_SENTINEL_TERMS = (
    "פרוטוקולים",
    "פרוטוקול",
    "נספח",
    "החלטות",
    "סיכום",
    "מועצה",
    "ועדה",
    "ישיבה",
    "רגילה",
    "מיוחדת",
)
REVERSED_SENTINEL_TERMS = tuple(term[::-1] for term in FORWARD_SENTINEL_TERMS)

EXPECTED_KEY_SNIPPETS: dict[Path, tuple[str, ...]] = {
    Path("tests/unit/test_extraction.py"): (
        'HE_WELFARE_COMMITTEE = "ועדת רווחה"',
        'HE_LINE_A = "שורה א"',
        'HE_PAGE_TWO = "עמוד שני"',
        'HE_LINE_B = "שורה ב"',
        'HE_SHORT_TEXT = "טקסט קצר"',
        'HE_MORE = "עוד"',
    ),
    Path("tests/unit/test_decisions.py"): (
        '"הוחלט לאשר תקציב. "',
        '"בעד/נגד/נמנע: 9/1/0"',
        '"תוצאה: בעד 7 "',
        '"נגד 2 נמנעים 1"',
        'HE_UNANIMOUS_TEXT = "הסעיף אושר פה אחד"',
        '"התקיים דיון: "',
        '"בעד ונגד אך ללא מספרים"',
        'HE_MEETING_HEADING = "ישיבת מועצה רגילה"',
        'HE_SUMMARY_AND_DECISIONS = "סיכום והחלטות"',
        '"אישור תקציב החינוך "',
        '"מינוי ועדת ביקורת אושר פה אחד"',
        'HE_BUDGET_APPROVAL = "אישור תקציב"',
    ),
}


def _iter_text_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT_DIR.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in TEXT_FILE_SUFFIXES:
            continue
        if any(part in SKIP_DIR_NAMES for part in path.parts):
            continue
        rel = path.relative_to(ROOT_DIR)
        if any(rel.is_relative_to(prefix) for prefix in SKIP_PATH_PREFIXES):
            continue
        files.append(path)
    return files


def _assert_no_offenders(offenders: list[str], message: str) -> None:
    if not offenders:
        return
    preview = "\n".join(f"- {item}" for item in offenders[:20])
    extra = f"\n... and {len(offenders) - 20} more" if len(offenders) > 20 else ""
    raise AssertionError(f"{message}\n{preview}{extra}")


def test_project_text_files_do_not_contain_bidi_control_marks() -> None:
    offenders: list[str] = []
    for path in _iter_text_files():
        content = path.read_text(encoding="utf-8")
        if BIDI_CONTROL_RE.search(content):
            offenders.append(str(path.relative_to(ROOT_DIR)))
    _assert_no_offenders(
        offenders,
        "Found bidi control marks (U+200E/U+200F/U+202A..U+202E/U+2066..U+2069). Remove them to avoid RTL/LTR rendering issues.",
    )


def test_project_text_files_do_not_use_escaped_hebrew_words() -> None:
    offenders: list[str] = []
    for path in _iter_text_files():
        content = path.read_text(encoding="utf-8")
        if HEBREW_ESCAPE_WORD_RE.search(content):
            offenders.append(str(path.relative_to(ROOT_DIR)))
    _assert_no_offenders(
        offenders,
        "Found escaped Hebrew words (\\u05xx sequences). Keep Hebrew as readable UTF-8 text in source files.",
    )


def test_project_text_files_do_not_contain_likely_reversed_hebrew_words() -> None:
    offenders: list[str] = []
    for path in _iter_text_files():
        content = path.read_text(encoding="utf-8")
        rel = str(path.relative_to(ROOT_DIR))

        for token in REVERSED_SENTINEL_TERMS:
            if token in content:
                offenders.append(f"{rel}: {token}")
                break
        else:
            for match in HEBREW_WORD_RE.finditer(content):
                word = match.group(0)
                if any(ch in FINAL_HEBREW_LETTERS for ch in word[:-1]):
                    offenders.append(f"{rel}: {word}")
                    break

    _assert_no_offenders(
        offenders,
        "Found likely reversed Hebrew words. Check the listed tokens and store Hebrew in normal writing order.",
    )


def test_key_test_files_keep_expected_logical_hebrew_snippets() -> None:
    offenders: list[str] = []
    for relative_path, snippets in EXPECTED_KEY_SNIPPETS.items():
        content = (ROOT_DIR / relative_path).read_text(encoding="utf-8")
        for snippet in snippets:
            if snippet not in content:
                offenders.append(f"{relative_path}: missing {snippet}")
    _assert_no_offenders(
        offenders,
        "Expected Hebrew snippets changed or became visually reversed in key test files.",
    )
