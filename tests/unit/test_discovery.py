from __future__ import annotations

import importlib
import sys
from pathlib import Path

# Ensure the repository `src` directory is on sys.path so tests can import the package
# sources under `src/municipality` (e.g. `import municipality.discovery`) for both
# static analysis and pytest runs.
_here = Path(__file__).resolve().parent.parent
_src_dir = _here / "src"
if _src_dir.is_dir():
    src_path = str(_src_dir)
    if src_path not in sys.path:
        # Insert at front so local source takes precedence over installed packages.
        sys.path.insert(0, src_path)


# Dynamically import the discovery module at runtime so static analyzers and
# environments that don't have the package installed don't error at import time.
# Tests call `normalize_url` and `resolve_external_id` via these thin wrappers
# which import the real implementations only when needed.
def _get_discovery_module():
    return importlib.import_module("municipality.discovery")


def normalize_url(*args, **kwargs):
    return _get_discovery_module().normalize_url(*args, **kwargs)


def resolve_external_id(*args, **kwargs):
    return _get_discovery_module().resolve_external_id(*args, **kwargs)


def test_normalize_url_removes_tracking_and_resolves_relative() -> None:
    value = normalize_url(
        "../docs/protocol.pdf?utm_source=a&parentMediaID=123&z=9",
        "https://example.org/topic/2025/meeting/index.html",
    )
    assert (
        value
        == "https://example.org/topic/2025/docs/protocol.pdf?parentMediaID=123&z=9"
    )


def test_external_id_prefers_parent_media_id() -> None:
    url = "https://example.org/path/file.pdf?parentMediaID=999"
    assert resolve_external_id(url) == "pmid:999"


def test_external_id_falls_back_to_url_hash() -> None:
    url = "https://example.org/path/file.pdf"
    assert resolve_external_id(url).startswith("urlhash:")
