"""Plug-in discovery and wiring.

Discovery happens through Python entry points, so a third-party pack, topology
or agent adapter is installed rather than imported by name from kernel code.
Built-in plug-ins are registered through the same mechanism and fall back to
direct imports when the package is running from a source tree that was never
installed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any

from testbed_contracts.manifest import ExperimentManifest
from testbed_kernel import Composition
from testbed_store import LocalArtifactStore, SqliteStore

GROUPS = {
    "packs": "testbed.packs",
    "topologies": "testbed.topologies",
    "agents": "testbed.agents",
    "runners": "testbed.runners",
    "scorers": "testbed.scorers",
}


def _discover(group: str) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for entry in entry_points(group=group):
        try:
            found[entry.name] = entry.load()
        except Exception as exc:  # a broken third-party plug-in must not hide the rest
            found[entry.name] = _BrokenPlugin(entry.name, exc)
    return found


@dataclass
class _BrokenPlugin:
    name: str
    error: Exception

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(f"plug-in {self.name!r} failed to load: {self.error}")


def _builtins() -> dict[str, dict[str, Any]]:
    """Fallback registry for an uninstalled source tree."""
    from testbed_adapters.agents.cli.adapter import ADAPTER as cli_adapter
    from testbed_adapters.agents.python.scripted import ADAPTER as scripted_adapter
    from testbed_adapters.runners.raw.runner import RUNNER as raw_runner
    from testbed_eval.builtin_scorers import SCORERS
    from testbed_packs.smoke import PACK as smoke_pack
    from testbed_plugins.topologies.mesh import TOPOLOGY as mesh
    from testbed_plugins.topologies.pipeline import TOPOLOGY as pipeline
    from testbed_plugins.topologies.solo import TOPOLOGY as solo
    from testbed_plugins.topologies.supervisor import TOPOLOGY as supervisor

    return {
        "packs": {"smoke": smoke_pack},
        "topologies": {
            "solo": solo,
            "supervisor": supervisor,
            "mesh": mesh,
            "pipeline": pipeline,
        },
        "agents": {"scripted": scripted_adapter, "cli": cli_adapter},
        "runners": {"raw_session": raw_runner},
        "scorers": {"builtin": SCORERS},
    }


@dataclass
class Registry:
    packs: dict[str, Any] = field(default_factory=dict)
    topologies: dict[str, Any] = field(default_factory=dict)
    agents: dict[str, Any] = field(default_factory=dict)
    runners: dict[str, Any] = field(default_factory=dict)
    scorers: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def discover(cls) -> Registry:
        builtin = _builtins()
        registry = cls()
        for attribute, group in GROUPS.items():
            merged = dict(builtin.get(attribute, {}))
            merged.update(_discover(group))
            setattr(registry, attribute, merged)
        # Scorer entry points publish a mapping of scorers; flatten it.
        flat: dict[str, Any] = {}
        for value in registry.scorers.values():
            if isinstance(value, Mapping):
                flat.update(value)
            else:
                flat[getattr(value, "name", str(value))] = value
        registry.scorers = flat
        return registry

    def describe(self) -> dict[str, list[str]]:
        return {
            "packs": sorted(self.packs),
            "topologies": sorted(self.topologies),
            "agents": sorted(self.agents),
            "runners": sorted(self.runners),
            "scorers": sorted(self.scorers),
        }


@dataclass
class Workspace:
    """Where a session's state lives on disk."""

    root: Path

    @property
    def db_path(self) -> Path:
        return self.root / "testbed.db"

    @property
    def artifacts_path(self) -> Path:
        return self.root / "artifacts"

    def open(self) -> tuple[SqliteStore, LocalArtifactStore]:
        self.root.mkdir(parents=True, exist_ok=True)
        return SqliteStore(self.db_path), LocalArtifactStore(self.artifacts_path)


def compose(
    manifest: ExperimentManifest,
    workspace: Workspace,
    registry: Registry | None = None,
) -> tuple[Composition, Registry, SqliteStore, LocalArtifactStore]:
    """Resolve everything a manifest names into a kernel `Composition`."""
    registry = registry or Registry.discover()
    store, artifacts = workspace.open()

    pack = registry.packs.get(manifest.task_pack.name)
    if pack is None:
        raise KeyError(
            f"no pack named {manifest.task_pack.name!r} (have: {sorted(registry.packs)})"
        )
    topology = registry.topologies.get(manifest.world.topology)
    if topology is None:
        raise KeyError(
            f"no topology named {manifest.world.topology!r} "
            f"(have: {sorted(registry.topologies)})"
        )
    runner_factory = registry.runners.get(manifest.runner)
    if runner_factory is None:
        raise KeyError(f"no runner named {manifest.runner!r} (have: {sorted(registry.runners)})")

    from testbed_adapters.sandboxes.process.sandbox import ProcessSandbox

    sandbox = ProcessSandbox() if manifest.sandbox.backend == "process" else None
    runner = runner_factory(adapters=registry.agents, artifacts=artifacts, sandbox=sandbox)
    composition = Composition(
        store=store, artifacts=artifacts, pack=pack, topology=topology, runner=runner
    )
    return composition, registry, store, artifacts
