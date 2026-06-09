from __future__ import annotations

import hashlib
import json
from datetime import datetime, UTC
from pathlib import Path
from typing import Any


class ImmutableRawStorage:
    def __init__(self, root: Path) -> None:
        self.root = root

    def write_bytes(self, *, source_id: str, name: str, content: bytes, metadata: dict[str, Any] | None = None) -> str:
        digest = hashlib.sha256(content).hexdigest()
        safe_name = _safe_name(name)
        directory = self.root / source_id / digest[:2] / digest
        directory.mkdir(parents=True, exist_ok=True)
        payload_path = directory / safe_name
        manifest_path = directory / "manifest.json"
        if not payload_path.exists():
            payload_path.write_bytes(content)
        if not manifest_path.exists():
            manifest_path.write_text(
                json.dumps(
                    {
                        "source_id": source_id,
                        "name": name,
                        "sha256": digest,
                        "byte_size": len(content),
                        "stored_at": datetime.now(UTC).isoformat(),
                        "metadata": metadata or {},
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
        return str(payload_path)

    def write_json(self, *, source_id: str, name: str, payload: Any, metadata: dict[str, Any] | None = None) -> str:
        content = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
        return self.write_bytes(source_id=source_id, name=name, content=content, metadata=metadata)


def _safe_name(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {".", "-", "_"} else "_" for ch in name.strip())
    return cleaned or "raw.bin"
