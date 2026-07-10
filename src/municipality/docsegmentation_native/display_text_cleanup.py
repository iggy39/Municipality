from __future__ import annotations

import re
from typing import Any


HEBREW_CHAR_RE = re.compile(r"[\u0590-\u05FF]")
LATIN_CHAR_RE = re.compile(r"[A-Za-z]")
LETTER_RE = re.compile(r"[A-Za-z\u0590-\u05FF]")
FOOTNOTE_ACCESSIBILITY_ARTIFACT_RE = re.compile(
    r"(?:הפנייה\s*להערת\s*שוליים|הפניה\s*להערת\s*שוליים|הערת\s*שוליים\s*\d*|םיילוש\s*תרעהל\s*היינפה|םיילוש\s*תרעהל|םיילוש\s*תרעה|(?<![\u0590-\u05FF])םיילוש(?![\u0590-\u05FF])|היינפה|הפנייה|תרעה(?=[\u0590-\u05FF]))"
)
BIDI_REVERSED_PAREN_WITH_CLOSER_RE = re.compile(r"\)\(([^()\n]{1,80})\)")
BIDI_REVERSED_DATE_PAREN_RE = re.compile(r"\)\s*(\d{1,2}[./]\d{1,2}[./]\d{2,4})\s*\(")
BIDI_REVERSED_PAREN_BEFORE_DATE_RE = re.compile(r"\)\(([^()\n]{1,80}?)(?=\s+\d{1,2}[./]\d{1,2}[./]\d{2,4}(?!\d)|\s+\d{4}\b|\s*[-–—־]|\s*$)")
BIDI_REVERSED_HEBREW_PAREN_RE = re.compile(r"\)\s*([^()\n]{0,120}[\u0590-\u05FF][^()\n]{0,120}?)\s*\(")
BIDI_REVERSED_HEBREW_PAREN_ACROSS_LINES_RE = re.compile(r"\)\s*\n\s*([^()\n]{0,120}[\u0590-\u05FF][^()\n]{0,120}?)\s*\(")
BIDI_EMPTY_PAREN_BEFORE_HEBREW_RE = re.compile(r"\(\)\s*([^()\n]{0,120}[\u0590-\u05FF][^()\n]{0,120}?)(?=\n|$)")
FAKE_UNDERLINE_LEADING_RE = re.compile(r"(^|[\s\"'([{.])U\s*(?=[\u0590-\u05FF])")
FAKE_UNDERLINE_TRAILING_COLON_RE = re.compile(r"(?<=[\u0590-\u05FF0-9\"'׳״])\s*U\s*([:：])")
FAKE_UNDERLINE_TRAILING_END_RE = re.compile(r"(?<=[\u0590-\u05FF0-9\"'׳״])\s+U\s*$")
FAKE_UNDERLINE_ISOLATED_TRAILING_PAIR_RE = re.compile(r"(?:^|(?<=\s))U(?:\s+U)+\s*$")
FAKE_UNDERLINE_AFTER_HEBREW_COLON_RE = re.compile(r"(?<=[\u0590-\u05FF0-9\"'׳״:：])\s+U\s*$")
HEBREW_ABBREVIATION_JOIN_RE = re.compile(r"(?<![\u0590-\u05FF])((?:גב|דר|מר|מס|רח|עמ)['׳])(?=[\u0590-\u05FF0-9])|(?<![\u0590-\u05FF])([\u0590-\u05FF]{1,4}['׳])(?=\d)")
HEBREW_QUOTED_PHRASE_RE = re.compile(r"(?P<quote>[\"״])(?P<body>[^\"״\n]{1,120}[\u0590-\u05FF][^\"״\n]{0,120}?)(?P=quote)")
RTL_VISUAL_TWO_DIGIT_ENUMERATOR_RE = re.compile(r"^\.(\d{2})(?=\s+[\u0590-\u05FF])")
RTL_VISUAL_LEADING_PERIOD_DATE_RE = re.compile(r"^\.\s*(\d{1,2}[./]\d{1,2}[./]\d{2,4})$")
RTL_VISUAL_ZERO_THOUSANDS_SEQUENCE_RE = re.compile(r"(?<![\d,])000(?:,\s*\d{3})*,\s*\d{1,3}(?![\d,])")
RTL_VISUAL_CONTEXTUAL_GROUPED_NUMBER_RE = re.compile(r"(?<![\d,])\d{3}(?:,\s+\d{3})*,\s+\d{1,3}(?![\d,])")
NORMAL_GROUPED_NUMBER_RE = re.compile(r"\d{1,3}(?:,\d{3})+")
CURRENCY_SYMBOL_RE = re.compile(r"[₪$€£]")
SPACED_THOUSANDS_COMMA_RE = re.compile(r"(?<!\d)(\d{1,3}(?:,\d{3})*),\s+(\d{3})(?!\d)")
HEBREW_COMMA_BEFORE_COLON_ARTIFACT_RE = re.compile(r"(?<=[\u0590-\u05FF0-9\"'׳״])\s*,\s*:(?=\s|$|[\u0590-\u05FFA-Za-z0-9\"'׳״])")
HEBREW_TWO_LETTER_TRAILING_GERESH_ACRONYM_RE = re.compile(r"(?<![\u0590-\u05FF])([\u0590-\u05FF]{2})['׳](?=\s|$|[.,:;!?])")
HEBREW_CLEAN_LABEL_TOKEN_RE = re.compile(r"[\u0590-\u05FF][\u0590-\u05FF\"'׳״-]{1,}")
RTL_LINE_LEADING_PUNCT_HEBREW_PREFIX_RE = re.compile(r"^[.,;!?]+\s*([\u05D0-\u05EA])\s+(?=[\u05D0-\u05EA])")
FINAL_GERESH_ABBREVIATIONS = {"גב", "דר", "מר", "מס", "מח", "רח", "עמ"}


