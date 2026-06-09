from __future__ import annotations

from municipality.gis_normalization import normalize_address, normalize_cadastral_id, normalize_gershayim, normalize_hebrew_text


def test_gershayim_variants_normalize_equally() -> None:
    assert normalize_hebrew_text("רובע ט״ו") == "רובע טו"
    assert normalize_hebrew_text("רובע טו") == "רובע טו"
    assert normalize_hebrew_text("רובע ט''ו") == "רובע טו"
    assert normalize_gershayim("ט׳׳ו") == "טו"


def test_address_abbreviations_expand_only_with_explicit_synonyms() -> None:
    assert normalize_address("שד׳ הרצל") == "שד הרצל"
    assert normalize_address("שד׳ הרצל", synonyms={"שד": "שדרות"}) == "שדרות הרצל"


def test_cadastral_normalization_does_not_over_normalize_other_identifiers() -> None:
    assert normalize_cadastral_id("גוש 123 חלקה 45") == "123/45"
    assert normalize_cadastral_id("123-45") == "123/45"
    assert normalize_cadastral_id("MOE-001-A") == "MOE-001-A"
