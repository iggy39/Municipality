from __future__ import annotations

import pytest

from municipality.gis_provenance import MissingProvenanceError, build_feature_response, source_fragment_from_row


def test_feature_response_requires_source_and_provenance() -> None:
    source = {"source_id": "xplan_blue_lines"}

    with pytest.raises(MissingProvenanceError, match="source_id"):
        build_feature_response({"id": "feature-1", "provenance_id": "prov-1"}, source)

    with pytest.raises(MissingProvenanceError, match="provenance_id"):
        build_feature_response({"id": "feature-1", "source_id": "xplan_blue_lines"}, source)


def test_feature_response_includes_source_fragment() -> None:
    source = source_fragment_from_row(
        {
            "source_id": "xplan_blue_lines",
            "name_he": "תכניות",
            "name_en": "Plans",
            "provider_key": "xplan",
            "provenance_level": "official_national",
            "reuse_status": "verified",
            "display_status": "official",
            "source_is_official": True,
            "reuse_is_verified": True,
            "display_as_official": True,
            "attribution": None,
            "license_name": None,
            "license_url": None,
        }
    )

    payload = build_feature_response(
        {"id": "feature-1", "source_id": "xplan_blue_lines", "provenance_id": "prov-1"},
        source,
    )

    assert payload["source"]["source_id"] == "xplan_blue_lines"
    assert payload["source"]["display_as_official"] is True