def cleanup_display_text(text: str) -> str:
    """Return readable display/downstream text while preserving raw_native_text elsewhere.

    The rules are intentionally deterministic and conservative. They target common
    native-PDF Hebrew/RTL display artifacts that can be identified from text shape,
    such as paired edge `U` underline markers and visually reversed parentheses.
    """
    source = _repair_multiline_reversed_parenthetical_text(str(text or ""))
    cleaned_lines: list[str] = []
    for raw_line in source.splitlines():
        line = " ".join(raw_line.split())
        if not line:
            cleaned_lines.append("")
            continue
        if line == ":" and cleaned_lines:
            cleaned_lines[-1] = cleaned_lines[-1].rstrip(" :") + ":"
            continue
        line = _remove_footnote_accessibility_artifacts(line)
        line = _repair_reversed_parenthetical_text(line)
        line = _remove_fake_underline_markers(line)
        line = _repair_rtl_visual_enumerator(line)
        line = _repair_rtl_visual_leading_period_date(line)
        line = _repair_rtl_visual_grouped_numbers(line)
        line = _repair_leading_rtl_colon(line)
        line = _repair_line_leading_punctuation_hebrew_prefix(line)
        line = _normalize_hebrew_abbreviation_spacing(line)
        line = _repair_two_letter_trailing_geresh_acronym(line)
        line = _normalize_dash_and_quote_spacing(line)
        line = _normalize_punctuation_spacing(line)
        line = _remove_fake_underline_markers(line)
        cleaned_lines.append(line.strip())
    return "\n".join(cleaned_lines).strip()


def _repair_rtl_visual_enumerator(line: str) -> str:
    return RTL_VISUAL_TWO_DIGIT_ENUMERATOR_RE.sub(lambda match: f"{match.group(1)[::-1]}.", line)


def _repair_rtl_visual_leading_period_date(line: str) -> str:
    return RTL_VISUAL_LEADING_PERIOD_DATE_RE.sub(lambda match: f"{match.group(1)}.", line)


def _repair_rtl_visual_grouped_numbers(line: str) -> str:
    line = RTL_VISUAL_ZERO_THOUSANDS_SEQUENCE_RE.sub(_reverse_rtl_visual_number_groups, line)
    return _sub_contextual_rtl_visual_grouped_numbers(line)


def _reverse_rtl_visual_number_groups(match: re.Match[str]) -> str:
    return ",".join(reversed(_rtl_visual_number_groups(match)))


def _sub_contextual_rtl_visual_grouped_numbers(line: str) -> str:
    position = 0
    while True:
        match = RTL_VISUAL_CONTEXTUAL_GROUPED_NUMBER_RE.search(line, position)
        if match is None:
            return line
        replacement = _reverse_rtl_visual_number_groups(match) if _has_visual_grouped_number_context(line, match) else match.group(0)
        line = line[: match.start()] + replacement + line[match.end() :]
        position = match.start() + len(replacement)


