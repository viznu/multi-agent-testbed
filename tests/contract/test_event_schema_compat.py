"""Event-schema compatibility.

An event written by an older version of the testbed must still deserialise, or
the log stops being an audit trail. New optional fields are allowed; changing
the meaning of an existing field is not.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from testbed_contracts.enums import EventKind, VisibilityPolicy
from testbed_contracts.events import EVENT_SCHEMA_VERSION, Event, canonical_event_hash
from testbed_contracts.manifest import MANIFEST_SCHEMA_VERSION, ExperimentManifest

FIXTURES = Path(__file__).resolve().parents[2] / "schemas" / "fixtures"


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("event_*.json")))
def test_stored_event_fixtures_still_deserialize(path: Path):
    raw = json.loads(path.read_text("utf-8"))
    event = Event.model_validate(raw)
    assert event.event_kind in set(EventKind)
    assert event.visibility_policy in set(VisibilityPolicy)


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("manifest_*.json")))
def test_stored_manifest_fixtures_still_deserialize(path: Path):
    manifest = ExperimentManifest.model_validate(json.loads(path.read_text("utf-8")))
    assert manifest.agents


def test_schema_versions_are_pinned():
    """A change here is a deliberate act that must come with a migration note."""
    assert EVENT_SCHEMA_VERSION == "1.0.0"
    assert MANIFEST_SCHEMA_VERSION == "1.0.0"


def test_unknown_fields_are_rejected_rather_than_silently_dropped():
    """Silently ignoring an unknown field would let a newer producer's meaning
    disappear without anyone noticing."""
    raw = json.loads((FIXTURES / "event_minimal.json").read_text("utf-8"))
    raw["invented_field"] = 1
    with pytest.raises(ValidationError):
        Event.model_validate(raw)


def test_canonical_hash_ignores_volatile_fields():
    raw = json.loads((FIXTURES / "event_minimal.json").read_text("utf-8"))
    a = Event.model_validate(raw)
    b = a.model_copy(update={"event_id": "evt_different", "attempt_id": "att_different"})
    assert canonical_event_hash([a]) == canonical_event_hash([b])

    c = a.model_copy(update={"logical_time": a.logical_time + 1})
    assert canonical_event_hash([a]) != canonical_event_hash([c])
