import importlib.util
from pathlib import Path

from bs4 import BeautifulSoup

# Load AshdodDiscoveryAdapter directly from its source file to avoid import resolution issues
adapter_path = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "municipality"
    / "adapters"
    / "ashdod.py"
)
spec = importlib.util.spec_from_file_location(
    "ashdod_adapter_module", str(adapter_path)
)
# Ensure spec and loader are present before attempting to use them. This avoids
# attribute errors in environments where spec_from_file_location may return None
# or a spec without a loader.
if spec is None:
    raise ImportError(f"Could not load module specification for {adapter_path}")
# Bind loader to a local variable so type checkers and linters can reason that
# it is not None after the assert below.
loader = spec.loader
assert loader is not None, f"No loader available for module {adapter_path}"
ashdod_mod = importlib.util.module_from_spec(spec)
# Use the local loader reference to execute the module
loader.exec_module(ashdod_mod)
AshdodDiscoveryAdapter = ashdod_mod.AshdodDiscoveryAdapter

HE_SITE = "\u05d0\u05ea\u05e8-\u05d4\u05e2\u05d9\u05e8"
HE_PROTOCOLS = "\u05e4\u05e8\u05d5\u05d8\u05d5\u05e7\u05d5\u05dc\u05d9\u05dd"
HE_PROTOCOLS_BY_TOPIC = "\u05e8\u05e9\u05d9\u05de\u05ea-\u05e4\u05e8\u05d5\u05d8\u05d5\u05e7\u05d5\u05dc\u05d9\u05dd-\u05dc\u05e4\u05d9-\u05e0\u05d5\u05e9\u05d0"
HE_PROTOCOLS_BY_TOPIC_TITLE = (
    "\u05e8\u05e9\u05d9\u05de\u05ea \u05e4\u05e8\u05d5\u05d8\u05d5\u05e7\u05d5\u05dc\u05d9\u05dd "
    "\u05dc\u05e4\u05d9 \u05e0\u05d5\u05e9\u05d0"
)
HE_HOME = "\u05d3\u05e3-\u05d4\u05d1\u05d9\u05ea"
HE_REPORTS = "\u05d3\u05d5\u05d7\u05d5\u05ea"
HE_PROTOCOL_SHORT = (
    "\u05e4\u05e8\u05d5\u05d8\u05d5\u05e7\u05d5\u05dc \u05de\u05e7\u05d5\u05e6\u05e8"
)
HE_PROTOCOL_FULL = "\u05e4\u05e8\u05d5\u05d8\u05d5\u05e7\u05d5\u05dc \u05de\u05dc\u05d0"
HE_AUDIO_FILE = "\u05e7\u05d5\u05d1\u05e5 \u05e9\u05de\u05e2"
HE_APPENDICES = "\u05e0\u05e1\u05e4\u05d7\u05d9\u05dd"

ASHDOD_PROTOCOLS_BASE = f"https://www.ashdod.muni.il/he-il/{HE_SITE}/{HE_PROTOCOLS}/{HE_PROTOCOLS_BY_TOPIC}/"


ROOT_HTML = f"""
<html><head><title>{HE_PROTOCOLS_BY_TOPIC_TITLE}</title></head><body>
  <ul class="gallery">
    <li>
      <a href="/he-il/{HE_SITE}/{HE_PROTOCOLS}/{HE_PROTOCOLS_BY_TOPIC}/?parentMediaID=9999&title=2025">
        <img src="/images/ashdod/folder-icon.png" />
        18
        <h3>2025</h3>
      </a>
    </li>
  </ul>
  <a href="/he-il/{HE_SITE}/{HE_HOME}/{HE_REPORTS}/?parentMediaID=192050&title={HE_REPORTS}">outside-gallery</a>
</body></html>
"""


MEETING_HTML = f"""
<html><body>
  <div class="gallery">
    <a href="/media/16514520/protocol-short.pdf">{HE_PROTOCOL_SHORT}</a>
    <a href="/media/16514521/protocol-full.pdf">{HE_PROTOCOL_FULL}</a>
    <a href="/media/16514518/audio.mp3">{HE_AUDIO_FILE}</a>
    <a href="/he-il/{HE_SITE}/{HE_PROTOCOLS}/{HE_PROTOCOLS_BY_TOPIC}/?parentMediaID=214566&title={HE_APPENDICES}">
      <img src="/images/ashdod/folder-icon.png" />
      16
      <h3>{HE_APPENDICES}</h3>
    </a>
  </div>
  <a href="/media/outside.pdf">outside-gallery</a>
</body></html>
"""


def test_extracts_folder_cards_and_count_hint() -> None:
    adapter = AshdodDiscoveryAdapter()
    soup = BeautifulSoup(ROOT_HTML, "html.parser")

    links = adapter.extract_link_candidates(
        soup,
        ASHDOD_PROTOCOLS_BASE,
    )

    assert len(links) == 1
    assert links[0].title_he == "2025"
    assert links[0].count_hint == 18


def test_extracts_media_assets_and_subfolder() -> None:
    adapter = AshdodDiscoveryAdapter()
    soup = BeautifulSoup(MEETING_HTML, "html.parser")

    links = adapter.extract_link_candidates(
        soup,
        f"{ASHDOD_PROTOCOLS_BASE}?parentMediaID=214565",
    )

    hrefs = {link.href for link in links}
    assert "/media/16514520/protocol-short.pdf" in hrefs
    assert "/media/16514521/protocol-full.pdf" in hrefs
    assert "/media/16514518/audio.mp3" in hrefs
    assert any(
        link.title_he == HE_APPENDICES and link.count_hint == 16 for link in links
    )
    assert any(
        link.href == "/media/16514520/protocol-short.pdf"
        and link.title_he == HE_PROTOCOL_SHORT
        for link in links
    )


def test_in_scope_filters_non_target_year() -> None:
    adapter = AshdodDiscoveryAdapter()

    assert adapter.in_scope(f"{ASHDOD_PROTOCOLS_BASE}?parentMediaID=214564&title=2025")
    assert not adapter.in_scope(
        f"{ASHDOD_PROTOCOLS_BASE}?parentMediaID=206853&title=2024"
    )


def test_returns_empty_when_gallery_is_missing() -> None:
    adapter = AshdodDiscoveryAdapter()
    soup = BeautifulSoup("<html><body><a href='/media/a.pdf'>A</a></body></html>", "html.parser")

    links = adapter.extract_link_candidates(soup, ASHDOD_PROTOCOLS_BASE)

    assert links == []
