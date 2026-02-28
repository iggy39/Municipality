"""
Test conftest to ensure the project's `src` directory is on `sys.path` so tests can import
the package sources under `src/municipality` (e.g. `import municipality.discovery`).

This file is intentionally simple and runs at import time so pytest can find the package
before any tests or fixtures import project modules.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Resolve paths relative to this conftest file. Tests live in <project_root>/tests.
_here = Path(__file__).resolve().parent
_project_root = _here.parent

# Prefer adding the top-level 'src' directory (i.e. <project_root>/src) which contains
# the actual package directory `municipality`.
_src_dir = _project_root / "src"

if _src_dir.is_dir():
    src_path = str(_src_dir)
    if src_path not in sys.path:
        # Insert at the front so the local src takes precedence over installed packages.
        sys.path.insert(0, src_path)
else:
    # Fallback: if the repository layout is different, try to add a direct path to the
    # package directory (e.g. <project_root>/src/municipality) if it exists.
    pkg_dir = _project_root / "src" / "municipality"
    if pkg_dir.is_dir():
        pkg_parent = str(pkg_dir.parent)
        if pkg_parent not in sys.path:
            sys.path.insert(0, pkg_parent)


# Optional: export a small helper so tests can assert the path was added when necessary.
def _is_src_on_path() -> bool:
    return any(Path(p) == _src_dir for p in map(Path, sys.path) if p)


__all__ = ["_is_src_on_path"]
