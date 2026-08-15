"""Which integrations are actually usable right now, and why not.

An integration can be in exactly one of four states, and conflating them is how
a testbed ends up quietly not running what someone thinks it ran:

* `no_adapter`    -- nothing has been written here. There is no switch.
* `not_installed` -- an adapter exists, but its dependencies are absent.
* `disabled`      -- everything is present and it has been switched off.
* `active`        -- installed, switched on, and loadable.

Probing never imports the dependency: `find_spec` is enough to answer the
question and keeps `matb doctor` fast even when a heavy stack is installed.
"""

from __future__ import annotations

import shutil
import tomllib
from dataclasses import dataclass
from enum import StrEnum
from importlib.util import find_spec
from pathlib import Path
from typing import Any

from testbed_catalog.model import CatalogRecord
from testbed_contracts.enums import Maturity

SWITCHES_FILENAME = "integrations.toml"
"""Name of the switch file. `testbed_cli.paths` decides where to look for it."""


class IntegrationState(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
    NOT_INSTALLED = "not_installed"
    NO_ADAPTER = "no_adapter"


@dataclass(frozen=True)
class Availability:
    """The status of one integration, with an actionable reason when it is off."""

    record_id: str
    plugin_group: str | None
    plugin_name: str | None
    state: IntegrationState
    missing_modules: tuple[str, ...] = ()
    missing_binaries: tuple[str, ...] = ()
    extra: str | None = None

    @property
    def usable(self) -> bool:
        return self.state is IntegrationState.ACTIVE

    def reason(self) -> str:
        if self.state is IntegrationState.ACTIVE:
            return "active"
        if self.state is IntegrationState.DISABLED:
            return f"switched off in {SWITCHES_FILENAME}"
        if self.state is IntegrationState.NO_ADAPTER:
            return "no adapter exists in this repository (catalog record only)"
        missing = list(self.missing_modules) + list(self.missing_binaries)
        detail = f"missing {', '.join(missing)}" if missing else "dependencies missing"
        if self.extra:
            return f"{detail}; install with: pip install 'multi-agent-testbed[{self.extra}]'"
        return f"{detail}; no pip extra is declared for this integration yet"

    def remediation(self) -> str | None:
        if self.state is IntegrationState.NOT_INSTALLED and self.extra:
            return f"pip install 'multi-agent-testbed[{self.extra}]'"
        if self.state is IntegrationState.DISABLED:
            return f"matb integrations enable {self.record_id}"
        return None


@dataclass(frozen=True)
class Switches:
    """User-controlled on/off state, independent of what is installed.

    Switching off is deliberately separate from uninstalling: an experiment
    should be able to exclude an integration without tearing down an
    environment, and the run record should show that it was excluded on purpose.
    """

    disabled: frozenset[str] = frozenset()
    enabled: frozenset[str] = frozenset()
    source: Path | None = None

    @classmethod
    def load(cls, path: Path | None) -> Switches:
        if path is None or not Path(path).exists():
            return cls()
        raw: dict[str, Any] = tomllib.loads(Path(path).read_text("utf-8"))
        section = raw.get("integrations", {})
        return cls(
            disabled=frozenset(section.get("disabled", [])),
            enabled=frozenset(section.get("enabled", [])),
            source=Path(path),
        )

    def allows(self, record: CatalogRecord) -> bool:
        """`disabled` always wins; `enabled` turns on an opt-in integration."""
        if record.record_id in self.disabled:
            return False
        if record.record_id in self.enabled:
            return True
        return record.default_enabled

    def with_change(self, record_id: str, *, enabled: bool) -> Switches:
        disabled = set(self.disabled)
        turned_on = set(self.enabled)
        if enabled:
            disabled.discard(record_id)
            turned_on.add(record_id)
        else:
            turned_on.discard(record_id)
            disabled.add(record_id)
        return Switches(
            disabled=frozenset(disabled), enabled=frozenset(turned_on), source=self.source
        )

    def dump(self) -> str:
        lines = [
            "# Integration switches for the multi-agent testbed.",
            "#",
            "# `disabled` always wins. `enabled` turns on an integration whose catalog",
            "# record sets default_enabled = false. Switching an integration off does",
            "# not uninstall it; a manifest that names it will fail with a clear error",
            "# rather than silently running something else.",
            "",
            "[integrations]",
            "disabled = [" + ", ".join(f'"{r}"' for r in sorted(self.disabled)) + "]",
            "enabled = [" + ", ".join(f'"{r}"' for r in sorted(self.enabled)) + "]",
            "",
        ]
        return "\n".join(lines)

    def save(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.dump(), "utf-8")
        return path


def _module_present(name: str) -> bool:
    try:
        return find_spec(name) is not None
    except (ImportError, ValueError):
        # A parent package that is itself missing raises rather than returning None.
        return False


def availability_of(record: CatalogRecord, switches: Switches) -> Availability:
    """Classify one catalog record."""
    def build(
        state: IntegrationState,
        *,
        modules: tuple[str, ...] = (),
        binaries: tuple[str, ...] = (),
    ) -> Availability:
        return Availability(
            record_id=record.record_id,
            plugin_group=record.plugin_group,
            plugin_name=record.plugin_name,
            state=state,
            missing_modules=modules,
            missing_binaries=binaries,
            extra=record.extra,
        )

    if record.maturity in (Maturity.STUB, Maturity.EXTERNAL) or not record.entry_point:
        return build(IntegrationState.NO_ADAPTER)

    missing_modules = tuple(m for m in record.requires if not _module_present(m))
    missing_binaries = tuple(b for b in record.requires_binaries if shutil.which(b) is None)
    if missing_modules or missing_binaries:
        return build(
            IntegrationState.NOT_INSTALLED, modules=missing_modules, binaries=missing_binaries
        )
    if not switches.allows(record):
        return build(IntegrationState.DISABLED)
    return build(IntegrationState.ACTIVE)


class IntegrationIndex:
    """Availability for a whole catalog, indexed for the composition root."""

    def __init__(self, records: list[CatalogRecord], switches: Switches) -> None:
        self.switches = switches
        self.records = {r.record_id: r for r in records}
        self.status = {r.record_id: availability_of(r, switches) for r in records}
        self._by_plugin: dict[tuple[str, str], Availability] = {
            (str(r.plugin_group), str(r.plugin_name)): self.status[r.record_id]
            for r in records
            if r.plugin_group and r.plugin_name
        }

    def for_plugin(self, group: str, name: str) -> Availability | None:
        """Status of the integration a manifest is asking for, if the catalog
        knows about it. Returns None for plug-ins with no catalog record."""
        return self._by_plugin.get((group, name))

    def active(self) -> list[Availability]:
        return [a for a in self.status.values() if a.usable]

    def blocked(self) -> list[Availability]:
        """Everything with an adapter that is not currently usable.

        Records with no adapter are excluded: they are catalogue entries, not
        integrations someone forgot to switch on.
        """
        return [
            a
            for a in self.status.values()
            if a.state in (IntegrationState.DISABLED, IntegrationState.NOT_INSTALLED)
        ]

    def counts(self) -> dict[str, int]:
        counts = dict.fromkeys((str(s) for s in IntegrationState), 0)
        for status in self.status.values():
            counts[str(status.state)] += 1
        return counts

    def disabled_plugin_names(self, group: str) -> set[str]:
        return {
            name
            for (plugin_group, name), status in self._by_plugin.items()
            if plugin_group == group and not status.usable
        }
