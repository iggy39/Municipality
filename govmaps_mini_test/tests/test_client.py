from __future__ import annotations

import unittest

from govmap_proxy.client import GovMapProxyClient, UnsafeUpstreamTarget
from govmap_proxy.config import GovMapSettings


def settings() -> GovMapSettings:
    return GovMapSettings(
        api_key="abc123",
        domain="horizonscanninglab.org",
        origin="https://horizonscanninglab.org",
        referer="https://horizonscanninglab.org/",
        base_url="https://www.govmap.gov.il",
        allowed_hosts=("www.govmap.gov.il", "es.govmap.gov.il"),
        api_key_param="apikey",
        api_key_header="",
        search_path="/search",
        spatial_path="",
        lookup_path="",
        allowed_iframe_origins=("https://horizonscanninglab.org",),
        cors_origins=("http://localhost:8000",),
        iframe_script_url="https://www.govmap.gov.il/govmap/api/govmap.api.js",
        timeout_seconds=20,
    )


class GovMapProxyClientTests(unittest.TestCase):
    def test_prepare_request_injects_origin_referer_and_api_key(self) -> None:
        client = GovMapProxyClient(settings())

        prepared = client.prepare_request(
            "search",
            {"method": "GET", "params": {"q": "Jerusalem"}},
        )

        self.assertEqual("GET", prepared.method)
        self.assertIn("https://www.govmap.gov.il/search?", prepared.url)
        self.assertIn("q=Jerusalem", prepared.url)
        self.assertIn("apikey=abc123", prepared.url)
        self.assertEqual("https://horizonscanninglab.org", prepared.headers["Origin"])
        self.assertEqual("https://horizonscanninglab.org/", prepared.headers["Referer"])

    def test_rejects_non_govmap_host(self) -> None:
        client = GovMapProxyClient(settings())

        with self.assertRaises(UnsafeUpstreamTarget):
            client.prepare_request("proxy", {"path": "https://example.com/search"})

    def test_rejects_relative_path_without_leading_slash(self) -> None:
        client = GovMapProxyClient(settings())

        with self.assertRaises(UnsafeUpstreamTarget):
            client.prepare_request("proxy", {"path": "search"})


if __name__ == "__main__":
    unittest.main()
