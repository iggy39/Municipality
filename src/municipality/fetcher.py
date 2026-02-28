from __future__ import annotations

import time
from dataclasses import dataclass

import httpx


@dataclass(slots=True)
class FetchResult:
    ok: bool
    status_code: int | None
    body: bytes
    mime: str | None
    reason: str | None


class AssetFetcher:
    def __init__(self, timeout_seconds: float = 20.0, retries: int = 3, backoff_base: float = 0.25):
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self.backoff_base = backoff_base

    def fetch(self, url: str) -> FetchResult:
        last_reason: str | None = None
        with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True) as client:
            for attempt in range(self.retries + 1):
                try:
                    response = client.get(url)
                    if 200 <= response.status_code < 300:
                        return FetchResult(
                            ok=True,
                            status_code=response.status_code,
                            body=response.content,
                            mime=response.headers.get("content-type"),
                            reason=None,
                        )
                    last_reason = f"HTTP_{response.status_code}"
                except httpx.TimeoutException:
                    last_reason = "TIMEOUT"
                except httpx.HTTPError:
                    last_reason = "NETWORK_ERROR"

                if attempt < self.retries:
                    time.sleep(self.backoff_base * (2**attempt))

        return FetchResult(ok=False, status_code=None, body=b"", mime=None, reason=last_reason or "UNKNOWN")
