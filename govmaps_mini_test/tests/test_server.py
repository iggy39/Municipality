from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from govmap_proxy.server import GovMapHTTPServer, GovMapRequestHandler, effective_request_origin, strip_public_prefix


@contextmanager
def temp_env(values: dict[str, str]) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextmanager
def test_server(config_path: Path, extra_env: dict[str, str] | None = None) -> Iterator[str]:
    env_values = {"GOVMAP_CONFIG_PATH": str(config_path)}
    env_values.update(extra_env or {})
    with temp_env(env_values):
        server = GovMapHTTPServer(("127.0.0.1", 0), GovMapRequestHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            yield f"http://{host}:{port}"
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


def request_json(url: str, headers: dict[str, str] | None = None) -> tuple[int, dict[str, object]]:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        try:
            return error.code, json.loads(error.read().decode("utf-8"))
        finally:
            error.close()


class GovMapIframeConfigTests(unittest.TestCase):
    def test_effective_origin_prefers_origin_header(self) -> None:
        headers = {
            "Origin": "https://horizonscanninglab.org",
            "X-Forwarded-Proto": "http",
            "Host": "localhost:8000",
        }

        self.assertEqual("https://horizonscanninglab.org", effective_request_origin(headers))

    def test_effective_origin_falls_back_to_forwarded_proto_and_host(self) -> None:
        headers = {"X-Forwarded-Proto": "https", "Host": "horizonscanninglab.org"}

        self.assertEqual("https://horizonscanninglab.org", effective_request_origin(headers))

    def test_effective_origin_prefers_forwarded_host_for_reverse_proxy(self) -> None:
        headers = {
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "horizonscanninglab.org",
            "Host": "example.trycloudflare.com",
        }

        self.assertEqual("https://horizonscanninglab.org", effective_request_origin(headers))

    def test_strip_public_prefix_from_cloudfront_path(self) -> None:
        with temp_env({"APP_PUBLIC_PATH_PREFIX": "/govmap-local"}):
            self.assertEqual("/iframe.html", strip_public_prefix("/govmap-local/iframe.html"))
            self.assertEqual("/", strip_public_prefix("/govmap-local"))

    def test_iframe_config_rejects_localhost_origin(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "govmaps.md"
            config_path.write_text("apk_key=abc123\ndomain=horizonscanninglab.org\n", encoding="utf-8")
            with test_server(config_path) as base_url:
                status, payload = request_json(
                    f"{base_url}/api/govmap/iframe-config",
                    {"Origin": "http://localhost:8000"},
                )

        self.assertEqual(403, status)
        self.assertEqual("iframe_origin_not_allowed", payload["error"])

    def test_iframe_config_allows_approved_origin_and_returns_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "govmaps.md"
            config_path.write_text("apk_key=abc123\ndomain=horizonscanninglab.org\n", encoding="utf-8")
            with test_server(config_path) as base_url:
                status, payload = request_json(
                    f"{base_url}/api/govmap/iframe-config",
                    {"Origin": "https://horizonscanninglab.org"},
                )

        self.assertEqual(200, status)
        self.assertEqual("abc123", payload["token"])
        self.assertEqual(
            "https://www.govmap.gov.il/govmap/api/govmap.api.js",
            payload["scriptUrl"],
        )
        self.assertEqual("https://horizonscanninglab.org", payload["origin"])

    def test_iframe_config_allows_cloudfront_prefixed_reverse_proxy_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "govmaps.md"
            config_path.write_text("apk_key=abc123\ndomain=horizonscanninglab.org\n", encoding="utf-8")
            with test_server(config_path, {"APP_PUBLIC_PATH_PREFIX": "/govmap-local"}) as base_url:
                status, payload = request_json(
                    f"{base_url}/govmap-local/api/govmap/iframe-config",
                    {
                        "X-Forwarded-Proto": "https",
                        "X-Forwarded-Host": "horizonscanninglab.org",
                        "Host": "example.trycloudflare.com",
                    },
                )

        self.assertEqual(200, status)
        self.assertEqual("abc123", payload["token"])
        self.assertEqual("https://horizonscanninglab.org", payload["origin"])

    def test_reverse_proxy_secret_blocks_unsigned_requests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "govmaps.md"
            config_path.write_text("apk_key=abc123\ndomain=horizonscanninglab.org\n", encoding="utf-8")
            with test_server(
                config_path,
                {
                    "APP_PUBLIC_PATH_PREFIX": "/govmap-local",
                    "APP_REVERSE_PROXY_SECRET": "secret-123",
                },
            ) as base_url:
                status, payload = request_json(
                    f"{base_url}/govmap-local/api/govmap/iframe-config",
                    {
                        "X-Forwarded-Proto": "https",
                        "X-Forwarded-Host": "horizonscanninglab.org",
                    },
                )

        self.assertEqual(403, status)
        self.assertEqual("forbidden", payload["error"])

    def test_reverse_proxy_secret_allows_signed_requests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "govmaps.md"
            config_path.write_text("apk_key=abc123\ndomain=horizonscanninglab.org\n", encoding="utf-8")
            with test_server(
                config_path,
                {
                    "APP_PUBLIC_PATH_PREFIX": "/govmap-local",
                    "APP_REVERSE_PROXY_SECRET": "secret-123",
                },
            ) as base_url:
                status, payload = request_json(
                    f"{base_url}/govmap-local/api/govmap/iframe-config",
                    {
                        "X-Forwarded-Proto": "https",
                        "X-Forwarded-Host": "horizonscanninglab.org",
                        "X-Govmap-Proxy-Secret": "secret-123",
                    },
                )

        self.assertEqual(200, status)
        self.assertEqual("abc123", payload["token"])

    def test_public_config_does_not_expose_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "govmaps.md"
            config_path.write_text("apk_key=abc123\ndomain=horizonscanninglab.org\n", encoding="utf-8")
            with test_server(config_path) as base_url:
                status, payload = request_json(f"{base_url}/api/govmap/config")

        self.assertEqual(200, status)
        self.assertNotIn("token", payload)
        self.assertTrue(payload["apiKeyConfigured"])


if __name__ == "__main__":
    unittest.main()
