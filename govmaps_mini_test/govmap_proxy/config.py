from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


DEFAULT_BASE_URL = "https://www.govmap.gov.il"
DEFAULT_ALLOWED_HOSTS = ("www.govmap.gov.il", "es.govmap.gov.il")
DEFAULT_IFRAME_SCRIPT_URL = "https://www.govmap.gov.il/govmap/api/govmap.api.js"


def read_key_value_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip().lower()] = value.strip()

    return values


def split_csv(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(part.strip() for part in value.split(",") if part.strip())


def env(name: str, fallback: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None or value == "":
        return fallback
    return value


@dataclass(frozen=True)
class GovMapSettings:
    api_key: str
    domain: str
    origin: str
    referer: str
    base_url: str
    allowed_hosts: tuple[str, ...]
    api_key_param: str
    api_key_header: str
    search_path: str
    spatial_path: str
    lookup_path: str
    allowed_iframe_origins: tuple[str, ...]
    cors_origins: tuple[str, ...]
    iframe_script_url: str
    timeout_seconds: float

    @classmethod
    def from_env(cls) -> "GovMapSettings":
        config_path = Path(env("GOVMAP_CONFIG_PATH", "govmaps.md") or "govmaps.md")
        file_values = read_key_value_file(config_path)

        api_key = (
            env("GOVMAP_API_KEY")
            or file_values.get("api_key")
            or file_values.get("apikey")
            or file_values.get("apk_key")
            or ""
        )
        domain = env("GOVMAP_DOMAIN") or file_values.get("domain") or ""
        origin = env("GOVMAP_ORIGIN") or (f"https://{domain}" if domain else "")
        referer = env("GOVMAP_REFERER") or (f"{origin}/" if origin else "")

        base_url = env("GOVMAP_BASE_URL", DEFAULT_BASE_URL) or DEFAULT_BASE_URL
        base_host = urlparse(base_url).hostname or ""
        allowed_hosts = split_csv(env("GOVMAP_ALLOWED_HOSTS"))
        if not allowed_hosts:
            allowed_hosts = tuple(dict.fromkeys((base_host, *DEFAULT_ALLOWED_HOSTS)))

        staging_origin = f"https://dev.{domain}" if domain else ""
        iframe_origins = split_csv(env("GOVMAP_ALLOWED_IFRAME_ORIGINS"))
        if not iframe_origins:
            iframe_origins = tuple(
                origin_value
                for origin_value in (origin, staging_origin)
                if origin_value
            )
        cors_origins = split_csv(env("APP_CORS_ORIGINS"))
        if not cors_origins:
            cors_origins = tuple(
                dict.fromkeys(
                    (
                        "http://localhost:8000",
                        "http://127.0.0.1:8000",
                        "http://localhost:5173",
                        "http://127.0.0.1:5173",
                        *iframe_origins,
                    )
                )
            )

        return cls(
            api_key=api_key,
            domain=domain,
            origin=origin,
            referer=referer,
            base_url=base_url.rstrip("/"),
            allowed_hosts=allowed_hosts,
            api_key_param=env("GOVMAP_API_KEY_PARAM", "apikey") or "",
            api_key_header=env("GOVMAP_API_KEY_HEADER", "") or "",
            search_path=env("GOVMAP_SEARCH_PATH", "") or "",
            spatial_path=env("GOVMAP_SPATIAL_PATH", "") or "",
            lookup_path=env("GOVMAP_LOOKUP_PATH", "") or "",
            allowed_iframe_origins=iframe_origins,
            cors_origins=cors_origins,
            iframe_script_url=env("GOVMAP_IFRAME_SCRIPT_URL", DEFAULT_IFRAME_SCRIPT_URL)
            or DEFAULT_IFRAME_SCRIPT_URL,
            timeout_seconds=float(env("GOVMAP_TIMEOUT_SECONDS", "20") or "20"),
        )

    def public_dict(self) -> dict[str, object]:
        return {
            "apiKeyConfigured": bool(self.api_key),
            "origin": self.origin,
            "referer": self.referer,
            "baseUrl": self.base_url,
            "allowedHosts": self.allowed_hosts,
            "allowedIframeOrigins": self.allowed_iframe_origins,
            "corsOrigins": self.cors_origins,
            "operations": {
                "search": {"configuredPath": bool(self.search_path)},
                "spatial": {"configuredPath": bool(self.spatial_path)},
                "lookup": {"configuredPath": bool(self.lookup_path)},
            },
        }

    def iframe_config_dict(self) -> dict[str, object]:
        return {
            "token": self.api_key,
            "scriptUrl": self.iframe_script_url,
            "allowedIframeOrigins": self.allowed_iframe_origins,
            "mapSettings": {
                "layers": ["PARCEL_ALL"],
                "visibleLayers": ["PARCEL_ALL"],
                "showXY": True,
                "identifyOnClick": True,
                "isEmbeddedToggle": False,
                "background": "1",
                "layersMode": 1,
                "zoomButtons": False,
            },
        }
