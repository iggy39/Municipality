from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from municipality.api import app, ask_playground_page, rag_dashboard_evidence, rag_dashboard_interaction, rag_dashboard_mock


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
        "חוף הים",
        "צפון העיר",
        "מרכז העיר",
        "מערב העיר",
        "מזרח העיר",
        "דרום העיר",
        "מקרא",
        "אזור נבחר",
        "פארקים",
        "מבני ציבור",
        "מוקדי עניין",
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

    assert expected_visual_strings <= endpoint_strings
    assert all(value in ask_body for value in expected_visual_strings)

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


def test_ask_dashboard_page_is_wired_to_backend_mock_endpoint() -> None:
    body = bytes(ask_playground_page().body).decode("utf-8")

    assert 'data-dashboard-endpoint="/api/ui/rag-dashboard/mock"' in body
    assert 'data-source-status="fallback"' in body
    assert 'const DASHBOARD_DATA_ENDPOINT = dashboardRoot?.dataset.dashboardEndpoint || "/api/ui/rag-dashboard/mock";' in body
    assert "fetch(DASHBOARD_DATA_ENDPOINT" in body
    assert "renderDashboardFromEndpoint(data)" in body
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
        {"state": base["state"], "interaction": {"type": "select_category", "id": "transport"}}
    )
    assert category_payload["state"]["selected_category_id"] == "transport"
    assert category_payload["state"]["active_detail_drawer_mode"] == "category"
    assert next(row for row in category_payload["start_discovery_panel"]["categories"] if row["id"] == "transport")["selected"] is True

    topic_payload = rag_dashboard_interaction(
        {"state": base["state"], "interaction": {"type": "select_hot_topic", "id": "topic_light_rail"}}
    )
    assert topic_payload["state"]["selected_topic_node_id"] == "topic_light_rail"
    assert next(row for row in topic_payload["start_discovery_panel"]["hot_topics"] if row["id"] == "topic_light_rail")["selected"] is True

    tree_payload = rag_dashboard_interaction(
        {"state": base["state"], "interaction": {"type": "select_topic_tree_node", "id": "topic_master_plans"}}
    )
    assert tree_payload["state"]["selected_topic_node_id"] == "topic_master_plans"
    assert tree_payload["state"]["active_detail_drawer_mode"] == "topicTree"
    assert next(row for row in tree_payload["start_discovery_panel"]["focused_topic_tree_context"]["children"] if row["id"] == "topic_master_plans")["selected"] is True

    timeline_payload = rag_dashboard_interaction(
        {"state": base["state"], "interaction": {"type": "select_timeline_event", "id": "event_2024_07_02"}}
    )
    assert timeline_payload["state"]["selected_timeline_event_id"] == "event_2024_07_02"
    assert timeline_payload["main_civic_workspace"]["timeline"]["selected_event_id"] == "event_2024_07_02"
    assert next(row for row in timeline_payload["main_civic_workspace"]["timeline"]["events"] if row["id"] == "event_2024_07_02")["selected"] is True

    map_payload = rag_dashboard_interaction(
        {"state": base["state"], "interaction": {"type": "select_map_entity", "id": "entity_rova_tet_vav_transit_route"}}
    )
    assert map_payload["state"]["selected_map_entity_id"] == "entity_rova_tet_vav_transit_route"
    assert next(row for row in map_payload["main_civic_workspace"]["map"]["entities"] if row["id"] == "entity_rova_tet_vav_transit_route")["selected"] is True

    evidence_payload = rag_dashboard_interaction(
        {"state": base["state"], "interaction": {"type": "open_evidence", "id": "evidence_rova_tet_vav_protocol_1"}}
    )
    assert evidence_payload["state"]["active_detail_drawer_mode"] == "evidencePreview"
    assert evidence_payload["evidence_preview"]["id"] == "evidence_rova_tet_vav_protocol_1"


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
