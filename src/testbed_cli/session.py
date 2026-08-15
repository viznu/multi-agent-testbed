"""Programmatic entry points behind the CLI commands.

Keeping these out of `main.py` means tests, notebooks and other tools drive the
same code path the command line does.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import anyio

from testbed_cli.composition import Registry, Workspace, compose
from testbed_contracts.ids import derive_run_id
from testbed_contracts.manifest import ExperimentManifest
from testbed_contracts.results import RunRecord, RunResult
from testbed_eval import score_run
from testbed_kernel import RunController


def score_result(
    manifest: ExperimentManifest, result: RunResult, store: Any, registry: Registry
) -> RunResult:
    """Apply the manifest's scorers to a finished run and persist them."""
    if not manifest.scorers:
        return result
    scores = score_run(
        record=result.run,
        events=store.read(result.run.run_id),
        specs=manifest.scorers,
        registry=registry.scorers,
        verifier=result.verifier,
        agent_ids=tuple(a.agent_id for a in manifest.agents),
    )
    store.put_scores(scores.scores)
    scored = result.model_copy(update={"scores": scores})
    store.put_result(result.run.run_id, scored.model_dump(mode="json"))
    return scored


def _offset_records(records: list[RunRecord], offset: int) -> list[RunRecord]:
    """Shift runs to a later repetition index.

    `matb rerun` uses this: a rerun is a new run, so it must not collide with
    the identity of the run it repeats.
    """
    shifted = []
    for record in records:
        repetition = record.repetition + offset
        shifted.append(
            record.model_copy(
                update={
                    "repetition": repetition,
                    "run_id": derive_run_id(
                        experiment_id=record.experiment_id,
                        task_id=record.task_id,
                        env_seed=record.env_seed,
                        team_config_id=record.team_config_id,
                        partner_population_id=record.partner_population_id,
                        perturbation_id=record.perturbation_id,
                        repetition=repetition,
                    ),
                }
            )
        )
    return shifted


def run_experiment(
    manifest: ExperimentManifest,
    workspace: Path,
    *,
    only_run_id: str | None = None,
    repetition_offset: int = 0,
    crash_after_checkpoints: int | None = None,
    close_store: bool = True,
) -> list[RunResult]:
    """Plan and execute every run a manifest implies."""
    composition, registry, store, _ = compose(manifest, Workspace(workspace))
    controller = RunController(composition)
    records = controller.plan(manifest)
    if repetition_offset:
        records = _offset_records(records, repetition_offset)
    if only_run_id:
        records = [r for r in records if r.run_id == only_run_id]

    results: list[RunResult] = []

    async def go() -> None:
        await composition.runner.provision(manifest)
        try:
            for record in records:
                # Fresh handles per run: an agent must never carry state across runs.
                handles = await composition.runner.agent_handles(manifest)
                result = await controller.execute(
                    manifest, record, handles,
                    crash_after_checkpoints=crash_after_checkpoints,
                )
                results.append(score_result(manifest, result, store, registry))
        finally:
            await composition.runner.teardown()

    try:
        anyio.run(go)
    finally:
        if close_store:
            store.close()
    return results


def resume_run(manifest: ExperimentManifest, workspace: Path, run_id: str) -> RunResult:
    """Continue an interrupted run from its last complete checkpoint."""
    composition, registry, store, _ = compose(manifest, Workspace(workspace))
    controller = RunController(composition)
    record = store.get_run(run_id)

    async def go() -> RunResult:
        await composition.runner.provision(manifest)
        try:
            handles = await composition.runner.agent_handles(manifest)
            return await controller.resume(manifest, record, handles)
        finally:
            await composition.runner.teardown()

    try:
        result = anyio.run(go)
        return score_result(manifest, result, store, registry)
    finally:
        store.close()
