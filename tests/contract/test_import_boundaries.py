"""The dependency rules from the plan, enforced by reading the source.

These are architecture tests: they fail on an illegal *import*, before anything
runs. `.importlinter` states the same rules for CI; this test keeps them true
even when the linter is not installed.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"

CONTRACTS = "testbed_contracts"
PACK_SDK = "testbed_pack_sdk"
CORE = {"testbed_kernel", "testbed_store", "testbed_eval", "testbed_catalog"}
OUTER = {"testbed_adapters", "testbed_plugins", "testbed_packs"}
COMPOSITION_ROOT = "testbed_cli"

#: Third-party packages the contracts layer may depend on.
CONTRACTS_ALLOWED_EXTERNAL = {"pydantic", "typing_extensions", "annotated_types"}

STDLIB_SAFE = {
    "__future__", "abc", "ast", "asyncio", "collections", "contextlib", "dataclasses",
    "datetime", "enum", "functools", "hashlib", "importlib", "itertools", "json", "math",
    "os", "pathlib", "platform", "random", "re", "shutil", "socket", "sqlite3",
    "subprocess", "sys", "tempfile", "types", "typing",
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text("utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found


def _modules(package: str) -> list[Path]:
    return sorted((SRC / package).rglob("*.py"))


def _top_level_packages() -> list[str]:
    return sorted(p.name for p in SRC.iterdir() if p.is_dir() and p.name.startswith("testbed_"))


def test_contracts_import_nothing_from_the_testbed():
    """Contracts sit at the bottom: stdlib and validation libraries only."""
    for module in _modules(CONTRACTS):
        for imported in _imports(module):
            if imported.startswith("testbed_"):
                assert imported == CONTRACTS, (
                    f"{module.relative_to(SRC)} imports {imported}; "
                    "contracts must not depend on any other testbed package"
                )
            elif imported not in STDLIB_SAFE:
                assert imported in CONTRACTS_ALLOWED_EXTERNAL, (
                    f"{module.relative_to(SRC)} imports {imported}, which contracts may not use"
                )


def test_pack_sdk_imports_contracts_only():
    for module in _modules(PACK_SDK):
        for imported in _imports(module):
            if imported.startswith("testbed_"):
                assert imported in {CONTRACTS, PACK_SDK}, (
                    f"{module.relative_to(SRC)} imports {imported}; "
                    "the Pack SDK may import contracts only"
                )


@pytest.mark.parametrize("package", sorted(CORE))
def test_core_never_imports_an_adapter_plugin_or_pack(package: str):
    """The kernel must not know that any concrete adapter exists."""
    for module in _modules(package):
        for imported in _imports(module):
            assert imported not in OUTER, (
                f"{module.relative_to(SRC)} imports {imported}; "
                "kernel/store/eval/catalog may never import an adapter, plugin or pack"
            )
            assert imported != COMPOSITION_ROOT, (
                f"{module.relative_to(SRC)} imports the composition root"
            )


@pytest.mark.parametrize("package", sorted(OUTER))
def test_adapters_plugins_and_packs_stay_at_the_edge(package: str):
    """They may use contracts and the Pack SDK, and nothing else of ours.

    `testbed_eval` is the one deliberate exception, and only for the purity
    guard's exception type -- adapters do not reach into eval internals.
    """
    allowed = {CONTRACTS, PACK_SDK, package}
    for module in _modules(package):
        for imported in _imports(module):
            if not imported.startswith("testbed_"):
                continue
            assert imported in allowed, (
                f"{module.relative_to(SRC)} imports {imported}; "
                f"{package} may import only {sorted(allowed)}"
            )


LAYERS = {
    "testbed_store": {CONTRACTS, PACK_SDK, "testbed_store"},
    "testbed_eval": {CONTRACTS, PACK_SDK, "testbed_eval"},
    "testbed_catalog": {CONTRACTS, PACK_SDK, "testbed_catalog"},
    "testbed_kernel": {CONTRACTS, PACK_SDK, "testbed_kernel"},
}


@pytest.mark.parametrize("package", sorted(LAYERS))
def test_core_layers_do_not_depend_on_each_other(package: str):
    """Store, eval, catalog and kernel are siblings, not a stack.

    The kernel takes stores through the `EventStore` port and scoring happens in
    the composition root, so none of these four may import another.
    """
    allowed = LAYERS[package]
    for module in _modules(package):
        for imported in _imports(module):
            if not imported.startswith("testbed_"):
                continue
            assert imported in allowed, (
                f"{module.relative_to(SRC)} imports {imported}; "
                f"{package} may import only {sorted(allowed)}"
            )


def test_only_the_composition_root_discovers_plugins():
    """Entry-point discovery is a composition-root privilege."""
    offenders = []
    for package in _top_level_packages():
        if package == COMPOSITION_ROOT:
            continue
        for module in _modules(package):
            if "entry_points" in module.read_text("utf-8"):
                offenders.append(str(module.relative_to(SRC)))
    assert offenders == [], f"plug-in discovery outside the composition root: {offenders}"
