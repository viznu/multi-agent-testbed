"""Exports: JSONL, Parquet partitions, and reproducibility bundles.

A bundle is the unit the plan's acceptance definition talks about: it must play
back on a clean machine and it must never contain a secret value, only a
reference to one.
"""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from testbed_contracts.enums import ReproducibilityLevel
from testbed_contracts.events import Event, canonical_event_hash
from testbed_contracts.results import BundleManifest
from testbed_store.sqlite import SqliteStore


def _rows(events: Sequence[Event]) -> list[dict[str, Any]]:
    return [json.loads(e.model_dump_json()) for e in events]


def export_jsonl(events: Sequence[Event], destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as fh:
        for event in events:
            fh.write(event.model_dump_json() + "\n")
    return destination


def export_parquet(events: Sequence[Event], destination: Path) -> Path:
    """Write a canonical Parquet partition for DuckDB analysis.

    Requires the `parquet` extra. Rather than silently degrading to JSON, this
    raises so an analysis pipeline never reads a file in an unexpected format.
    """
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on extras
        raise RuntimeError(
            "Parquet export needs the 'parquet' extra: pip install 'multi-agent-testbed[parquet]'"
        ) from exc

    flat = []
    for event in events:
        row = json.loads(event.model_dump_json())
        row["payload"] = json.dumps(row.get("payload", {}), sort_keys=True)
        row["redaction_metadata"] = json.dumps(row.get("redaction_metadata", {}), sort_keys=True)
        row["resource_delta"] = json.dumps(row.get("resource_delta", {}), sort_keys=True)
        row["policy_decision"] = json.dumps(row.get("policy_decision"), sort_keys=True)
        row["target_ids"] = list(row.get("target_ids") or [])
        row["authorized_view_ids"] = list(row.get("authorized_view_ids") or [])
        flat.append(row)
    destination.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(flat), destination)
    return destination


def _git_revision(root: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - environment dependent
        return "unknown"


def export_bundle(
    store: SqliteStore,
    run_id: str,
    destination: Path,
    *,
    repo_root: Path | None = None,
    artifacts_dir: Path | None = None,
) -> Path:
    """Write a complete, secret-free reproducibility bundle."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)

    run = store.get_run(run_id)
    manifest = store.get_manifest(run.manifest_hash)
    events = list(store.read(run_id))

    (destination / "manifest.json").write_text(manifest.model_dump_json(indent=2), "utf-8")
    (destination / "manifest.normalized.json").write_text(
        json.dumps(manifest.normalized(), indent=2, sort_keys=True), "utf-8"
    )
    (destination / "run.json").write_text(run.model_dump_json(indent=2), "utf-8")
    export_jsonl(events, destination / "events.jsonl")
    (destination / "checkpoints.jsonl").write_text(
        "".join(c.model_dump_json() + "\n" for c in store.checkpoints(run_id)), "utf-8"
    )
    (destination / "scores.json").write_text(
        json.dumps([json.loads(s.model_dump_json()) for s in store.scores(run_id)], indent=2),
        "utf-8",
    )
    result = store.get_result(run_id)
    if result is not None:
        (destination / "result.json").write_text(json.dumps(result, indent=2, default=str), "utf-8")
    if artifacts_dir and Path(artifacts_dir).exists():
        shutil.copytree(artifacts_dir, destination / "artifacts", dirs_exist_ok=True)

    root = repo_root or Path(__file__).resolve().parents[2]
    provenance = {
        "python": sys.version,
        "platform": platform.platform(),
        "git_revision": _git_revision(root),
        "manifest_hash": manifest.manifest_hash,
        "declared_reproducibility": str(run.reproducibility),
    }
    (destination / "provenance.json").write_text(json.dumps(provenance, indent=2), "utf-8")

    # Secret *references* only. The manifest stores names; values never enter a bundle.
    secret_refs = tuple(
        sorted(
            {
                grant.name
                for agent in manifest.agents
                for grant in agent.tool_grants
                if agent.credentials_policy != "none"
            }
        )
    )
    bundle = BundleManifest(
        run_id=run_id,
        manifest_hash=manifest.manifest_hash,
        event_hash=canonical_event_hash(events),
        declared_reproducibility=ReproducibilityLevel(run.reproducibility),
        tool_versions={"python": platform.python_version()},
        files=tuple(sorted(p.name for p in destination.iterdir())),
        secret_references=secret_refs,
    )
    (destination / "bundle.json").write_text(bundle.model_dump_json(indent=2), "utf-8")
    return destination
