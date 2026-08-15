"""The on/off contract.

An integration is either usable or it fails loudly with a remediation. The one
outcome that must never happen is a manifest naming an integration and getting
something else, or getting a run that quietly skipped it.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from testbed_catalog import load_catalog
from testbed_catalog.availability import (
    IntegrationIndex,
    IntegrationState,
    Switches,
    availability_of,
)
from testbed_catalog.model import CatalogRecord
from testbed_cli.composition import Registry, Workspace, compose
from testbed_cli.integrations import IntegrationUnavailable, load_index
from testbed_cli.paths import PACKAGED_CATALOG
from testbed_contracts.enums import Maturity

ROOT = Path(__file__).resolve().parents[2]
CATALOG = PACKAGED_CATALOG


def _record(**overrides) -> CatalogRecord:
    base = {
        "record_id": "someone/thing",
        "title": "Thing",
        "lane": 1,
        "maturity": Maturity.EXPERIMENTAL,
        "entry_point": "thing:THING",
        "plugin_group": "packs",
        "plugin_name": "thing",
    }
    return CatalogRecord(**{**base, **overrides})


def test_a_record_with_no_adapter_has_no_switch():
    stub = CatalogRecord(
        record_id="someone/thing", title="Thing", lane=1, maturity=Maturity.STUB
    )
    status = availability_of(stub, Switches())
    assert status.state is IntegrationState.NO_ADAPTER
    assert "no adapter exists" in status.reason()
    assert status.remediation() is None


def test_a_missing_dependency_names_the_extra_that_installs_it():
    record = _record(requires=("definitely_not_installed_xyz",), extra="lm-eval")
    status = availability_of(record, Switches())
    assert status.state is IntegrationState.NOT_INSTALLED
    assert "definitely_not_installed_xyz" in status.reason()
    assert status.remediation() == "pip install 'multi-agent-testbed[lm-eval]'"


def test_a_missing_dependency_with_no_extra_says_so_rather_than_guessing():
    status = availability_of(_record(requires=("definitely_not_installed_xyz",)), Switches())
    assert "no pip extra is declared" in status.reason()


def test_a_missing_binary_is_detected():
    record = _record(requires_binaries=("definitely-not-a-binary-xyz",))
    status = availability_of(record, Switches())
    assert status.state is IntegrationState.NOT_INSTALLED
    assert status.missing_binaries == ("definitely-not-a-binary-xyz",)


def test_probing_never_imports_the_dependency():
    """`find_spec` answers the question; importing a heavy stack to ask it would
    make `matb doctor` unusable."""
    import sys

    availability_of(_record(requires=("json",)), Switches())
    assert "lm_eval" not in sys.modules


def test_switching_off_is_independent_of_installing():
    record = _record()
    assert availability_of(record, Switches()).state is IntegrationState.ACTIVE
    off = Switches(disabled=frozenset({record.record_id}))
    status = availability_of(record, off)
    assert status.state is IntegrationState.DISABLED
    assert status.remediation() == "matb integrations enable someone/thing"


def test_disabled_wins_over_enabled():
    record = _record()
    both = Switches(disabled=frozenset({record.record_id}), enabled=frozenset({record.record_id}))
    assert availability_of(record, both).state is IntegrationState.DISABLED


def test_an_opt_in_integration_stays_off_until_enabled():
    record = _record(default_enabled=False)
    assert availability_of(record, Switches()).state is IntegrationState.DISABLED
    on = Switches(enabled=frozenset({record.record_id}))
    assert availability_of(record, on).state is IntegrationState.ACTIVE


def test_switches_round_trip_through_the_file(tmp_path: Path):
    path = tmp_path / "integrations.toml"
    Switches().with_change("a/b", enabled=False).save(path)
    reloaded = Switches.load(path)
    assert reloaded.disabled == frozenset({"a/b"})

    reloaded.with_change("a/b", enabled=True).save(path)
    assert Switches.load(path).disabled == frozenset()
    assert tomllib.loads(path.read_text("utf-8"))["integrations"]["enabled"] == ["a/b"]


def test_a_missing_switches_file_means_defaults(tmp_path: Path):
    assert Switches.load(tmp_path / "nope.toml").disabled == frozenset()


def test_composing_a_disabled_integration_fails_with_a_remediation(solo_manifest, tmp_path: Path):
    catalog = load_catalog(CATALOG)
    index = IntegrationIndex(catalog.records, Switches(disabled=frozenset({"testbed/smoke-pack"})))
    with pytest.raises(IntegrationUnavailable) as excinfo:
        compose(solo_manifest, Workspace(tmp_path / "ws"), index=index)
    message = str(excinfo.value)
    assert "switched off" in message
    assert "matb integrations enable testbed/smoke-pack" in message


def test_a_disabled_agent_adapter_is_caught_too(solo_manifest, tmp_path: Path):
    catalog = load_catalog(CATALOG)
    index = IntegrationIndex(
        catalog.records, Switches(disabled=frozenset({"testbed/scripted-agent"}))
    )
    with pytest.raises(IntegrationUnavailable, match="scripted"):
        compose(solo_manifest, Workspace(tmp_path / "ws"), index=index)


def test_an_unknown_plugin_is_still_an_unknown_plugin(solo_manifest, tmp_path: Path):
    """Switched off and never existed are different errors."""
    broken = solo_manifest.model_copy(
        update={"task_pack": solo_manifest.task_pack.model_copy(update={"name": "nonexistent"})}
    )
    with pytest.raises(KeyError, match="nonexistent"):
        compose(broken, Workspace(tmp_path / "ws"))


def test_a_plugin_without_a_catalog_record_is_allowed(tmp_path: Path):
    """Third-party packs are not required to appear in this repository's catalog."""
    index = IntegrationIndex([], Switches())
    assert index.for_plugin("packs", "someone_elses_pack") is None


