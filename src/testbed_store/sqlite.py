"""SQLite-backed store for run metadata, the append-only event log, checkpoints
and scores.

Two properties matter more than anything else here:

* An idempotency key may be committed at most once per run, so an
  infrastructure retry can never create a second successful logical action.
* Reading a run returns exactly one canonical event sequence, even after a
  controller death mid-attempt.

Work that a dead attempt performed *after* its last checkpoint is moved to
`superseded_events` when a later attempt resumes past it. It is archived rather
than deleted, so the audit trail survives, but it is not part of the canonical
log and its idempotency keys are released for re-execution.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from testbed_contracts.enums import RunState
from testbed_contracts.events import Event
from testbed_contracts.manifest import ExperimentManifest
from testbed_contracts.results import Attempt, Checkpoint, RunRecord, Score

SCHEMA = """
CREATE TABLE IF NOT EXISTS manifests (
    manifest_hash TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    body          TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    run_id        TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    manifest_hash TEXT NOT NULL,
    task_id       TEXT NOT NULL,
    env_seed      INTEGER NOT NULL,
    state         TEXT NOT NULL,
    eval_set_kind TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    body          TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attempts (
    attempt_id     TEXT PRIMARY KEY,
    run_id         TEXT NOT NULL REFERENCES runs(run_id),
    attempt_number INTEGER NOT NULL,
    state          TEXT NOT NULL,
    body           TEXT NOT NULL,
    UNIQUE (run_id, attempt_number)
);
CREATE TABLE IF NOT EXISTS events (
    event_id        TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL,
    attempt_id      TEXT NOT NULL,
    sequence        INTEGER NOT NULL,
    logical_time    INTEGER NOT NULL,
    event_kind      TEXT NOT NULL,
    idempotency_key TEXT,
    body            TEXT NOT NULL,
    UNIQUE (run_id, sequence)
);
CREATE UNIQUE INDEX IF NOT EXISTS events_idem
    ON events (run_id, idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE TABLE IF NOT EXISTS superseded_events (
    event_id     TEXT PRIMARY KEY,
    run_id       TEXT NOT NULL,
    attempt_id   TEXT NOT NULL,
    sequence     INTEGER NOT NULL,
    superseded_at TEXT NOT NULL,
    body         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    run_id        TEXT NOT NULL,
    attempt_id    TEXT NOT NULL,
    sequence      INTEGER NOT NULL,
    body          TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scores (
    run_id     TEXT NOT NULL,
    scorer     TEXT NOT NULL,
    version    TEXT NOT NULL,
    body       TEXT NOT NULL,
    PRIMARY KEY (run_id, scorer, version)
);
CREATE TABLE IF NOT EXISTS results (
    run_id TEXT PRIMARY KEY,
    body   TEXT NOT NULL
);
"""


class StoreError(RuntimeError):
    pass


class SqliteStore:
    """Implements the `EventStore` port plus run/attempt/checkpoint/score access."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with closing(self._conn.cursor()) as cur:
            cur.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> SqliteStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- manifests ---------------------------------------------------------

    def put_manifest(self, manifest: ExperimentManifest) -> str:
        self._conn.execute(
            "INSERT OR IGNORE INTO manifests (manifest_hash, experiment_id, body)"
            " VALUES (?, ?, ?)",
            (manifest.manifest_hash, manifest.experiment_id, manifest.model_dump_json()),
        )
        return manifest.manifest_hash

    def get_manifest(self, manifest_hash: str) -> ExperimentManifest:
        row = self._conn.execute(
            "SELECT body FROM manifests WHERE manifest_hash = ?", (manifest_hash,)
        ).fetchone()
        if row is None:
            raise StoreError(f"no manifest {manifest_hash}")
        return ExperimentManifest.model_validate_json(row["body"])

    # -- runs and attempts -------------------------------------------------

    def put_run(self, run: RunRecord) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO runs"
            " (run_id, experiment_id, manifest_hash, task_id, env_seed, state,"
            "  eval_set_kind, created_at, body) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                run.run_id,
                run.experiment_id,
                run.manifest_hash,
                run.task_id,
                run.env_seed,
                str(run.state),
                str(run.eval_set_kind),
                run.created_at.isoformat(),
                run.model_dump_json(),
            ),
        )

    def get_run(self, run_id: str) -> RunRecord:
        row = self._conn.execute("SELECT body FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise StoreError(f"no run {run_id}")
        return RunRecord.model_validate_json(row["body"])

    def set_run_state(self, run_id: str, state: RunState) -> None:
        run = self.get_run(run_id).model_copy(update={"state": state})
        self.put_run(run)

    def list_runs(self, *, experiment_id: str | None = None) -> list[RunRecord]:
        if experiment_id:
            rows = self._conn.execute(
                "SELECT body FROM runs WHERE experiment_id = ? ORDER BY created_at",
                (experiment_id,),
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT body FROM runs ORDER BY created_at").fetchall()
        return [RunRecord.model_validate_json(r["body"]) for r in rows]

    def put_attempt(self, attempt: Attempt) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO attempts"
            " (attempt_id, run_id, attempt_number, state, body) VALUES (?,?,?,?,?)",
            (
                attempt.attempt_id,
                attempt.run_id,
                attempt.attempt_number,
                str(attempt.state),
                attempt.model_dump_json(),
            ),
        )

    def attempts(self, run_id: str) -> list[Attempt]:
        rows = self._conn.execute(
            "SELECT body FROM attempts WHERE run_id = ? ORDER BY attempt_number", (run_id,)
        ).fetchall()
        return [Attempt.model_validate_json(r["body"]) for r in rows]

    def next_attempt_number(self, run_id: str) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(attempt_number), 0) AS n FROM attempts WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return int(row["n"]) + 1

    # -- events ------------------------------------------------------------

    def append(self, event: Event) -> bool:
        """Append one event. Returns False when the idempotency key was already
        committed for this run, which is how retries are made harmless."""
        try:
            self._conn.execute(
                "INSERT INTO events"
                " (event_id, run_id, attempt_id, sequence, logical_time, event_kind,"
                "  idempotency_key, body) VALUES (?,?,?,?,?,?,?,?)",
                (
                    event.event_id,
                    event.run_id,
                    event.attempt_id,
                    event.sequence,
                    event.logical_time,
                    str(event.event_kind),
                    event.idempotency_key,
                    event.model_dump_json(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            message = str(exc).replace("events.", "")
            if "idempotency_key" in message:
                return False
            if "sequence" in message:
                raise StoreError(
                    f"sequence {event.sequence} already committed for run {event.run_id};"
                    " resume must supersede uncheckpointed work first"
                ) from exc
            raise
        return True

    def read(self, run_id: str, *, attempt_id: str | None = None) -> Sequence[Event]:
        if attempt_id:
            rows = self._conn.execute(
                "SELECT body FROM events WHERE run_id = ? AND attempt_id = ? ORDER BY sequence",
                (run_id, attempt_id),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT body FROM events WHERE run_id = ? ORDER BY sequence", (run_id,)
            ).fetchall()
        return [Event.model_validate_json(r["body"]) for r in rows]

    def next_sequence(self, run_id: str) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(sequence), -1) AS s FROM events WHERE run_id = ?", (run_id,)
        ).fetchone()
        return int(row["s"]) + 1

    def has_idempotency_key(self, run_id: str, key: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM events WHERE run_id = ? AND idempotency_key = ?", (run_id, key)
        ).fetchone()
        return row is not None

    def supersede_from(self, run_id: str, sequence: int) -> int:
        """Archive events from `sequence` onwards so a resumed attempt can redo
        them.

        `sequence` is the checkpoint's *next free* sequence number, so anything
        at or after it was written after the checkpoint and is not covered by
        it. Returns how many events were moved; nothing is lost, since rows land
        in `superseded_events` with the time they were superseded.
        """
        rows = self._conn.execute(
            "SELECT event_id, attempt_id, sequence, body FROM events"
            " WHERE run_id = ? AND sequence >= ?",
            (run_id, sequence),
        ).fetchall()
        now = datetime.now(UTC).isoformat()
        for row in rows:
            self._conn.execute(
                "INSERT OR REPLACE INTO superseded_events"
                " (event_id, run_id, attempt_id, sequence, superseded_at, body)"
                " VALUES (?,?,?,?,?,?)",
                (row["event_id"], run_id, row["attempt_id"], row["sequence"], now, row["body"]),
            )
        self._conn.execute(
            "DELETE FROM events WHERE run_id = ? AND sequence >= ?", (run_id, sequence)
        )
        return len(rows)

    def superseded(self, run_id: str) -> list[Event]:
        rows = self._conn.execute(
            "SELECT body FROM superseded_events WHERE run_id = ? ORDER BY sequence", (run_id,)
        ).fetchall()
        return [Event.model_validate_json(r["body"]) for r in rows]

    # -- checkpoints -------------------------------------------------------

    def put_checkpoint(self, checkpoint: Checkpoint) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO checkpoints"
            " (checkpoint_id, run_id, attempt_id, sequence, body) VALUES (?,?,?,?,?)",
            (
                checkpoint.checkpoint_id,
                checkpoint.run_id,
                checkpoint.attempt_id,
                checkpoint.sequence,
                checkpoint.model_dump_json(),
            ),
        )

    def latest_checkpoint(self, run_id: str) -> Checkpoint | None:
        row = self._conn.execute(
            "SELECT body FROM checkpoints WHERE run_id = ? ORDER BY sequence DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        return Checkpoint.model_validate_json(row["body"]) if row else None

    def checkpoints(self, run_id: str) -> list[Checkpoint]:
        rows = self._conn.execute(
            "SELECT body FROM checkpoints WHERE run_id = ? ORDER BY sequence", (run_id,)
        ).fetchall()
        return [Checkpoint.model_validate_json(r["body"]) for r in rows]

    # -- scores and results ------------------------------------------------

    def put_scores(self, scores: Iterable[Score]) -> None:
        for score in scores:
            self._conn.execute(
                "INSERT OR REPLACE INTO scores (run_id, scorer, version, body) VALUES (?,?,?,?)",
                (score.run_id, score.scorer, score.version, score.model_dump_json()),
            )

    def scores(self, run_id: str) -> list[Score]:
        rows = self._conn.execute(
            "SELECT body FROM scores WHERE run_id = ? ORDER BY scorer", (run_id,)
        ).fetchall()
        return [Score.model_validate_json(r["body"]) for r in rows]

    def put_result(self, run_id: str, body: dict[str, Any]) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO results (run_id, body) VALUES (?, ?)",
            (run_id, json.dumps(body, default=str)),
        )

    def get_result(self, run_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT body FROM results WHERE run_id = ?", (run_id,)
        ).fetchone()
        return json.loads(row["body"]) if row else None

    def results_for(self, experiment_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT r.body AS body FROM results r JOIN runs ru ON ru.run_id = r.run_id"
            " WHERE ru.experiment_id = ? ORDER BY ru.created_at",
            (experiment_id,),
        ).fetchall()
        return [json.loads(r["body"]) for r in rows]
