from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from govmap_proxy.config import GovMapSettings, read_key_value_file


class GovMapSettingsTests(unittest.TestCase):
    def test_reads_existing_govmaps_file_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "govmaps.md"
            path.write_text("apk_key=abc123\ndomain=horizonscanninglab.org\n", encoding="utf-8")
            previous = os.environ.get("GOVMAP_CONFIG_PATH")
            os.environ["GOVMAP_CONFIG_PATH"] = str(path)
            try:
                settings = GovMapSettings.from_env()
            finally:
                if previous is None:
                    os.environ.pop("GOVMAP_CONFIG_PATH", None)
                else:
                    os.environ["GOVMAP_CONFIG_PATH"] = previous

        self.assertEqual("abc123", settings.api_key)
        self.assertEqual("https://horizonscanninglab.org", settings.origin)
        self.assertEqual("https://horizonscanninglab.org/", settings.referer)
        self.assertIn("https://dev.horizonscanninglab.org", settings.allowed_iframe_origins)

    def test_key_value_parser_ignores_comments_and_blank_lines(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "govmaps.md"
            path.write_text("\n# comment\nAPI_KEY = key\n domain = example.org \n", encoding="utf-8")

            values = read_key_value_file(path)

        self.assertEqual({"api_key": "key", "domain": "example.org"}, values)


if __name__ == "__main__":
    unittest.main()
