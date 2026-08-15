"""Catalog honesty tests.

The catalog is a set of claims about integration status. These tests make sure
it cannot quietly overstate itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from testbed_catalog import CatalogError, load_catalog
from testbed_catalog.model import CatalogRecord
from testbed_cli.paths import PACKAGED_CATALOG
from testbed_contracts.enums import Maturity, Runtime

CATALOG = PACKAGED_CATALOG


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(CATALOG)


def test_catalog_is_internally_consistent(catalog):
    assert catalog.verify() == []


def test_every_market_lane_has_a_record(catalog):
    assert catalog.uncovered_lanes() == []


def test_stub_and_external_records_claim_no_entry_point(catalog):
    """`stub` and `external` must mean "not integrated here", with nothing to
    call."""
    for record in catalog.records:
        if record.maturity in (Maturity.STUB, Maturity.EXTERNAL):
            assert record.entry_point is None, f"{record.record_id} claims an entry point"


def test_runnable_records_actually_import(catalog):
    """Anything claiming to be runnable here must really load."""
    from importlib import import_module

    for record in catalog.runnable():
        module_name, _, attribute = record.entry_point.partition(":")
        module = import_module(module_name)
        assert hasattr(module, attribute), f"{record.record_id}: missing {attribute}"


def test_record_ids_are_publisher_qualified():
    with pytest.raises(ValueError, match="publisher-qualified"):
        CatalogRecord(record_id="petri", title="ambiguous", lane=7)


def test_certified_requires_a_real_pin():
    """A moving ref is not a pin, and `certified` without one must be rejected."""
    record = CatalogRecord(
        record_id="someone/thing",
        title="Thing",
        lane=1,
        maturity=Maturity.CERTIFIED,
        source_url="https://example.invalid/thing",
        revision="main",
        license="MIT",
        entry_point="thing:THING",
        capabilities=("runner",),
    )
    gaps = record.certification_gaps()
    assert any("pinned revision" in gap for gap in gaps)


def test_certified_oci_record_requires_an_image_digest():
    record = CatalogRecord(
        record_id="someone/container-thing",
        title="Container thing",
        lane=4,
        runtime=Runtime.OCI,
        maturity=Maturity.CERTIFIED,
        source_url="https://example.invalid/thing",
        revision="v1.2.3",
        license="MIT",
        entry_point="thing:THING",
        capabilities=("benchmark",),
    )
    assert "image digest" in record.certification_gaps()


def test_duplicate_record_ids_are_rejected(tmp_path: Path):
    (tmp_path / "a.yaml").write_text(
        "records:\n"
        "  - {record_id: x/y, title: A, lane: 1}\n"
        "  - {record_id: x/y, title: B, lane: 2}\n",
        "utf-8",
    )
    with pytest.raises(CatalogError, match="duplicate"):
        load_catalog(tmp_path)


def test_capability_vocabulary_is_open_but_reported(tmp_path: Path):
    """A new kind of tool is a new tag, not a new kernel contract."""
    (tmp_path / "a.yaml").write_text(
        "records:\n  - {record_id: x/y, title: A, lane: 1, capabilities: [brand_new_thing]}\n",
        "utf-8",
    )
    catalog = load_catalog(tmp_path)
    assert catalog.unknown_capabilities() == ["brand_new_thing"]
    assert catalog.verify() != []  # only because lanes 2..15 are uncovered here
