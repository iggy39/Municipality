from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from .config import GovMapSettings


class GovMapProxyError(Exception):
    code = "govmap_proxy_error"
    status = 500


class OperationNotConfigured(GovMapProxyError):
    code = "operation_not_configured"
    status = 400


class UnsafeUpstreamTarget(GovMapProxyError):
    code = "unsafe_upstream_target"
    status = 400


class UpstreamUnavailable(GovMapProxyError):
    code = "upstream_unavailable"
    status = 502


@dataclass(frozen=True)
class PreparedGovMapRequest:
    method: str
    url: str
    headers: dict[str, str]
    body: bytes | None


@dataclass(frozen=True)
class GovMapResponse:
    status: int
    headers: dict[str, str]
    body: bytes


def operation_default_path(settings: GovMapSettings, operation: str) -> str:
    paths = {
        "search": settings.search_path,
        "spatial": settings.spatial_path,
        "lookup": settings.lookup_path,
        "proxy": "",
    }
    return paths.get(operation, "")


def normalize_params(params: Any) -> list[tuple[str, str]]:
    if params is None:
        return []
    if not isinstance(params, dict):
        raise UnsafeUpstreamTarget("params must be a JSON object")

    normalized: list[tuple[str, str]] = []
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, list):
            for item in value:
                if item is not None:
                    normalized.append((str(key), param_value(item)))
            continue
        normalized.append((str(key), param_value(value)))
    return normalized


def param_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


class GovMapProxyClient:
    def __init__(self, settings: GovMapSettings):
        self.settings = settings

    def prepare_request(self, operation: str, payload: dict[str, Any]) -> PreparedGovMapRequest:
        if not isinstance(payload, dict):
            raise UnsafeUpstreamTarget("request body must be a JSON object")

        method = str(payload.get("method") or "GET").upper()
        if method not in {"GET", "POST"}:
            raise UnsafeUpstreamTarget("method must be GET or POST")

        path = str(payload.get("path") or operation_default_path(self.settings, operation) or "")
        if not path:
            raise OperationNotConfigured(
                f"Set GOVMAP_{operation.upper()}_PATH or pass a GovMap relative path in the request body."
            )

        url = self.resolve_target(path)
        params = normalize_params(payload.get("params") or {})
        if self.settings.api_key and self.settings.api_key_param:
            existing_keys = {key for key, _ in params}
            if self.settings.api_key_param not in existing_keys:
                params.append((self.settings.api_key_param, self.settings.api_key))
        if params:
            separator = "&" if urllib.parse.urlparse(url).query else "?"
            url = f"{url}{separator}{urllib.parse.urlencode(params, doseq=True)}"

        headers = self.govmap_headers()
        body: bytes | None = None
        if method == "POST":
            json_payload = payload.get("json")
            if json_payload is not None:
                body = json.dumps(json_payload).encode("utf-8")
                headers["Content-Type"] = "application/json"

        return PreparedGovMapRequest(method=method, url=url, headers=headers, body=body)

    def resolve_target(self, path: str) -> str:
        parsed = urllib.parse.urlparse(path)
        if parsed.scheme or parsed.netloc:
            target = parsed
        else:
            if not path.startswith("/") or path.startswith("//"):
                raise UnsafeUpstreamTarget("path must be an absolute GovMap URL or a relative path beginning with /")
            target = urllib.parse.urlparse(f"{self.settings.base_url}{path}")

        if target.scheme != "https":
            raise UnsafeUpstreamTarget("GovMap upstream URL must use https")
        if not target.hostname or target.hostname not in self.settings.allowed_hosts:
            raise UnsafeUpstreamTarget("GovMap upstream host is not allowed")

        return urllib.parse.urlunparse(target)

    def govmap_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "govmap-origin-proxy/1.0",
        }
        if self.settings.origin:
            headers["Origin"] = self.settings.origin
        if self.settings.referer:
            headers["Referer"] = self.settings.referer
        if self.settings.api_key and self.settings.api_key_header:
            headers[self.settings.api_key_header] = self.settings.api_key
        return headers

    def send(self, operation: str, payload: dict[str, Any]) -> GovMapResponse:
        prepared = self.prepare_request(operation, payload)
        request = urllib.request.Request(
            prepared.url,
            data=prepared.body,
            headers=prepared.headers,
            method=prepared.method,
        )

        try:
            with urllib.request.urlopen(request, timeout=self.settings.timeout_seconds) as response:
                return GovMapResponse(
                    status=response.status,
                    headers=dict(response.headers.items()),
                    body=response.read(),
                )
        except urllib.error.HTTPError as error:
            return GovMapResponse(
                status=error.code,
                headers=dict(error.headers.items()),
                body=error.read(),
            )
        except (urllib.error.URLError, TimeoutError, socket.timeout) as error:
            raise UpstreamUnavailable(str(error)) from error