def _has_visual_grouped_number_context(line: str, match: re.Match[str]) -> bool:
    if _rtl_visual_number_groups(match)[-1] == "000":
        return False
    if _starts_immediately_after_numeric_range_dash(line=line, start=match.start()):
        return False
    before = line[max(0, match.start() - 16) : match.start()]
    after = line[match.end() : match.end() + 24]
    if CURRENCY_SYMBOL_RE.search(before) or CURRENCY_SYMBOL_RE.search(after[:8]):
        return True
    if re.match(r"\s*[-–—]\s*" + NORMAL_GROUPED_NUMBER_RE.pattern, after):
        return True
    return re.search(NORMAL_GROUPED_NUMBER_RE.pattern + r"\s*[-–—]\s*$", before) is not None


def _starts_immediately_after_numeric_range_dash(*, line: str, start: int) -> bool:
    if start < 2 or line[start - 1] not in "-–—":
        return False
    return line[start - 2].isdigit()


def _rtl_visual_number_groups(match: re.Match[str]) -> list[str]:
    return [group.strip() for group in match.group(0).split(",")]


def cleanup_display_text_record(*, before: str, after: str) -> dict[str, Any]:
    changed = before != after
    return {
        "applied": changed,
        "policy": "deterministic_hebrew_rtl_display_cleanup_only_raw_native_text_preserved",
        "source_text_preserved_in_raw_native_text": True,
        "rules": [
            "fake_underline_edge_markers",
            "reversed_hebrew_parentheses",
            "quote_dash_spacing",
            "punctuation_spacing",
            "footnote_accessibility_artifacts",
            "leading_rtl_colon",
            "rtl_visual_grouped_numbers",
        ]
        if changed
        else [],
    }


def _remove_footnote_accessibility_artifacts(line: str) -> str:
    cleaned = FOOTNOTE_ACCESSIBILITY_ARTIFACT_RE.sub(" ", line)
    return re.sub(r"\s+", " ", cleaned).strip()


def _repair_reversed_parenthetical_text(line: str) -> str:
    def replace_reversed_date(match: re.Match[str], source_line: str) -> str:
        prefix = " " if match.start() > 0 and re.search(r"[\u0590-\u05FF0-9\"'׳״]$", source_line[: match.start()]) else ""
        return f"{prefix}({match.group(1).strip()})"

    def replace_reversed_hebrew_or_mixed(match: re.Match[str], source_line: str) -> str:
        return _format_reversed_parenthetical(match=match, source_line=source_line, space_mixed_ltr=True)

    line = _sub_reversed_parenthetical(line, BIDI_REVERSED_PAREN_WITH_CLOSER_RE, lambda match, _: f"({match.group(1).strip()})")
    line = _sub_reversed_parenthetical(line, BIDI_REVERSED_DATE_PAREN_RE, replace_reversed_date)
    line = _sub_reversed_parenthetical(line, BIDI_REVERSED_PAREN_BEFORE_DATE_RE, lambda match, _: f"({match.group(1).strip()})")
    line = BIDI_EMPTY_PAREN_BEFORE_HEBREW_RE.sub(lambda match: f"({match.group(1).strip()})", line)
    return _sub_reversed_parenthetical(line, BIDI_REVERSED_HEBREW_PAREN_RE, replace_reversed_hebrew_or_mixed)


def _format_reversed_parenthetical(*, match: re.Match[str], source_line: str, space_mixed_ltr: bool = False) -> str:
    content = match.group(1).strip()
    text = f"({content})"
    if not space_mixed_ltr or LATIN_CHAR_RE.search(content) is None:
        return text
    if match.start() > 0 and re.search(r"[\u0590-\u05FFA-Za-z0-9\"'׳״]$", source_line[: match.start()]):
        text = " " + text
    if match.end() < len(source_line) and re.search(r"^[\u0590-\u05FFA-Za-z0-9\"'׳״]", source_line[match.end() :]):
        text += " "
    return text


