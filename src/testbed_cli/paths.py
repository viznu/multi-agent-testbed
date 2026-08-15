"""Where the catalog and the switch file come from.

Resolution has to be explicit. If `matb` silently found no catalog when run from
a different directory, every integration switch would silently stop applying --
which is the exact failure this machinery exists to prevent. So each location is
resolved in a defined order, and `matb doctor` prints what it resolved.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

CATALOG_ENV = "MATB_CATALOG"
SWITCHES_ENV = "MATB_SWITCHES"
SWITCHES_FILENAME = "integrations.toml"
LOCAL_CATALOG_DIRNAME = "catalog"

#: The catalog that ships inside the distribution. Always present, so a
#: `pip install`ed testbed behaves the same as one run from a source tree.
PACKAGED_CATALOG = Path(__file__).resolve().parents[1] / "testbed_catalog" / "data"


@dataclass(frozen=True)
class ResolvedPath:
    path: Path
    origin: str

    def __str__(self) -> str:
        return f"{self.path} ({self.origin})"


def find_upwards(name: str, start: Path | None = None, *, limit: int = 24) -> Path | None:
    """Search for `name` in `start` and its parents, as build tools do."""
    current = Path(start or Path.cwd()).resolve()
    for _ in range(limit):
        candidate = current / name
        if candidate.exists():
            return candidate
        if current.parent == current:
            break
        current = current.parent
    return None


def resolve_catalog(explicit: Path | None = None, *, start: Path | None = None) -> ResolvedPath:
    """`--path` beats `MATB_CATALOG`, which beats a project catalog, which beats
    the packaged one."""
    if explicit is not None:
        return ResolvedPath(Path(explicit), "--path")
    from_env = os.environ.get(CATALOG_ENV)
    if from_env:
        return ResolvedPath(Path(from_env), f"${CATALOG_ENV}")
    local = find_upwards(LOCAL_CATALOG_DIRNAME, start)
    if local is not None and local.is_dir():
        return ResolvedPath(local, "project catalog/")
    return ResolvedPath(PACKAGED_CATALOG, "packaged")


def resolve_switches(explicit: Path | None = None, *, start: Path | None = None) -> ResolvedPath:
    """The switch file is optional; when absent, nothing is switched off."""
    if explicit is not None:
        return ResolvedPath(Path(explicit), "explicit")
    from_env = os.environ.get(SWITCHES_ENV)
    if from_env:
        return ResolvedPath(Path(from_env), f"${SWITCHES_ENV}")
    found = find_upwards(SWITCHES_FILENAME, start)
    if found is not None:
        return ResolvedPath(found, "project file")
    return ResolvedPath(Path(start or Path.cwd()) / SWITCHES_FILENAME, "default (absent)")
