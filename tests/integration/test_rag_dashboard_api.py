from __future__ import annotations

import os
import json
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from math import sqrt
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import municipality.api as api_module
from municipality.api import app, ask_playground_page, get_db, rag_dashboard_evidence, rag_dashboard_interaction, rag_dashboard_mock
from municipality.migrations import apply_all
from municipality.models import Document, DocumentVersion, ExtractedDocument, RetrievalArtifact, SourceSite
from municipality.rag_dashboard_adapter import build_dashboard_error_payload, encode_artifact_evidence_id, validate_dashboard_payload
from municipality.rag_dashboard_contracts import RagDashboardInteractionRequest, RagDashboardPayload
from municipality.rag_dashboard_ui import render_rag_dashboard_page


def _flatten_strings(value: Any) -> set[str]:
    strings: set[str] = set()
    if isinstance(value, str):
        strings.add(value)
    elif isinstance(value, dict):
        for nested in value.values():
            strings.update(_flatten_strings(nested))
    elif isinstance(value, list):
        for nested in value:
            strings.update(_flatten_strings(nested))
    return strings


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_url(url: str, *, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as response:
                if response.status < 500:
                    return
        except Exception as exc:  # noqa: BLE001 - diagnostic helper for subprocess server startup.
            last_error = exc
        time.sleep(0.25)
    raise AssertionError(f"Timed out waiting for {url}: {last_error}")


def test_rag_dashboard_mock_endpoint_returns_contract_shaped_payload() -> None:
    payload = rag_dashboard_mock()
    assert RagDashboardPayload.model_validate(payload).state.map_mode == "schematic"

    assert payload["state"]["municipality_id"] == "ashdod"
    assert payload["state"]["current_question"] == "מה הוחלט לגבי תכנית רובע טו?"
    assert payload["state"]["search_intent"] == "decision_about_topic"
    assert payload["state"]["active_detail_drawer_mode"] == "answer"
    assert payload["state"]["filter_modal_open"] is False
    assert payload["state"]["map_mode"] == "schematic"

    discovery = payload["start_discovery_panel"]
    assert [row["label"] for row in discovery["categories"]] == [
        "תכנון ובנייה",
        "תחבורה",
        "חינוך",
        "רווחה",
        "סביבה",
    ]
    assert discovery["categories"][0]["count"] == 342
    assert discovery["hot_topics"][0] == {
        "id": "topic_rova_tet_vav_plan",
        "label": "תכנית רובע טו",
        "count": 23,
        "selected": True,
    }
    assert discovery["focused_topic_tree_context"]["root"]["label"] == "תכנון ובנייה"

    workspace = payload["main_civic_workspace"]
    assert workspace["map"]["spatial_representation"] == "schematic"
    assert workspace["map"]["real_gis_available"] is False
    assert workspace["map"]["real_geometry"] is None
    assert workspace["map"]["geometry_provenance"] is None
    assert workspace["map"]["provenance"] == {
        "status": "schematic_only",
        "label_he": "מפה סכמטית בלבד",
        "description_he": "אין גיאומטריית GIS מאומתת; המיקומים והצורות מוצגים להמחשה בלבד על בסיס הראיות.",
        "source_evidence_refs": [],
    }
    assert workspace["map"]["entities"][0]["real_geometry"] is None
    assert workspace["map"]["entities"][0]["geometry_provenance"] is None
    assert workspace["timeline"]["selected_event_id"] == "event_2024_06_23"
    assert [event["date_label"] for event in workspace["timeline"]["events"]] == [
        "10.05.2024",
        "28.05.2024",
        "23.06.2024",
        "02.07.2024",
        "15.07.2024",
    ]

    drawer = payload["end_detail_drawer"]
    assert drawer["mode"] == "answer"
    assert drawer["title"] == "תשובה"
    assert drawer["confidence_label"] == "גבוהה"
    assert len(drawer["decisions"]) == 4
    assert [decision["summary"] for decision in drawer["decisions"]] == [
        "אישור להפקדה בתנאים",
        "נקבעו תנאים להמשך קידום התכנית והפקדתה.",
        "נדרש עדכון בדו״ח ההשפעה על הסביבה לפני שלב ההפקדה הסופית.",
        "הצגת התכנית לציבור ושמיעת ההתנגדויות בכפוף לפרסום הודעה כדין.",
    ]
    assert all(decision["resident_evidence_links"][0]["label_he"] == "מקור" for decision in drawer["decisions"])
    assert [topic["label"] for topic in drawer["related_topics"]] == [
        "רובע טו",
        "תכנית רובע טו",
        "תכנון ובנייה",
        "תוכניות מפורטות",
        "תחבורה ציבורית",
        "שטחים פתוחים",
        "השפעה סביבתית",
    ]

    assert payload["contracts"]["topic_nodes"]
    assert payload["contracts"]["decisions"] == drawer["decisions"]
    assert payload["contracts"]["evidence"] == payload["evidence"]
    assert payload["evidence"][0]["artifact_kind"] == "decision_unit"
    assert payload["evidence"][0]["header_path"] == ["מועצת העיר", "תכנון ובנייה", "תכנית רובע טו"]
    assert payload["evidence"][0]["page_span"] == {"start": 7, "end": 7}


def test_rag_dashboard_mock_payload_covers_current_dashboard_visual_data() -> None:
    payload = rag_dashboard_mock()
    endpoint_strings = _flatten_strings(payload)
    ask_body = bytes(ask_playground_page().body).decode("utf-8")

    expected_visual_strings = {
        "לוח מחוונים עירוני",
        "עירייה",
        "מה הוחלט לגבי תכנית רובע טו?",
        "חיפושים פופולריים",
        "מסננים",
        "מנהל",
        "תשובה",
        "שאלה:",
        "תקציר",
        "ביטחון התשובה:",
        "גבוהה",
        "החלטות עיקריות",
        "נושאים קשורים",
        "מגבלות",
        "אישור להפקדה בתנאים",
        "נקבעו תנאים להמשך קידום התכנית והפקדתה.",
        "נדרש עדכון בדו״ח ההשפעה על הסביבה לפני שלב ההפקדה הסופית.",
        "הצגת התכנית לציבור ושמיעת ההתנגדויות בכפוף לפרסום הודעה כדין.",
        "מקור",
        "רובע טו",
        "תכנית רובע טו",
        "תכנון ובנייה",
        "תוכניות מפורטות",
        "תחבורה ציבורית",
        "שטחים פתוחים",
        "השפעה סביבתית",
        "ייתכנו שינויים בהחלטות עד לאישור סופי. מומלץ לפתוח את המקור לפני הסקת מסקנות.",
        "מפה סכמטית של רובע טו",
        "מפה סכמטית בלבד",
        "אין גיאומטריית GIS מאומתת; המיקומים והצורות מוצגים להמחשה בלבד על בסיס הראיות.",
        "חוף הים",
        "צפון העיר",
        "מרכז העיר",
        "מערב העיר",
        "מזרח העיר",
        "דרום העיר",
        "מקרא",
        "חלקה נבחרת",
        "תכנית / גבול תכנון",
        "שכונה / גבול עירוני",
        "מוסד חינוך",
        "מבנה הקשר OSM",
        "מוקד הקשר OSM",
        "מרכז מפה",
        "התקרבות",
        "התרחקות",
        "שכבות מפה",
        "ציר זמן",
        "אירוע קודם",
        "אירוע הבא",
        "15.07.2024",
        "פרסום להפקדה",
        "התחלת תהליך",
        "02.07.2024",
        "החלטה מס׳ 1123",
        "אישור התנאים",
        "23.06.2024",
        "ישיבה מס׳ 478",
        "אישור להפקדה",
        "28.05.2024",
        "דיון ציבורי",
        "הצגת התכנית",
        "10.05.2024",
        "דיונים מקדימים",
        "פרסום תכנית",
        "קטגוריות",
        "תחבורה",
        "חינוך",
        "רווחה",
        "סביבה",
        "הצג עוד",
        "נושאים בולטים",
        "הקמת קו רכבת קלה",
        "שדרוג פארק לכיש",
        "תכנית מתאר חדשה",
        "עץ נושאים",
        "תוכניות מתאר",
        "היתרים",
        "הצג כל העץ",
        "סינון תוצאות",
        "אזור",
        "טווח זמן",
        "קטגוריה",
        "סוגי מקורות",
        "ודאות",
        "איפוס",
        "החל סינון",
        "כל העיר",
        "כל השנים",
        "פרוטוקולים ונספחים",
        "פרוטוקולים",
        "גבוהה ובינונית",
        "כל הרמות",
    }

    expected_body_strings = (
        expected_visual_strings
        - {"חלקה נבחרת", "תכנית / גבול תכנון", "שכונה / גבול עירוני"}
        | {"חלקת MAPI רשמית", "תכנית אם קיימת בנקודה", "גבול תל אביב רשמי"}
    )

    assert expected_visual_strings <= endpoint_strings
    assert all(value in ask_body for value in expected_body_strings)
    assert 'muni: "ashdod"' not in ask_body
    assert "selectedMunicipalityForQuery" in ask_body

    assert [row["count"] for row in payload["start_discovery_panel"]["categories"]] == [342, 128, 95, 76, 64]
    assert [row["count"] for row in payload["start_discovery_panel"]["hot_topics"]] == [23, 18, 14, 11]
    assert [row["count"] for row in payload["start_discovery_panel"]["focused_topic_tree_context"]["children"]] == [12, 23, 7]

    evidence_ids = {row["id"] for row in payload["evidence"]}
    for decision in payload["end_detail_drawer"]["decisions"]:
        links = decision["resident_evidence_links"]
        assert links
        for link in links:
            assert link["label_he"] == payload["ui_copy"]["answer_drawer"]["source_link_label"]
            assert link["evidence_ref"] in evidence_ids
            assert rag_dashboard_evidence(link["evidence_ref"])["id"] == link["evidence_ref"]

    for entity in payload["main_civic_workspace"]["map"]["entities"]:
        assert entity["spatial_representation"] == "schematic"
        assert entity["real_geometry"] is None
        assert entity["geometry_provenance"] is None


def test_rag_dashboard_page_wires_real_gis_map_progressive_enhancement() -> None:
    body = bytes(ask_playground_page().body).decode("utf-8")

    assert "/api/ui/rag-dashboard/gis-map" in body
    assert "maplibre-gl" not in body
    assert "id=\"real-gis-map\"" in body
    assert "id=\"real-gis-static-map\"" in body
    assert "id=\"map-example-select\"" in body
    assert "create_gis_map_examples" not in body
    assert "window.__municipalSvgGisController" in body
    assert "SVG GIS פעיל" in body
    assert "מפת GIS אמיתית" in body


def test_rag_dashboard_page_wires_stage_6_point_report_and_coverage_ui() -> None:
    body = bytes(ask_playground_page().body).decode("utf-8")

    assert 'const POINT_REPORT_ENDPOINT = "/v1/point-report"' in body
    assert 'const COVERAGE_ENDPOINT = "/v1/coverage"' in body
    assert 'id="point-report-panel"' in body
    assert 'id="coverage-panel"' in body
    assert 'id="coverage-matrix-button"' in body
    assert 'class="sourceBadge" data-status="official"' in body
    assert 'class="sourceBadge" data-status="municipal_license_under_review"' in body
    assert 'class="sourceBadge" data-status="context_only"' in body
    assert "loadPointReport" in body
    assert "loadCoverage" in body
    assert "selected_example" in body
    assert "mapExampleLayers" in body
    assert "mapExampleCopy" in body
    assert "visualBasemapCollections" in body
    assert "ensurePocBasemap" in body
    assert "gisMapMarker" in body
    assert "visibleLayers.pois" in body
    assert "dashboard-plans-fill" in body
    assert "dashboard-nearby-parcels-fill" in body
    assert "dashboard-context-pois-circle" in body
    assert "חלקת MAPI רשמית" in body


def test_rag_dashboard_gis_map_endpoint_returns_real_layers_with_provenance() -> None:
    engine = _seed_gis_dashboard_sqlite()

    def override_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        response = client.get("/api/ui/rag-dashboard/gis-map")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "found"
    assert payload["real_gis_available"] is True
    assert payload["selected_example"] == "tel_aviv_parcel"
    assert [item["id"] for item in payload["examples"]] == ["tel_aviv_parcel"]
    assert payload["basemap"]["display_status"] == "context_only"
    assert payload["focus"] == "tel_aviv_parcel"
    assert payload["query"]["gush"] == "7103"
    assert payload["query"]["helka"] == "43"
    assert payload["parcel"]["source_id"] == "mapi_parcels"
    assert payload["parcel"]["provenance_id"] == "prov-mapi-parcel-dashboard"
    assert payload["layers"]["nearby_parcels"]["count"] == 2
    assert payload["layers"]["nearby_parcels"]["total_count"] == 2
    assert payload["layers"]["nearby_parcels"]["items"][0]["source_id"] == "mapi_parcels"
    assert payload["layers"]["nearby_parcels"]["items"][0]["provenance_id"]
    assert payload["nearby_pois"]["count"] == 2
    assert payload["nearby_pois"]["categories"]["transport_stop"]["total_count"] == 1
    assert payload["nearby_pois"]["categories"]["school"]["total_count"] == 1
    assert {"nearby_parcels", "plans", "municipal_boundaries", "neighborhoods", "context_pois", "buildings"} <= set(payload["layers"].keys())
    assert payload["layers"]["plans"]["status"] == "not_found"
    assert payload["layers"]["municipal_boundaries"]["items"][0]["source_id"] == "moin_municipal_boundaries"
    assert payload["layers"]["context_pois"]["items"][0]["source"]["display_status"] == "context_only"
    assert payload["layers"]["buildings"]["items"][0]["source"]["display_status"] == "context_only"
    for item in payload["nearby_pois"]["items"]:
        assert item["source"]
        assert item["provenance_id"]
        assert item["geometry"]["type"] == "Point"


def test_rag_dashboard_gis_map_endpoint_keeps_tel_aviv_parcel_caveat() -> None:
    engine = _seed_gis_dashboard_sqlite()

    def override_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        response = client.get("/api/ui/rag-dashboard/gis-map?example=tel_aviv_parcel")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    payload = response.json()
    assert payload["selected_example"] == "tel_aviv_parcel"
    assert payload["parcel"]["gush"] == "7103"
    assert payload["parcel"]["helka"] == "43"
    assert payload["parcel"]["source"]["source_id"] == "mapi_parcels"
    assert payload["parcel"]["source"]["display_status"] == "official"
    assert any("MAPI" in caveat for caveat in payload["caveats"])


def test_rag_dashboard_can_server_render_visible_gis_map() -> None:
    payload = {
        "status": "found",
        "title_he": "מפת GIS לדוגמה: גוש 7103 חלקה 43",
        "parcel": {
            "label": "7103 / 43",
            "geometry": {
                "type": "MultiPolygon",
                "coordinates": [[[[34.781, 32.067], [34.782, 32.067], [34.782, 32.068], [34.781, 32.068], [34.781, 32.067]]]],
            },
        },
        "nearby_pois": {
            "items": [
                {
                    "poi_category": "school",
                    "name_he": "בית ספר סמוך",
                    "provenance_id": "prov-school",
                    "geometry": {"type": "Point", "coordinates": [34.7815, 32.0675]},
                }
            ]
        },
    }

    body = render_rag_dashboard_page(initial_gis_map_payload=payload)

    assert 'data-server-rendered-gis="true"' in body
    assert 'id="real-gis-static-map"' in body
    assert 'id="real-gis-static-map" class="realGisStaticMap" viewBox="0 0 900 620" role="img" aria-label="מפת GIS אמיתית ללא ספריית מפה חיצונית" hidden' not in body
    assert "מפת GIS אמיתית - נטענה מהשרת" not in body
    assert "7103 / 43" in body


def test_ask_dashboard_page_is_wired_to_backend_mock_endpoint() -> None:
    body = bytes(ask_playground_page().body).decode("utf-8")

    assert 'data-dashboard-endpoint="/api/ui/rag-dashboard/mock"' in body
    assert 'data-build-id="source-preview-text-only-v2"' in body
    assert 'data-source-status="fallback"' in body
    assert 'class="mapProvenanceBadge" data-spatial-representation="schematic"' in body
    assert "dashboardRoot.dataset.mapSpatialRepresentation" in body
    assert "dashboardRoot.dataset.realGisAvailable" in body
    assert 'const DASHBOARD_QUERY_ENDPOINT = "/api/ui/rag-dashboard/query"' in body
    assert 'const DASHBOARD_EVIDENCE_ENDPOINT = "/api/ui/rag-dashboard/evidence"' in body
    assert "DASHBOARD_QUERY_TIMEOUT_MS = 45000" in body
    assert 'class="filterCountBadge"' in body
    assert 'data-item-id="transport"' in body
    assert 'select name="time_range"' in body
    assert '<option value="transport">תחבורה</option>' in body
    assert 'id="evidence-source-status"' in body
    assert "אין כפתור פתיחה: קישור מקור מוצג כטקסט בלבד" in body
    assert 'id="evidence-source-url"' in body
    assert "קישור מקור אינו זמין" in body
    assert "זהו קישור mock ואינו ניתן לפתיחה" in body
    assert "evidence-source-link" not in body
    assert "isOpenableSourceUrl" not in body
    assert 'window.open(source, "_blank", "noopener,noreferrer")' not in body
    assert "categoryFilterToId" in body
    assert "applySelectedCategoryToRows" in body
    assert "collectFilterValues" in body
    assert "filters: activeFilters" in body
    assert "AbortController" in body
    assert "controller.abort()" in body
    assert "updateFilterBadge" in body
    assert "fetch(DASHBOARD_QUERY_ENDPOINT" in body
    assert "renderDashboardFromEndpoint(data)" in body
    assert "openEvidencePreview" in body
    assert 'const DASHBOARD_DATA_ENDPOINT = dashboardRoot?.dataset.dashboardEndpoint || "/api/ui/rag-dashboard/mock";' in body
    assert "fetch(DASHBOARD_DATA_ENDPOINT" in body
    assert 'dashboardRoot.dataset.sourceStatus = "loaded"' in body
    assert "window.__municipalDashboardData = currentDashboardData" in body
    assert "window.__municipalDashboardState = dashboardState" in body
    assert "DASHBOARD_INTERACTION_ENDPOINT = \"/api/ui/rag-dashboard/interaction\"" in body
    assert "applyDashboardInteraction(\"select_category\"" in body
    assert "applyDashboardInteraction(\"select_hot_topic\"" in body
    assert "applyDashboardInteraction(\"select_topic_tree_node\"" in body
    assert "applyDashboardInteraction(\"select_timeline_event\"" in body
    assert "applyDashboardInteraction(\"select_map_entity\"" in body
    assert "applyDashboardInteraction(\"select_related_topic\"" in body
    assert "applyDashboardInteraction(\"open_evidence\"" in body
    assert "applyDashboardInteraction(\"apply_filters\"" in body
    assert "applyDashboardInteraction(\"reset_filters\"" in body


def test_ask_dashboard_page_loads_backend_data_in_chromium(tmp_path) -> None:
    chromium = shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome")
    if not chromium:
        pytest.skip("Chromium executable is not available")
    version_check = subprocess.run([chromium, "--version"], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    if version_check.returncode != 0:
        pytest.skip(f"Chromium executable is not usable: {version_check.stderr.strip()}")

    port = _free_port()
    env = dict(os.environ)
    env["PYTHONPATH"] = "src"
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "municipality.api:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_url(f"http://127.0.0.1:{port}/health")
        result = subprocess.run(
            [
                chromium,
                "--headless=new",
                "--disable-gpu",
                "--no-sandbox",
                f"--user-data-dir={tmp_path / 'chromium-profile'}",
                "--virtual-time-budget=5000",
                "--dump-dom",
                f"http://127.0.0.1:{port}/ui/ask",
            ],
            cwd=Path(__file__).resolve().parents[2],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=40,
        )
        assert result.returncode == 0, result.stderr
        assert 'id="rag-dashboard"' in result.stdout
        assert 'data-source-status="loaded"' in result.stdout
        assert 'data-source-url="/api/ui/rag-dashboard/mock"' in result.stdout
        assert "מה הוחלט לגבי תכנית רובע טו?" in result.stdout
        assert "נתוני לוח המחוונים נטענו מהשרת." in result.stdout
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)


def test_rag_dashboard_evidence_endpoint_returns_artifact_native_evidence() -> None:
    evidence = rag_dashboard_evidence("evidence_rova_tet_vav_protocol_1")

    assert evidence["id"] == "evidence_rova_tet_vav_protocol_1"
    assert evidence["source_type"] == "protocol"
    assert evidence["source_title"] == "פרוטוקול מועצה 23.06.2024"
    assert evidence["retrieval_artifact_id"] == "artifact_protocol_decision_unit_478"
    assert evidence["artifact_kind"] == "decision_unit"
    assert evidence["retrieval_set_id"] == "retrieval_set_rova_tet_vav_2024"
    assert evidence["page_span"] == {"start": 7, "end": 7}
    assert evidence["confidence_label"] == "גבוהה"


def test_rag_dashboard_interaction_updates_selection_state() -> None:
    base = rag_dashboard_mock()

    category_payload = rag_dashboard_interaction(
        RagDashboardInteractionRequest.model_validate({"state": base["state"], "interaction": {"type": "select_category", "id": "transport"}})
    )
    assert category_payload["state"]["selected_category_id"] == "transport"
    assert category_payload["state"]["active_detail_drawer_mode"] == "category"
    assert next(row for row in category_payload["start_discovery_panel"]["categories"] if row["id"] == "transport")["selected"] is True

    topic_payload = rag_dashboard_interaction(
        RagDashboardInteractionRequest.model_validate({"state": base["state"], "interaction": {"type": "select_hot_topic", "id": "topic_light_rail"}})
    )
    assert topic_payload["state"]["selected_topic_node_id"] == "topic_light_rail"
    assert next(row for row in topic_payload["start_discovery_panel"]["hot_topics"] if row["id"] == "topic_light_rail")["selected"] is True

    tree_payload = rag_dashboard_interaction(
        RagDashboardInteractionRequest.model_validate({"state": base["state"], "interaction": {"type": "select_topic_tree_node", "id": "topic_master_plans"}})
    )
    assert tree_payload["state"]["selected_topic_node_id"] == "topic_master_plans"
    assert tree_payload["state"]["active_detail_drawer_mode"] == "topicTree"
    assert next(row for row in tree_payload["start_discovery_panel"]["focused_topic_tree_context"]["children"] if row["id"] == "topic_master_plans")["selected"] is True

    timeline_payload = rag_dashboard_interaction(
        RagDashboardInteractionRequest.model_validate({"state": base["state"], "interaction": {"type": "select_timeline_event", "id": "event_2024_07_02"}})
    )
    assert timeline_payload["state"]["selected_timeline_event_id"] == "event_2024_07_02"
    assert timeline_payload["main_civic_workspace"]["timeline"]["selected_event_id"] == "event_2024_07_02"
    assert next(row for row in timeline_payload["main_civic_workspace"]["timeline"]["events"] if row["id"] == "event_2024_07_02")["selected"] is True

    map_payload = rag_dashboard_interaction(
        RagDashboardInteractionRequest.model_validate({"state": base["state"], "interaction": {"type": "select_map_entity", "id": "entity_rova_tet_vav_transit_route"}})
    )
    assert map_payload["state"]["selected_map_entity_id"] == "entity_rova_tet_vav_transit_route"
    assert next(row for row in map_payload["main_civic_workspace"]["map"]["entities"] if row["id"] == "entity_rova_tet_vav_transit_route")["selected"] is True

    evidence_payload = rag_dashboard_interaction(
        RagDashboardInteractionRequest.model_validate({"state": base["state"], "interaction": {"type": "open_evidence", "id": "evidence_rova_tet_vav_protocol_1"}})
    )
    assert evidence_payload["state"]["active_detail_drawer_mode"] == "evidencePreview"
    assert evidence_payload["evidence_preview"]["id"] == "evidence_rova_tet_vav_protocol_1"

    filter_payload = rag_dashboard_interaction(
        RagDashboardInteractionRequest.model_validate({"state": base["state"], "interaction": {"type": "apply_filters", "id": "filters", "filters": {"category": "תחבורה"}}})
    )
    assert filter_payload["state"]["selected_category_id"] == "transport"
    assert next(row for row in filter_payload["start_discovery_panel"]["categories"] if row["id"] == "transport")["selected"] is True

    filter_id_payload = rag_dashboard_interaction(
        RagDashboardInteractionRequest.model_validate({"state": base["state"], "interaction": {"type": "apply_filters", "id": "filters", "filters": {"category": "transport"}}})
    )
    assert filter_id_payload["state"]["selected_category_id"] == "transport"
    assert next(row for row in filter_id_payload["start_discovery_panel"]["categories"] if row["id"] == "transport")["selected"] is True


def test_rag_dashboard_interaction_rejects_unknown_ids() -> None:
    client = TestClient(app)
    response = client.post(
        "/api/ui/rag-dashboard/interaction",
        json={"state": {}, "interaction": {"type": "select_category", "id": "missing"}},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "rag_dashboard_interaction_not_found"


def test_rag_dashboard_http_routes_are_exposed() -> None:
    client = TestClient(app)

    mock_response = client.get("/api/ui/rag-dashboard/mock")
    assert mock_response.status_code == 200
    assert mock_response.json()["state"]["selected_topic_node_id"] == "topic_rova_tet_vav_plan"

    evidence_response = client.get("/api/ui/rag-dashboard/evidence/evidence_rova_tet_vav_protocol_1")
    assert evidence_response.status_code == 200
    assert evidence_response.json()["id"] == "evidence_rova_tet_vav_protocol_1"

    missing_response = client.get("/api/ui/rag-dashboard/evidence/missing")
    assert missing_response.status_code == 404
    assert missing_response.json()["detail"] == "rag_dashboard_evidence_not_found"

    interaction_response = client.post(
        "/api/ui/rag-dashboard/interaction",
        json={"state": mock_response.json()["state"], "interaction": {"type": "select_timeline_event", "id": "event_2024_07_15"}},
    )
    assert interaction_response.status_code == 200
    assert interaction_response.json()["state"]["selected_timeline_event_id"] == "event_2024_07_15"


def test_rag_dashboard_query_endpoint_adapts_ask_answer(monkeypatch) -> None:
    seen_request = {}

    def fake_run_ask(*, request, db):
        seen_request["year"] = request.year
        seen_request["semantic_label"] = request.semantic_label
        return _fake_ask_payload(question=request.question)

    monkeypatch.setattr(api_module, "_run_ask", fake_run_ask)
    client = TestClient(app)

    response = client.post(
        "/api/ui/rag-dashboard/query",
        json={
            "question": "מה הוחלט על פארק לכיש?",
            "muni": "ashdod",
            "top_k": 3,
            "filters": {"time_range": "2024", "category": "transport", "area": "רובע טו", "source_types": "פרוטוקולים", "confidence": "גבוהה ובינונית"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    validate_dashboard_payload(payload)
    assert seen_request == {"year": 2024, "semantic_label": "תחבורה"}
    assert payload["state"]["current_question"] == "מה הוחלט על פארק לכיש?"
    assert payload["state"]["generation_status"] == "answer_ready"
    assert payload["state"]["active_filter_count"] == 5
    assert payload["state"]["active_filter_summary"]["area"] == "רובע טו"
    assert payload["state"]["selected_category_id"] == "transport"
    assert next(row for row in payload["start_discovery_panel"]["categories"] if row["id"] == "transport")["selected"] is True
    assert payload["state"]["selected_time_range"] == {"start": "2024-01-01", "end": "2024-12-31"}
    assert payload["state"]["source_type_filter"] == ["protocol"]
    assert payload["state"]["confidence_filter"] == "medium_and_high"
    assert payload["end_detail_drawer"]["brief"] == "הוחלט לאשר שדרוג של פארק לכיש."
    assert payload["end_detail_drawer"]["decisions"][0]["decision_kind"]["code"] == "APPROVAL"
    assert payload["end_detail_drawer"]["decisions"][0]["outcome_status"]["code"] == "APPROVED"
    assert payload["end_detail_drawer"]["decisions"][0]["legal_effect"]["code"] == "BINDING"
    assert payload["end_detail_drawer"]["decisions"][0]["resident_evidence_links"][0]["evidence_ref"].startswith("artifact_")
    assert payload["evidence"][0]["retrieval_artifact_id"] == "artifact-real-1"
    assert payload["evidence"][0]["source_url"] == "/document-versions/20/source.pdf#page=2"
    assert payload["main_civic_workspace"]["map"]["spatial_representation"] == "schematic"
    assert payload["main_civic_workspace"]["map"]["real_gis_available"] is False
    assert payload["main_civic_workspace"]["map"]["provenance"]["status"] == "schematic_only"


def test_rag_dashboard_query_endpoint_returns_safe_error_state(monkeypatch) -> None:
    def failing_run_ask(*, request, db):
        raise HTTPException(status_code=503, detail="llm_unavailable")

    monkeypatch.setattr(api_module, "_run_ask", failing_run_ask)
    client = TestClient(app)

    response = client.post("/api/ui/rag-dashboard/query", json={"question": "מה קרה?"})

    assert response.status_code == 200
    payload = response.json()
    validate_dashboard_payload(payload)
    assert payload["state"]["generation_status"] == "error"
    assert payload["state"]["error"]["code"] == "llm_unavailable"
    assert payload["end_detail_drawer"]["mode"] == "errorState"
    assert payload["end_detail_drawer"]["decisions"] == []
    assert payload["evidence"] == []
    assert payload["main_civic_workspace"]["map"]["provenance"]["status"] == "schematic_only"


def test_dashboard_error_payload_validates_empty_state() -> None:
    payload = build_dashboard_error_payload(question="שאלה ללא תשובה", error_code="test_error", message_he="אין תשובה זמינה.")

    assert RagDashboardPayload.model_validate(payload).state.generation_status == "error"
    assert payload["main_civic_workspace"]["map"]["real_geometry"] is None
    assert payload["main_civic_workspace"]["map"]["geometry_provenance"] is None


def test_rag_dashboard_evidence_endpoint_resolves_real_artifact(tmp_path: Path) -> None:
    db_path = tmp_path / "dashboard_artifact_evidence.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    apply_all(engine, Path("migrations"))

    with Session(engine) as session:
        artifact_id = _seed_retrieval_artifact(session)
        evidence_id = encode_artifact_evidence_id(artifact_id)

    def override_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        response = client.get(f"/api/ui/rag-dashboard/evidence/{evidence_id}")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    evidence = response.json()
    assert evidence["id"] == evidence_id
    assert evidence["retrieval_artifact_id"] == artifact_id
    assert evidence["source_type"] == "pdf_first_protocol"
    assert evidence["source_title"] == "פרוטוקול בדיקה"
    assert evidence["page_span"] == {"start": 2, "end": 3}
    assert evidence["source_url"].endswith("#page=2")
    assert evidence["header_path"] == ["מועצה", "בדיקה"]
    assert "הוחלט לאשר" in evidence["text"]


def _fake_ask_payload(*, question: str) -> dict[str, Any]:
    return {
        "ask_request_id": "ask-test-1",
        "status": "answer",
        "question": question,
        "answer": "הוחלט לאשר שדרוג של פארק לכיש.",
        "extended_answer": "הוחלט לאשר שדרוג של פארק לכיש, בכפוף לפתיחת מקור התכנון.",
        "answer_sections": [{"topic_root": "סביבה", "topic_child": "פארק לכיש", "summary": "אושר שדרוג פארק לכיש."}],
        "citations": [
            {
                "chunk_id": "artifact-real-1",
                "source_type": "pdf_first_protocol",
                "citation": "עמ׳ 2",
                "start_page": 2,
                "end_page": 2,
                "header_path": ["מועצה", "פארק לכיש"],
                "document": {"id": 10, "version_id": 20, "title": "פרוטוקול מועצה", "url": None, "original_url": "https://example.local/protocol.pdf"},
                "score": 0.82,
            }
        ],
        "claim_assessments": [{"text_he": "הוחלט לאשר שדרוג", "supporting_chunk_ids": ["artifact-real-1"], "quoted_evidence": "הוחלט לאשר שדרוג של פארק לכיש"}],
        "limitations": ["מבוסס על מקור אחד."],
        "scoring": {"confidence": 0.84},
        "retrieval": {"retrieval_set_id": "retrieval-test-1", "count": 1},
    }


def _seed_retrieval_artifact(session: Session) -> str:
    site = SourceSite(municipality_slug="ashdod", name="Ashdod", root_url="https://example.local")
    session.add(site)
    session.flush()
    document = Document(
        source_site_id=site.id,
        document_external_id="doc:dashboard-artifact",
        canonical_url="https://example.local/protocol.pdf",
        title_he="פרוטוקול בדיקה",
        doc_kind="protocol_full",
        mime_hint="application/pdf",
        last_seen_at=datetime.utcnow(),
    )
    session.add(document)
    session.flush()
    version = DocumentVersion(
        document_id=document.id,
        sha256="a" * 64,
        byte_size=10,
        storage_uri="storage/raw/missing.pdf",
        fetched_http_status=200,
        fetched_mime="application/pdf",
    )
    session.add(version)
    session.flush()
    extracted = ExtractedDocument(
        document_version_id=version.id,
        parser_name="test",
        parser_version="1",
        status="ok",
        extracted_text="הוחלט לאשר בדיקה",
        page_count=3,
    )
    session.add(extracted)
    session.flush()
    artifact_id = "artifact-db-real-1"
    artifact = RetrievalArtifact(
        artifact_id=artifact_id,
        document_id=document.id,
        document_version_id=version.id,
        extracted_document_id=extracted.id,
        section_id=None,
        source_kind="pdf_first_protocol",
        artifact_kind="pdf_first_retrieval_chunk",
        ordinal=1,
        title_he="סעיף בדיקה",
        committee_name=None,
        meeting_date=None,
        header_path_json='["מועצה", "בדיקה"]',
        body_text="הוחלט לאשר את בדיקת לוח המחוונים.",
        retrieval_text="הוחלט לאשר את בדיקת לוח המחוונים.",
        retrieval_text_norm="הוחלט לאשר את בדיקת לוח המחוונים.",
        start_offset=10,
        end_offset=52,
        start_page=2,
        end_page=3,
        citation_label="עמ׳ 2-3",
        trigram_count=4,
        metadata_json=None,
    )
    session.add(artifact)
    session.commit()
    return artifact_id


def _seed_gis_dashboard_sqlite():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    event.listen(engine, "connect", _register_gis_spatial_sqlite_functions)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE source_registry (
                  source_id TEXT PRIMARY KEY,
                  name_he TEXT,
                  name_en TEXT,
                  provider_key TEXT,
                  provenance_level TEXT,
                  reuse_status TEXT,
                  display_status TEXT,
                  source_is_official INTEGER,
                  reuse_is_verified INTEGER,
                  display_as_official INTEGER,
                  attribution TEXT,
                  license_name TEXT,
                  license_url TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE parcels (
                  id TEXT PRIMARY KEY,
                  source_id TEXT NOT NULL,
                  provenance_id TEXT NOT NULL,
                  source_object_id TEXT,
                  gush TEXT,
                  helka TEXT,
                  parcel_label TEXT,
                  validation_status TEXT NOT NULL,
                  geom TEXT NOT NULL,
                  geom_2039 TEXT NOT NULL,
                  fetched_at TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE plans (
                  id TEXT PRIMARY KEY,
                  source_id TEXT NOT NULL,
                  provenance_id TEXT NOT NULL,
                  plan_number TEXT NOT NULL,
                  plan_name TEXT,
                  validation_status TEXT NOT NULL,
                  geom TEXT NOT NULL,
                  metadata TEXT NOT NULL,
                  fetched_at TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE official_municipal_boundaries (
                  id TEXT PRIMARY KEY,
                  source_id TEXT NOT NULL,
                  provenance_id TEXT NOT NULL,
                  municipality_code TEXT NOT NULL,
                  municipality_name_he TEXT,
                  validation_status TEXT NOT NULL,
                  geom TEXT NOT NULL,
                  fetched_at TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE poi_points (
                  id TEXT PRIMARY KEY,
                  source_id TEXT NOT NULL,
                  provenance_id TEXT NOT NULL,
                  source_object_id TEXT,
                  poi_category TEXT NOT NULL,
                  name_he TEXT,
                  name_en TEXT,
                  official_identifier TEXT,
                  geom TEXT NOT NULL,
                  geom_2039 TEXT NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE context_pois (
                  id TEXT PRIMARY KEY,
                  source_id TEXT NOT NULL,
                  provenance_id TEXT NOT NULL,
                  source_object_id TEXT,
                  poi_category TEXT NOT NULL,
                  name TEXT,
                  geom TEXT NOT NULL,
                  geom_2039 TEXT NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE buildings (
                  id TEXT PRIMARY KEY,
                  source_id TEXT NOT NULL,
                  provenance_id TEXT NOT NULL,
                  source_object_id TEXT,
                  municipality_code TEXT,
                  validation_status TEXT NOT NULL,
                  geom TEXT NOT NULL,
                  geom_2039 TEXT NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE layer_coverage (
                  municipality_code TEXT NOT NULL,
                  municipality_name_he TEXT,
                  layer_key TEXT NOT NULL,
                  status TEXT NOT NULL,
                  source_id TEXT,
                  feature_count INTEGER NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO source_registry VALUES
                ('mapi_parcels', 'חלקות MAPI', 'MAPI parcels', 'mapi', 'official_national', 'verified', 'official', 1, 1, 1, NULL, NULL, NULL),
                ('moe_school_coordinates', 'מוסדות חינוך', 'Schools', 'moe', 'official_national', 'verified', 'official', 1, 1, 1, NULL, NULL, NULL),
                ('mot_gtfs_stops', 'תחנות תחבורה ציבורית', 'Bus stops', 'mot', 'official_national', 'verified', 'official', 1, 1, 1, NULL, NULL, NULL),
                ('xplan_blue_lines', 'תכניות XPLAN', 'XPLAN plans', 'xplan', 'official_national', 'verified', 'official', 1, 1, 1, NULL, NULL, NULL),
                ('moin_municipal_boundaries', 'גבולות רשויות', 'Municipal boundaries', 'moin', 'official_national', 'verified', 'official', 1, 1, 1, NULL, NULL, NULL),
                ('osm_context', 'OSM הקשר', 'OSM context', 'osm', 'context', 'verified', 'context_only', 0, 1, 0, 'OpenStreetMap contributors', NULL, NULL)
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO parcels VALUES
                ('parcel-dashboard', 'mapi_parcels', 'prov-mapi-parcel-dashboard', '7103-43', '7103', '43', '7103 / 43', 'valid', :parcel_geom, :parcel_geom, '2026-06-10'),
                ('parcel-neighbor-dashboard-1', 'mapi_parcels', 'prov-mapi-parcel-neighbor-1', '7103-44', '7103', '44', '7103 / 44', 'valid', :neighbor_geom_1, :neighbor_geom_1, '2026-06-10'),
                ('parcel-neighbor-dashboard-2', 'mapi_parcels', 'prov-mapi-parcel-neighbor-2', '7103-45', '7103', '45', '7103 / 45', 'valid', :neighbor_geom_2, :neighbor_geom_2, '2026-06-10')
                """
            ),
            {
                "parcel_geom": _gis_polygon_json(34.779, 32.079, 34.783, 32.083),
                "neighbor_geom_1": _gis_polygon_json(34.783, 32.079, 34.786, 32.083),
                "neighbor_geom_2": _gis_polygon_json(34.776, 32.079, 34.779, 32.083),
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO plans VALUES
                ('plan-dashboard', 'xplan_blue_lines', 'prov-plan-dashboard', '101-0057273', 'תכנית ירושלים בדיקה', 'valid', :plan_geom, :metadata, '2026-06-10')
                """
            ),
            {
                "plan_geom": _gis_polygon_json(35.2216, 31.7951, 35.2224, 31.7959),
                "metadata": json.dumps({"plan": {"internet_short_status": "בדיקה"}, "properties": {}}, ensure_ascii=False),
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO official_municipal_boundaries VALUES
                ('boundary-dashboard', 'moin_municipal_boundaries', 'prov-boundary-dashboard', '5000', 'תל אביב-יפו', 'valid', :boundary_geom, '2026-06-10')
                """
            ),
            {"boundary_geom": _gis_polygon_json(34.74, 32.03, 34.86, 32.15)},
        )
        connection.execute(
            text(
                """
                INSERT INTO poi_points VALUES
                ('school-dashboard', 'moe_school_coordinates', 'prov-school-dashboard', 'school-1', 'school', 'בית ספר סמוך', 'Nearby school', '1001', :school_geom, :school_geom),
                ('stop-dashboard', 'mot_gtfs_stops', 'prov-stop-dashboard', 'stop-1', 'transport_stop', 'תחנה סמוכה', 'Nearby stop', '2001', :stop_geom, :stop_geom)
                """
            ),
            {
                "school_geom": _gis_point_json(34.781, 32.081),
                "stop_geom": _gis_point_json(34.782, 32.082),
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO context_pois VALUES
                ('context-dashboard', 'osm_context', 'prov-context-dashboard', 'osm-1', 'library', 'ספרייה OSM', :context_geom, :context_geom)
                """
            ),
            {"context_geom": _gis_point_json(34.7815, 32.0815)},
        )
        connection.execute(
            text(
                """
                INSERT INTO buildings VALUES
                ('building-dashboard', 'osm_context', 'prov-building-dashboard', 'building-1', '5000', 'valid', :building_geom, :building_geom)
                """
            ),
            {"building_geom": _gis_polygon_json(34.7812, 32.0812, 34.7816, 32.0816)},
        )
        connection.execute(
            text(
                """
                INSERT INTO layer_coverage VALUES
                ('5000', 'תל אביב-יפו', 'parcels', 'national_available', 'mapi_parcels', 1),
                ('5000', 'תל אביב-יפו', 'boundaries', 'national_available', 'moin_municipal_boundaries', 1),
                ('5000', 'תל אביב-יפו', 'buildings', 'context_available', 'osm_context', 1),
                ('5000', 'תל אביב-יפו', 'context_pois', 'context_available', 'osm_context', 1),
                ('5000', 'תל אביב-יפו', 'neighborhoods', 'not_enabled', NULL, 0)
                """
            )
        )
    return engine


def _register_gis_spatial_sqlite_functions(connection: object, _record: object) -> None:
    connection.create_function("ST_AsGeoJSON", 1, lambda geom: geom)
    connection.create_function("ST_SetSRID", 2, lambda geom, _srid: geom)
    connection.create_function("ST_Transform", 2, lambda geom, _srid: geom)
    connection.create_function("ST_Point", 2, _gis_point_json)
    connection.create_function("ST_Centroid", 1, _gis_centroid_json)
    connection.create_function("ST_PointOnSurface", 1, _gis_centroid_json)
    connection.create_function("ST_Covers", 2, lambda geom, point: 1 if _gis_contains_point(geom, point) else 0)
    connection.create_function("ST_DWithin", 3, lambda geom, point, radius: 1 if _gis_distance_m(geom, point) <= float(radius) else 0)
    connection.create_function("ST_Distance", 2, _gis_distance_m)


def _gis_polygon_json(min_lon: float, min_lat: float, max_lon: float, max_lat: float) -> str:
    return json.dumps(
        {
            "type": "MultiPolygon",
            "coordinates": [[[[min_lon, min_lat], [max_lon, min_lat], [max_lon, max_lat], [min_lon, max_lat], [min_lon, min_lat]]]],
        }
    )


def _gis_point_json(lon: float, lat: float) -> str:
    return json.dumps({"type": "Point", "coordinates": [float(lon), float(lat)]})


def _gis_centroid_json(geom: object) -> str:
    bbox = _gis_bbox(geom)
    if bbox is None:
        return str(geom)
    min_lon, min_lat, max_lon, max_lat = bbox
    return _gis_point_json((min_lon + max_lon) / 2, (min_lat + max_lat) / 2)


def _gis_distance_m(geom: object, point: object) -> float:
    left = _gis_point_coordinates(geom)
    right = _gis_point_coordinates(point)
    if left is None or right is None:
        return 1_000_000.0
    return sqrt(((left[0] - right[0]) * 111_000) ** 2 + ((left[1] - right[1]) * 111_000) ** 2)


def _gis_contains_point(geom: object, point: object) -> bool:
    bbox = _gis_bbox(geom)
    coordinates = _gis_point_coordinates(point)
    if bbox is None or coordinates is None:
        return False
    min_lon, min_lat, max_lon, max_lat = bbox
    lon, lat = coordinates
    return min_lon <= lon <= max_lon and min_lat <= lat <= max_lat


def _gis_point_coordinates(value: object) -> tuple[float, float] | None:
    geometry = _gis_load_geometry(value)
    if not geometry:
        return None
    if geometry.get("type") == "Point":
        coordinates = geometry.get("coordinates") or []
        return float(coordinates[0]), float(coordinates[1])
    bbox = _gis_bbox(value)
    if bbox is None:
        return None
    min_lon, min_lat, max_lon, max_lat = bbox
    return (min_lon + max_lon) / 2, (min_lat + max_lat) / 2


def _gis_bbox(value: object) -> tuple[float, float, float, float] | None:
    geometry = _gis_load_geometry(value)
    if not geometry:
        return None
    points = list(_gis_iter_points(geometry.get("coordinates")))
    if not points:
        return None
    lons = [point[0] for point in points]
    lats = [point[1] for point in points]
    return min(lons), min(lats), max(lons), max(lats)


def _gis_iter_points(coordinates: object):
    if isinstance(coordinates, list) and len(coordinates) >= 2 and all(isinstance(value, (int, float)) for value in coordinates[:2]):
        yield float(coordinates[0]), float(coordinates[1])
        return
    if isinstance(coordinates, list):
        for item in coordinates:
            yield from _gis_iter_points(item)


def _gis_load_geometry(value: object) -> dict[str, object] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
