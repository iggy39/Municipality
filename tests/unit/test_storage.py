from __future__ import annotations

from municipality.storage import RawStorage


def test_write_tree_keeps_url_tree_structure(tmp_path) -> None:
    storage = RawStorage(tmp_path)

    uri = storage.write_tree(
        "ashdod",
        "https://www.ashdod.muni.il/media/16515722/protocol-a.pdf",
        b"pdf-bytes",
        tree_segments=["Finance Committee", "2025", "Meeting 1"],
    )

    assert uri == "tree/ashdod/Finance Committee/2025/Meeting 1/protocol-a.pdf"
    assert (tmp_path / uri).read_bytes() == b"pdf-bytes"


def test_write_tree_disambiguates_query_variants(tmp_path) -> None:
    storage = RawStorage(tmp_path)

    uri = storage.write_tree(
        "ashdod",
        "https://www.ashdod.muni.il/media/file.pdf?parentMediaID=10&title=2025",
        b"x",
    )

    assert uri.startswith("tree/ashdod/www.ashdod.muni.il/media/file__q")
    assert uri.endswith(".pdf")
    assert (tmp_path / uri).read_bytes() == b"x"


def test_write_page_html_uses_tree_path(tmp_path) -> None:
    storage = RawStorage(tmp_path)

    uri = storage.write_page_html(
        "ashdod",
        "https://www.ashdod.muni.il/he-il/protocols/list?parentMediaID=10",
        "<html>ok</html>",
        tree_segments=["Finance Committee", "2025"],
    )

    assert uri.startswith("tree/ashdod/Finance Committee/2025/")
    assert uri.endswith(".html")
    assert (tmp_path / uri).read_text(encoding="utf-8") == "<html>ok</html>"
