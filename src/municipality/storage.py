from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import unquote, urlparse


class RawStorage:
    def __init__(self, root: Path):
        self.root = root

    def write(self, municipality_slug: str, doc_kind: str, sha256: str, data: bytes) -> str:
        rel = Path(municipality_slug) / doc_kind / f"{sha256}.bin"
        target = self.root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(".tmp")
        temp.write_bytes(data)
        temp.replace(target)
        return rel.as_posix()

    def write_tree(
        self,
        municipality_slug: str,
        source_url: str,
        data: bytes,
        tree_segments: list[str] | tuple[str, ...] | None = None,
    ) -> str:
        parsed = urlparse(source_url)
        host = _safe_segment(parsed.netloc.lower() or "unknown-host")

        raw_parts = [part for part in parsed.path.split("/") if part]
        safe_parts = [_safe_segment(unquote(part)) for part in raw_parts]

        if safe_parts:
            *dirs, filename = safe_parts
        else:
            dirs, filename = [], "index.bin"

        if "." not in filename:
            filename = f"{filename}.bin"

        if parsed.query:
            stem, suffix = _split_name(filename)
            qhash = hashlib.sha256(parsed.query.encode("utf-8")).hexdigest()[:10]
            filename = f"{stem}__q{qhash}{suffix}"

        rel = Path("tree") / municipality_slug
        if tree_segments:
            for segment in tree_segments:
                rel = rel / _safe_segment(segment)
        else:
            rel = rel / host
            for segment in dirs:
                rel = rel / segment
        rel = rel / filename

        target = self.root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(target.suffix + ".tmp")
        temp.write_bytes(data)
        temp.replace(target)
        return rel.as_posix()

    def write_page_html(
        self,
        municipality_slug: str,
        page_url: str,
        html: str,
        tree_segments: list[str] | tuple[str, ...] | None = None,
    ) -> str:
        parsed = urlparse(page_url)
        host = _safe_segment(parsed.netloc.lower() or "unknown-host")

        raw_parts = [part for part in parsed.path.split("/") if part]
        safe_parts = [_safe_segment(unquote(part)) for part in raw_parts]

        if safe_parts:
            *dirs, leaf = safe_parts
            stem, _ = _split_name(leaf)
            filename = f"{stem}.html"
        else:
            dirs = []
            filename = "index.html"

        if parsed.query:
            stem, suffix = _split_name(filename)
            qhash = hashlib.sha256(parsed.query.encode("utf-8")).hexdigest()[:10]
            filename = f"{stem}__q{qhash}{suffix}"

        rel = Path("tree") / municipality_slug
        if tree_segments:
            for segment in tree_segments:
                rel = rel / _safe_segment(segment)
        else:
            rel = rel / host
            for segment in dirs:
                rel = rel / segment
        rel = rel / filename

        target = self.root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(target.suffix + ".tmp")
        temp.write_bytes(html.encode("utf-8"))
        temp.replace(target)
        return rel.as_posix()

    def read(self, uri: str) -> bytes:
        return (self.root / uri).read_bytes()


_INVALID_SEGMENT_CHARS = re.compile(r"[<>:\"/\\|?*\x00-\x1f]")


def _safe_segment(value: str) -> str:
    cleaned = _INVALID_SEGMENT_CHARS.sub("_", value.strip())
    cleaned = cleaned.replace("..", "_")
    return cleaned or "_"


def _split_name(filename: str) -> tuple[str, str]:
    dot = filename.rfind(".")
    if dot <= 0:
        return filename, ""
    return filename[:dot], filename[dot:]