def _sub_reversed_parenthetical(
    line: str,
    pattern: re.Pattern[str],
    replace: Any,
) -> str:
    position = 0
    while True:
        match = pattern.search(line, position)
        if match is None:
            return line
        trailing_open_index = match.end() - 1 if match.end() > match.start() and line[match.end() - 1] == "(" else None
        if _has_unclosed_open_parenthesis_before(line, match.start()) or (
            trailing_open_index is not None and _opening_parenthesis_starts_normal_pair(line, trailing_open_index)
        ):
            position = match.start() + 1
            continue
        replacement = replace(match, line)
        line = line[: match.start()] + replacement + line[match.end() :]
        position = match.start() + len(replacement)


def _has_unclosed_open_parenthesis_before(line: str, index: int) -> bool:
    balance = 0
    for char in line[:index]:
        if char == "(":
            balance += 1
        elif char == ")" and balance > 0:
            balance -= 1
    return balance > 0


def _opening_parenthesis_starts_normal_pair(line: str, index: int) -> bool:
    next_content_index = index + 1
    while next_content_index < len(line) and line[next_content_index].isspace() and line[next_content_index] != "\n":
        next_content_index += 1
    if next_content_index >= len(line) or line[next_content_index] in ",.;:!?)]}\n":
        return False
    next_close = line.find(")", index + 1)
    if next_close == -1:
        return False
    next_open = line.find("(", index + 1)
    next_newline = line.find("\n", index + 1)
    if next_open != -1 and next_open < next_close:
        return False
    if next_newline != -1 and next_newline < next_close:
        return False
    return next_close - index <= 80


def _repair_multiline_reversed_parenthetical_text(text: str) -> str:
    return _sub_reversed_parenthetical(
        text,
        BIDI_REVERSED_HEBREW_PAREN_ACROSS_LINES_RE,
        lambda match, _: f"({match.group(1).strip()})",
    )


def _remove_fake_underline_markers(line: str) -> str:
    if HEBREW_CHAR_RE.search(line) is None:
        return line
    line = FAKE_UNDERLINE_LEADING_RE.sub(lambda match: match.group(1), line)
    line = FAKE_UNDERLINE_TRAILING_COLON_RE.sub(r"\1", line)
    line = FAKE_UNDERLINE_ISOLATED_TRAILING_PAIR_RE.sub("", line)
    line = FAKE_UNDERLINE_AFTER_HEBREW_COLON_RE.sub("", line)
    line = FAKE_UNDERLINE_TRAILING_END_RE.sub("", line)
    return re.sub(r"\s+", " ", line).strip()


def _repair_leading_rtl_colon(line: str) -> str:
    if not line.startswith(":") or not LETTER_RE.search(line[1:]):
        return line
    body = line[1:].strip().lstrip(":：").strip()
    if not body:
        return ""
    if body.startswith(":"):
        return body
    for separator in (" – ", " — ", " - "):
        if separator not in body:
            continue
        prefix, rest = body.split(separator, 1)
        if _looks_like_clean_hebrew_label_prefix(prefix):
            return f"{prefix.rstrip()}:{separator}{rest.lstrip()}"
    tokens = body.split()
    if len(tokens) >= 4:
        prefix = " ".join(tokens[:2])
        if _looks_like_clean_hebrew_label_prefix(prefix):
            return f"{prefix}: {' '.join(tokens[2:])}"
        return body
    return body.rstrip(" :") + ":"


def _looks_like_clean_hebrew_label_prefix(prefix: str) -> bool:
    tokens = str(prefix or "").split()
    return 1 <= len(tokens) <= 3 and len(prefix) <= 30 and all(HEBREW_CLEAN_LABEL_TOKEN_RE.fullmatch(token) for token in tokens)


def _repair_line_leading_punctuation_hebrew_prefix(line: str) -> str:
    return RTL_LINE_LEADING_PUNCT_HEBREW_PREFIX_RE.sub(r"\1", line)


def _normalize_hebrew_abbreviation_spacing(line: str) -> str:
    # Split only high-confidence abbreviations glued by native PDF extraction.
    # Normal geresh words/names such as חבר'ה, אג'נדות, אברז'ל stay untouched.
    return HEBREW_ABBREVIATION_JOIN_RE.sub(lambda match: f"{(match.group(1) or match.group(2)).strip()} ", line)


