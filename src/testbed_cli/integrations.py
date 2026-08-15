"""Resolving which integrations are on, and refusing to guess when one is off.

The composition root asks two questions of every plug-in a manifest names:
does an adapter exist, and is it switched on? A "no" to either is an error with
a remediation, never a silent fallback to something else.
"""

from __future__ import annotations

from pathlib import Path

from testbed_catalog import load_catalog
from testbed_catalog.availability import (
    Availability,
    IntegrationIndex,
    IntegrationState,
    Switches,
)
from testbed_cli.paths import ResolvedPath, resolve_catalog, resolve_switches


class IntegrationUnavailable(RuntimeError):
    """A manifest asked for an integration that is not currently usable."""

    def __init__(self, group: str, name: str, status: Availability) -> None:
        remediation = status.remediation()
        message = f"{group[:-1]} {name!r} is not available: {status.reason()}"
        if remediation:
            message += f"\n  try: {remediation}"
        super().__init__(message)
        self.status = status


def switches_path(root: Path | None = None) -> Path:
    return resolve_switches(start=root).path


def resolved_locations(catalog_path: Path | None = None) -> tuple[ResolvedPath, ResolvedPath]:
    return resolve_catalog(catalog_path), resolve_switches()


def load_index(catalog_path: Path | None = None) -> IntegrationIndex:
    """Build the availability index from the resolved catalog and switch file.

    A catalog directory that was named explicitly but does not exist is an
    error, not an empty catalog: silently checking nothing is the failure mode
    this whole module exists to avoid.
    """
    catalog_location = resolve_catalog(catalog_path)
    if not catalog_location.path.exists():
        raise FileNotFoundError(f"no catalog at {catalog_location}")
    catalog = load_catalog(catalog_location.path)
    return IntegrationIndex(catalog.records, Switches.load(resolve_switches().path))


def check_available(index: IntegrationIndex, group: str, name: str) -> None:
    """Raise unless the named plug-in is usable.

    A plug-in with no catalog record passes: third-party packs installed by a
    user are not required to register themselves in this repository's catalog.
    """
    status = index.for_plugin(group, name)
    if status is None or status.usable:
        return
    raise IntegrationUnavailable(group, name, status)


def describe(index: IntegrationIndex) -> list[tuple[str, Availability]]:
    """Every integration that has, or could have, a switch.

    Records with no adapter are excluded: they are catalogue entries, not
    integrations someone forgot to turn on.
    """
    rows = [
        (record_id, status)
        for record_id, status in index.status.items()
        if status.state is not IntegrationState.NO_ADAPTER
    ]
    return sorted(rows, key=lambda row: (str(row[1].state), row[0]))
