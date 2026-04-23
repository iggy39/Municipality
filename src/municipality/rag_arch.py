from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


RAG_ARCH_V1 = "v1"
RAG_ARCH_V2 = "v2"


@dataclass(slots=True)
class RagArchitectureConfig:
    version: str = RAG_ARCH_V2
    v2_index_build_enabled: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "RagArchitectureConfig":
        source = env if env is not None else os.environ
        raw_version = (source.get("RAG_ARCH_VERSION") or RAG_ARCH_V2).strip().casefold()
        version = raw_version if raw_version in {RAG_ARCH_V1, RAG_ARCH_V2} else RAG_ARCH_V1
        return cls(
            version=version,
            v2_index_build_enabled=_env_bool(
                source.get("RAG_V2_INDEX_BUILD_ENABLED"),
                default=version == RAG_ARCH_V2,
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
