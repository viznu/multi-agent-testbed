"""Store guarantees and reproducibility bundles."""

from __future__ import annotations

from pathlib import Path

import pytest

from testbed_cli.composition import Workspace
from testbed_cli.session import run_experiment
from testbed_contracts.enums import EventKind
from testbed_contracts.events import Event, canonical_event_hash
from testbed_store import LocalArtifactStore, SqliteStore, StoreError
from testbed_store.export import export_bundle, export_jsonl


def _event(sequence: int, key: str | None = None) -> Event:
    return Event(
        event_id=f"evt_{sequence}",
        run_id="run_x",
        attempt_id="att_1",
        sequence=sequence,
        logical_time=sequence,
        event_kind=EventKind.AGENT_MESSAGE,
        actor_id="a",
        idempotency_key=key,
        payload={"n": sequence},
    )


def test_idempotency_key_may_be_committed_once(tmp_path: Path):
    store = SqliteStore(tmp_path / "db.sqlite")
    assert store.append(_event(0, "idem_a")) is True
    assert store.append(_event(1, "idem_a")) is False, "a repeated key must be rejected"
    assert len(store.read("run_x")) == 1
    store.close()


def test_a_reused_sequence_is_an_error_not_silent_overwrite(tmp_path: Path):
    store = SqliteStore(tmp_path / "db.sqlite")
    store.append(_event(0, "idem_a"))
    with pytest.raises(StoreError, match="already committed"):
        store.append(_event(0, "idem_b"))
    store.close()


def test_superseded_events_are_archived_not_deleted(tmp_path: Path):
    store = SqliteStore(tmp_path / "db.sqlite")
    for i in range(5):
        store.append(_event(i, f"idem_{i}"))
    moved = store.supersede_from("run_x", 3)
    assert moved == 2
    assert [e.sequence for e in store.read("run_x")] == [0, 1, 2]
    assert [e.sequence for e in store.superseded("run_x")] == [3, 4]
    # The archived keys are released so the resumed attempt can redo the work.
    assert store.append(_event(3, "idem_3")) is True
    store.close()


def test_artifacts_are_content_addressed(tmp_path: Path):
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    a = artifacts.put(b"hello")
    b = artifacts.put(b"hello")
    assert a.hash == b.hash
    assert artifacts.get(a) == b"hello"
    assert artifacts.put(b"other").hash != a.hash


def test_bundle_round_trips_and_matches_the_event_hash(coop_manifest, tmp_path: Path):
    manifest = coop_manifest.model_copy(update={"seeds": (0,)})
    result = run_experiment(manifest, tmp_path / "ws")[0]

    space = Workspace(tmp_path / "ws")
    store, _ = space.open()
    destination = export_bundle(
        store, result.run.run_id, tmp_path / "bundle", artifacts_dir=space.artifacts_path
    )
    events = list(store.read(result.run.run_id))
    store.close()

    import json

    bundle = json.loads((destination / "bundle.json").read_text("utf-8"))
    assert bundle["event_hash"] == canonical_event_hash(events)

    # The exported events deserialise on their own, which is what a clean
    # machine has to be able to do.
    lines = (destination / "events.jsonl").read_text("utf-8").strip().splitlines()
    restored = [Event.model_validate_json(line) for line in lines]
    assert canonical_event_hash(restored) == bundle["event_hash"]


def test_jsonl_export_is_stable(coop_manifest, tmp_path: Path):
    result = run_experiment(coop_manifest.model_copy(update={"seeds": (0,)}), tmp_path / "ws")[0]
    store, _ = Workspace(tmp_path / "ws").open()
    events = list(store.read(result.run.run_id))
    store.close()
    a = export_jsonl(events, tmp_path / "a.jsonl").read_text("utf-8")
    b = export_jsonl(events, tmp_path / "b.jsonl").read_text("utf-8")
    assert a == b
