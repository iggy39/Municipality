from __future__ import annotations

import json
from pathlib import Path

from municipality.rag_dashboard_mock import SCENARIOS


def test_saved_mock_scenario_fixture_matches_current_catalog_count() -> None:
    path = Path("/Users/igor/Desktop/projects/Municipality/config/ui/rag_dashboard_mock_scenarios.json")
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["scenario_count"] == len(SCENARIOS) == 24
    assert len(payload["scenarios"]) == len(SCENARIOS)
    assert {row["key"] for row in payload["scenarios"]} == {row["key"] for row in SCENARIOS}
