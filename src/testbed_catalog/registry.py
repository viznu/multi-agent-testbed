"""Loading and verifying catalog files."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path

import yaml

from testbed_catalog.model import CATALOG_CAPABILITIES, LANES_BY_NUMBER, CatalogRecord
from testbed_contracts.enums import Maturity


class CatalogError(RuntimeError):
    pass


class Catalog:
    def __init__(self, records: Iterable[CatalogRecord]) -> None:
        self.records: list[CatalogRecord] = sorted(records, key=lambda r: (r.lane, r.record_id))
        duplicates = _duplicates(r.record_id for r in self.records)
        if duplicates:
            raise CatalogError(f"duplicate record ids: {sorted(duplicates)}")

    def __len__(self) -> int:
        return len(self.records)

    def by_lane(self, lane: int) -> list[CatalogRecord]:
        return [r for r in self.records if r.lane == lane]

    def get(self, record_id: str) -> CatalogRecord:
        for record in self.records:
            if record.record_id == record_id:
                return record
        raise KeyError(record_id)

    def runnable(self) -> list[CatalogRecord]:
        return [r for r in self.records if r.is_runnable_here]

    def uncovered_lanes(self) -> list[int]:
        """Lanes with no record at all. The plan requires every lane to have at
        least one, even if it is honestly marked `external`."""
        covered = {r.lane for r in self.records}
        return sorted(set(LANES_BY_NUMBER) - covered)

    def unknown_capabilities(self) -> list[str]:
        """Tags outside the suggested vocabulary. Reported, never rejected: the
        capability vocabulary is deliberately open."""
        known = set(CATALOG_CAPABILITIES)
        return sorted({c for r in self.records for c in r.capabilities} - known)

    def verify(self) -> list[str]:
        """Return every problem found. An empty list means the catalog is honest
        about its own contents."""
        problems: list[str] = []
        for record in self.records:
            if record.maturity is Maturity.CERTIFIED:
                gaps = record.certification_gaps()
                if gaps:
                    problems.append(
                        f"{record.record_id} claims 'certified' but is missing: {', '.join(gaps)}"
                    )
            if record.maturity is Maturity.EXTERNAL and record.entry_point:
                problems.append(
                    f"{record.record_id} is marked 'external' but declares an entry point"
                )
            if record.is_runnable_here and not record.entry_point:
                problems.append(
                    f"{record.record_id} is marked '{record.maturity}' but declares no entry point"
                )
        for lane in self.uncovered_lanes():
            problems.append(f"lane {lane} ({LANES_BY_NUMBER[lane].title}) has no catalog record")
        return problems

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for record in self.records:
            counts[str(record.maturity)] = counts.get(str(record.maturity), 0) + 1
        return counts


def _duplicates(values: Iterable[str]) -> set[str]:
    seen: set[str] = set()
    dupes: set[str] = set()
    for value in values:
        if value in seen:
            dupes.add(value)
        seen.add(value)
    return dupes


def load_catalog(paths: Sequence[Path] | Path) -> Catalog:
    """Load every `*.yaml` under the given file or directory."""
    if isinstance(paths, Path):
        paths = [paths]
    files: list[Path] = []
    for path in paths:
        path = Path(path)
        files.extend(sorted(path.glob("**/*.yaml")) if path.is_dir() else [path])
    records: list[CatalogRecord] = []
    for file in files:
        raw = yaml.safe_load(file.read_text("utf-8")) or {}
        for entry in raw.get("records", []):
            try:
                records.append(CatalogRecord.model_validate(entry))
            except Exception as exc:
                raise CatalogError(f"{file}: {exc}") from exc
    return Catalog(records)
