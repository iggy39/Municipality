from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx


GOOGLE_PLACES_TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"


def _local_env_value(name: str) -> str | None:
    value = os.environ.get(name)
    if value:
        return value
    env_path = Path(__file__).resolve().parents[2] / ".env.local"
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, raw_value = stripped.split("=", 1)
        if key.strip() == name:
            return raw_value.strip().strip('"\'') or None
    return None


class GoogleMapsClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        timeout_seconds: float = 6.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.api_key = api_key or _local_env_value("GOOGLE_MAPS_API_KEY")
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    def search_text(self, query: str, *, max_results: int = 5, language_code: str = "he", region_code: str = "IL") -> dict[str, Any]:
        query = " ".join(str(query or "").split())
        if not self.api_key:
            return {"places": [], "error": "missing_google_maps_api_key"}
        if not query:
            return {"places": [], "error": "empty_query"}
        body = {
            "textQuery": query,
            "languageCode": language_code,
            "regionCode": region_code,
            "maxResultCount": max(1, min(int(max_results), 10)),
        }
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress,places.location,places.types,places.googleMapsUri",
        }
        with httpx.Client(timeout=self.timeout_seconds, transport=self.transport) as client:
            response = client.post(GOOGLE_PLACES_TEXT_SEARCH_URL, headers=headers, json=body)
            response.raise_for_status()
            data = response.json()
        return data if isinstance(data, dict) else {"places": [], "error": "invalid_google_maps_response"}
