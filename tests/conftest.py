"""Shared fixtures.

Every test runs against the deterministic scripted agent and the fake/process
sandbox, so the whole suite is offline and reproducible.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from testbed_cli.loader import load_manifest

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path / "workspace"


@pytest.fixture
def solo_manifest():
    return load_manifest(EXAMPLES / "solo_lookup.yaml")


@pytest.fixture
def coop_manifest():
    return load_manifest(EXAMPLES / "coop_codeword.yaml")


@pytest.fixture
def baseline_manifest():
    return load_manifest(EXAMPLES / "coop_baseline.yaml")


@pytest.fixture
def mixed_manifest():
    return load_manifest(EXAMPLES / "mixed_split.yaml")
