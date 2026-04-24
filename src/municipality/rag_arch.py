from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


RAG_ARCH_V2 = "v2"


@dataclass(slots=True)
class RagArchitectureConfig:
    version: str = RAG_ARCH_V2
    v2_index_build_enabled: bool = True

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "RagArchitectureConfig":
        source = env if env is not None else os.environ
        return cls(
            version=RAG_ARCH_V2,
            v2_index_build_enabled=_env_bool(
                source.get("RAG_V2_INDEX_BUILD_ENABLED"),
                default=True,
            ),
        )

    @property
    def uses_v2(self) -> bool:
        return self.version == RAG_ARCH_V2


def _env_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default