def test_every_registered_builtin_plugin_has_a_catalog_record():
    """Otherwise an integration exists with no switch and no documented status."""
    index = load_index(CATALOG)
    registry = Registry.discover()
    groups = {
        "packs": registry.packs,
        "agents": registry.agents,
        "runners": registry.runners,
        "scorers": {"builtin": True},
    }
    missing = [
        f"{group}:{name}"
        for group, plugins in groups.items()
        for name in plugins
        if index.for_plugin(group, name) is None
    ]
    assert missing == [], f"registered plug-ins with no catalog record: {missing}"


def test_every_runnable_record_declares_an_installable_extra_or_no_dependencies():
    """A runnable integration must be installable by following its own record."""
    raw = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
    declared = set(raw["project"]["optional-dependencies"])
    catalog = load_catalog(CATALOG)
    problems = [
        r.record_id
        for r in catalog.runnable()
        if (r.extra and r.extra not in declared) or (r.requires and not r.extra)
    ]
    assert problems == [], f"runnable records with unusable install instructions: {problems}"


def test_the_committed_switches_file_is_neutral():
    """The repository ships with nothing silently switched off."""
    switches = Switches.load(ROOT / "integrations.toml")
    assert switches.disabled == frozenset()


def test_catalog_resolution_is_explicit_and_ordered(tmp_path: Path, monkeypatch):
    """Running `matb` from elsewhere must not silently switch checking off."""
    from testbed_cli.paths import PACKAGED_CATALOG, resolve_catalog

    monkeypatch.delenv("MATB_CATALOG", raising=False)
    # No project catalog anywhere above tmp_path: fall back to the packaged one.
    assert resolve_catalog(start=tmp_path).path == PACKAGED_CATALOG

    project = tmp_path / "project"
    (project / "catalog").mkdir(parents=True)
    nested = project / "a" / "b"
    nested.mkdir(parents=True)
    assert resolve_catalog(start=nested).path == project / "catalog"

    monkeypatch.setenv("MATB_CATALOG", str(tmp_path / "elsewhere"))
    assert resolve_catalog(start=nested).path == tmp_path / "elsewhere"
    assert resolve_catalog(tmp_path / "explicit", start=nested).path == tmp_path / "explicit"


def test_a_catalog_path_that_does_not_exist_is_an_error_not_an_empty_catalog(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_index(tmp_path / "missing")


def test_the_packaged_catalog_is_always_available():
    """A pip-installed testbed must behave like one run from a source tree."""
    from testbed_cli.paths import PACKAGED_CATALOG

    assert PACKAGED_CATALOG.is_dir()
    assert list(PACKAGED_CATALOG.glob("*.yaml"))
    assert load_catalog(PACKAGED_CATALOG).verify() == []


def test_switches_are_found_from_a_subdirectory(tmp_path: Path, monkeypatch):
    from testbed_cli.paths import resolve_switches

    monkeypatch.delenv("MATB_SWITCHES", raising=False)
    (tmp_path / "integrations.toml").write_text(
        '[integrations]\ndisabled = ["a/b"]\nenabled = []\n', "utf-8"
    )
    nested = tmp_path / "deep" / "deeper"
    nested.mkdir(parents=True)
    found = resolve_switches(start=nested)
    assert found.path == tmp_path / "integrations.toml"
    assert Switches.load(found.path).disabled == frozenset({"a/b"})
