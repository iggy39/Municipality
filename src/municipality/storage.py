from __future__ import annotations

from pathlib import Path


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

    def read(self, uri: str) -> bytes:
        return (self.root / uri).read_bytes()