def _repair_two_letter_trailing_geresh_acronym(line: str) -> str:
    def replace(match: re.Match[str]) -> str:
        token = match.group(1)
        if token in FINAL_GERESH_ABBREVIATIONS:
            return match.group(0)
        return f'{token[0]}"{token[1]}'

    return HEBREW_TWO_LETTER_TRAILING_GERESH_ACRONYM_RE.sub(replace, line)


def _normalize_dash_and_quote_spacing(line: str) -> str:
    line = _normalize_paired_hebrew_quote_spacing(line)
    line = re.sub(r"(?<=[\u0590-\u05FF0-9\"״])\s*([-–—])\s*(?=[\"״])", r" \1 ", line)
    line = re.sub(r"(?<=[\u0590-\u05FF\"'׳״])([-־])\s*(?=\d)", r"\1", line)
    line = re.sub(r"(?<=[\u0590-\u05FF])\s+([-–—])\s+(?=[\u0590-\u05FF\"״])", r" \1 ", line)
    line = re.sub(r"(?<=\d)\s+([-–—])\s+(?=[\u0590-\u05FF])", r" \1 ", line)
    return re.sub(r"\s+", " ", line).strip()


def _normalize_paired_hebrew_quote_spacing(line: str) -> str:
    def replace(match: re.Match[str]) -> str:
        start, end = match.span()
        before = line[:start]
        after = line[end:]
        prefix_token = re.search(r"[\u0590-\u05FF]+$", before)
        body = match.group("body")
        if before and after and HEBREW_CHAR_RE.search(before[-1]) and HEBREW_CHAR_RE.match(body[:1]) and HEBREW_CHAR_RE.match(after[:1]):
            return match.group(0)
        abbreviation_context = (
            prefix_token is not None
            and len(prefix_token.group(0)) <= 8
            and re.match(r"[\u0590-\u05FF]{1,3}(?:\s|[,.;:!?)]|$)", body) is not None
        )
        if abbreviation_context:
            return match.group(0)
        text = match.group(0)
        if before and re.search(r"[\u0590-\u05FF0-9]$", before):
            text = " " + text
        if after and re.search(r"^[\u0590-\u05FF0-9]", after):
            text += " "
        return text

    return HEBREW_QUOTED_PHRASE_RE.sub(replace, line)


def _normalize_punctuation_spacing(line: str) -> str:
    line = HEBREW_COMMA_BEFORE_COLON_ARTIFACT_RE.sub(":", line)
    line = re.sub(r"\s+([.,:;!?%])", r"\1", line)
    line = re.sub(r"([;!?])(?=[\u0590-\u05FFA-Za-z0-9])", r"\1 ", line)
    line = re.sub(r",(?=[\u0590-\u05FFA-Za-z])", r", ", line)
    line = re.sub(r"(?<!\d),(?=\d)", r", ", line)
    line = _normalize_thousands_comma_spacing(line)
    line = re.sub(r"([,;!?])(?=\()", r"\1 ", line)
    line = re.sub(r"(?<!\d)\. (?=\d)", ". ", line)
    line = re.sub(r"(?<=\d)\.(?=[\u0590-\u05FFA-Za-z])", ". ", line)
    line = re.sub(r"(?<!\d)\.(?=[\u0590-\u05FFA-Za-z])", ". ", line)
    line = re.sub(r"([:])(?=[\u0590-\u05FFA-Za-z])", r"\1 ", line)
    line = re.sub(r"([:])(?=[\"״])", r"\1 ", line)
    line = re.sub(r"\(\s+", "(", line)
    line = re.sub(r"\s+\)", ")", line)
    line = re.sub(r"\)(?=[\u0590-\u05FF])", ") ", line)
    return re.sub(r"\s+", " ", line).strip()


def _normalize_thousands_comma_spacing(line: str) -> str:
    def replace(match: re.Match[str]) -> str:
        leading_group = match.group(1)
        before = line[max(0, match.start() - 8) : match.start()]
        after = line[match.end() : match.end() + 8]
        if len(leading_group.split(",", 1)[0]) <= 2 or "," in leading_group:
            return f"{leading_group},{match.group(2)}"
        if CURRENCY_SYMBOL_RE.search(before) or CURRENCY_SYMBOL_RE.search(after):
            return f"{leading_group},{match.group(2)}"
        return match.group(0)

    previous = None
    while previous != line:
        previous = line
        line = SPACED_THOUSANDS_COMMA_RE.sub(replace, line)
    return line
