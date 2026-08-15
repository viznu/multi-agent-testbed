"""Run lifecycle: plan, execute, checkpoint, resume, verify.

The controller owns the state machine
`planned -> provisioning -> running -> verifying -> scoring -> complete`,
with `cancelled`, `task_failed`, `policy_blocked` and `infra_failed` as distinct
terminal outcomes. Infrastructure failures are never reported as task failures.

Scoring itself lives in `testbed_eval` and is applied by the composition root,
so the kernel never depends on scorer implementations.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from testbed_contracts.enums import (
    EnvMode,
    EventKind,
    ReproducibilityLevel,
    RunState,
    WorldDriverKind,
)
from testbed_contracts.events import canonical_event_hash
from testbed_contracts.ids import content_hash, derive_run_id, short_hash
from testbed_contracts.manifest import ExperimentManifest
from testbed_contracts.ports import AgentHandle
from testbed_contracts.results import Attempt, Checkpoint, RunRecord, RunResult, VerifierResult
from testbed_kernel.drivers import DriverOutcome, DriverState, EnvDriver, SessionDriver
from testbed_kernel.errors import ResumeIncompatible
from testbed_kernel.journal import EventJournal
from testbed_kernel.measures import compute_measures
from testbed_kernel.rng import DeterministicRng
from testbed_kernel.world import WORLD_ACTOR, World
from testbed_pack_sdk.hooks import Pack, TaskCase


@dataclass
class Composition:
    """Everything the composition root resolved for this experiment.

    The kernel receives already-loaded plug-ins; it never performs discovery, so
    it cannot accidentally import an adapter or pack.
    """

    store: Any
    artifacts: Any
    pack: Pack
    topology: Any
    runner: Any


class RunController:
    """Executes and resumes a single run."""

    def __init__(self, composition: Composition) -> None:
        self.c = composition

    # -- planning ----------------------------------------------------------

    def plan(self, manifest: ExperimentManifest) -> list[RunRecord]:
        """Expand a manifest into the runs it implies.

        Attempts are deliberately absent here: an infrastructure retry adds an
        attempt to an existing run rather than creating a new one.
        """
        cases = self._selected_cases(manifest)
        perturbations: Sequence[str | None] = manifest.perturbations or (None,)
        records: list[RunRecord] = []
        for case in cases:
            for seed in manifest.seeds:
                for perturbation in perturbations:
                    for repetition in range(manifest.repetitions):
                        run_id = derive_run_id(
                            experiment_id=manifest.experiment_id,
                            task_id=case.task_id,
                            env_seed=seed,
                            team_config_id=manifest.team.team_config_id,
                            partner_population_id=manifest.team.partner_population_id,
                            perturbation_id=perturbation,
                            repetition=repetition,
                        )
                        records.append(
                            RunRecord(
                                run_id=run_id,
                                experiment_id=manifest.experiment_id,
                                manifest_hash=manifest.manifest_hash,
                                task_id=case.task_id,
                                env_seed=seed,
                                team_config_id=manifest.team.team_config_id,
                                partner_population_id=manifest.team.partner_population_id,
                                perturbation_id=perturbation,
                                repetition=repetition,
                                eval_set_kind=case.eval_set_kind,
                                reproducibility=manifest.declared_reproducibility(),
                            )
                        )
        return records

    def _selected_cases(self, manifest: ExperimentManifest) -> list[TaskCase]:
        cases = list(self.c.pack.tasks.cases())
        selector = manifest.task_pack.sample_selector
        if selector:
            wanted = set(selector)
            cases = [c for c in cases if c.task_id in wanted]
            missing = wanted - {c.task_id for c in cases}
            if missing:
                raise KeyError(f"pack has no such task ids: {sorted(missing)}")
        return cases

    # -- execution ---------------------------------------------------------

    async def execute(
        self,
        manifest: ExperimentManifest,
        record: RunRecord,
        agents: Mapping[str, AgentHandle],
        *,
        crash_after_checkpoints: int | None = None,
    ) -> RunResult:
        """Run one record to a terminal state.

        `crash_after_checkpoints` simulates controller death immediately after a
        checkpoint boundary; the reproducibility tests use it to prove that a
        resume neither duplicates nor loses completed work.
        """
        store = self.c.store
        store.put_manifest(manifest)
        store.put_run(record)

        attempt = self._start_attempt(record)
        world, driver, journal = self._build(manifest, record, attempt, agents)
        return await self._drive(
            manifest, record, attempt, world, driver, journal, agents,
            crash_after_checkpoints=crash_after_checkpoints,
        )

    async def resume(
        self,
        manifest: ExperimentManifest,
        record: RunRecord,
        agents: Mapping[str, AgentHandle],
    ) -> RunResult:
        """Continue an interrupted run from its last complete checkpoint."""
        store = self.c.store
        checkpoint = store.latest_checkpoint(record.run_id)
        if checkpoint is None:
            return await self.execute(manifest, record, agents)
        stored_manifest = store.get_manifest(record.manifest_hash)
        if stored_manifest.manifest_hash != manifest.manifest_hash:
            raise ResumeIncompatible(
                "manifest hash changed since the checkpoint was written"
            )

        # Work performed after the last checkpoint is archived, not replayed:
        # it is released so the resumed attempt can redo it deterministically.
        superseded = store.supersede_from(record.run_id, checkpoint.sequence)

        attempt = self._start_attempt(record, resumed_from=checkpoint.checkpoint_id)
        world, driver, journal = self._build(
            manifest, record, attempt, agents, checkpoint=checkpoint
        )
        journal.commit(
            kind=EventKind.ATTEMPT_STARTED,
            actor_id=WORLD_ACTOR,
            logical_time=world.logical_time,
            payload={
                "attempt_id": attempt.attempt_id,
                "resumed_from": checkpoint.checkpoint_id,
                "superseded_events": superseded,
            },
        )
        for agent_id, ref in checkpoint.agent_snapshots.items():
            handle = agents.get(agent_id)
            if handle is not None:
                await handle.restore(ref)
        return await self._drive(manifest, record, attempt, world, driver, journal, agents)

    # -- internals ---------------------------------------------------------

    def _start_attempt(self, record: RunRecord, *, resumed_from: str | None = None) -> Attempt:
        number = self.c.store.next_attempt_number(record.run_id)
        attempt = Attempt(
            attempt_id=f"att_{short_hash({'run': record.run_id, 'n': number}, 12)}",
            run_id=record.run_id,
            attempt_number=number,
            state=RunState.PROVISIONING,
            resumed_from_checkpoint=resumed_from,
        )
        self.c.store.put_attempt(attempt)
        return attempt

    def _build(
        self,
        manifest: ExperimentManifest,
        record: RunRecord,
        attempt: Attempt,
        agents: Mapping[str, AgentHandle],
        *,
        checkpoint: Checkpoint | None = None,
    ) -> tuple[World, SessionDriver | EnvDriver, EventJournal]:
        case = self.c.pack.case(record.task_id)
        journal_state = (checkpoint.driver_state.get("journal", {}) if checkpoint else {})
        journal = EventJournal(
            self.c.store,
            run_id=record.run_id,
            attempt_id=attempt.attempt_id,
            sequence=int(journal_state.get("sequence", 0)),
            logical_action_index=int(journal_state.get("logical_action_index", 0)),
        )
        rng = (
            DeterministicRng.restore(checkpoint.rng_state)
            if checkpoint
            else DeterministicRng(record.env_seed)
        )
        world = World(manifest=manifest, case=case, journal=journal, rng=rng)
        driver_state = (
            DriverState.load(checkpoint.driver_state.get("driver", {}))
            if checkpoint
            else DriverState()
        )
        if checkpoint:
            world.restore(checkpoint.world_snapshot)

        async def checkpoint_hook(state: DriverState) -> None:
            await self._write_checkpoint(record, attempt, world, state, journal, agents)

        if manifest.world.driver is WorldDriverKind.ENV:
            driver: SessionDriver | EnvDriver = EnvDriver(
                world=world,
                agents=agents,
                topology=self.c.topology,
                pack=self.c.pack,
                mode=EnvMode(manifest.world.env_mode),
                state=driver_state,
                checkpoint_hook=checkpoint_hook,
            )
        else:
            driver = SessionDriver(
                world=world,
                agents=agents,
                topology=self.c.topology,
                pack=self.c.pack,
                state=driver_state,
                checkpoint_hook=checkpoint_hook,
            )
        return world, driver, journal

    async def _write_checkpoint(
        self,
        record: RunRecord,
        attempt: Attempt,
        world: World,
        driver_state: DriverState,
        journal: EventJournal,
        agents: Mapping[str, AgentHandle],
    ) -> None:
        snapshots = {}
        for agent_id, handle in agents.items():
            ref = await handle.snapshot()
            if ref is not None:
                snapshots[agent_id] = ref
        checkpoint = Checkpoint(
            checkpoint_id=f"ckpt_{short_hash({'run': record.run_id, 'seq': journal.sequence}, 14)}",
            run_id=record.run_id,
            attempt_id=attempt.attempt_id,
            sequence=journal.sequence,
            logical_time=world.logical_time,
            world_snapshot=world.snapshot(),
            driver_state={"driver": driver_state.dump(), "journal": journal.state()},
            agent_snapshots=snapshots,
            pending_deliveries=world.queue.dump(),
            rng_state=world.rng.state(),
        )
        self.c.store.put_checkpoint(checkpoint)
        self._checkpoints_written += 1
        journal.commit(
            kind=EventKind.WORLD_SNAPSHOT_CREATED,
            actor_id=WORLD_ACTOR,
            logical_time=world.logical_time,
            payload={"checkpoint_id": checkpoint.checkpoint_id, "sequence": checkpoint.sequence},
        )
        if (
            self._crash_after_checkpoints is not None
            and self._checkpoints_written >= self._crash_after_checkpoints
        ):
            raise SimulatedControllerDeath(checkpoint.checkpoint_id)

    _checkpoints_written: int = 0
    _crash_after_checkpoints: int | None = None

    async def _drive(
        self,
        manifest: ExperimentManifest,
        record: RunRecord,
        attempt: Attempt,
        world: World,
        driver: SessionDriver | EnvDriver,
        journal: EventJournal,
        agents: Mapping[str, AgentHandle],
        *,
        crash_after_checkpoints: int | None = None,
    ) -> RunResult:
        self._crash_after_checkpoints = crash_after_checkpoints
        self._checkpoints_written = 0
        store = self.c.store

        journal.commit(
            kind=EventKind.RUN_STARTED,
            actor_id=WORLD_ACTOR,
            logical_time=world.logical_time,
            payload={
                "run_id": record.run_id,
                "task_id": record.task_id,
                "manifest_hash": manifest.manifest_hash,
                "agents": [a.agent_id for a in manifest.agents],
            },
        )
        for spec in manifest.agents:
            journal.commit(
                kind=EventKind.AGENT_REGISTERED,
                actor_id=spec.agent_id,
                logical_time=world.logical_time,
                payload={
                    "adapter": spec.adapter,
                    "model": spec.model,
                    "role": spec.role,
                    "prompt_digest": spec.prompt_digest,
                },
            )

        store.put_run(record.model_copy(update={"state": RunState.RUNNING}))
        store.put_attempt(attempt.model_copy(update={"state": RunState.RUNNING}))

        try:
            outcome: DriverOutcome = await driver.run()
        except SimulatedControllerDeath:
            store.put_attempt(attempt.model_copy(update={"state": RunState.INFRA_FAILED}))
            store.put_run(record.model_copy(update={"state": RunState.INFRA_FAILED}))
            raise
        except Exception as exc:  # infrastructure failure, never a task failure
            store.put_attempt(attempt.model_copy(update={"state": RunState.INFRA_FAILED}))
            store.put_run(record.model_copy(update={"state": RunState.INFRA_FAILED}))
            result = RunResult(
                run=record,
                attempt_id=attempt.attempt_id,
                state=RunState.INFRA_FAILED,
                event_count=journal.sequence,
                logical_time=world.logical_time,
                attrition_reason=f"{type(exc).__name__}: {exc}",
                reproducibility=ReproducibilityLevel(record.reproducibility),
            )
            store.put_result(record.run_id, result.model_dump(mode="json"))
            return result

        verifier_result = self._verify(record, world, journal)
        state = self._terminal_state(outcome, verifier_result)

        journal.commit(
            kind=EventKind.RUN_FINISHED,
            actor_id=WORLD_ACTOR,
            logical_time=world.logical_time,
            payload={"state": str(state), "reason": outcome.reason},
        )

        events = list(store.read(record.run_id))
        result = RunResult(
            run=record.model_copy(update={"state": state}),
            attempt_id=attempt.attempt_id,
            state=state,
            verifier=verifier_result,
            event_count=len(events),
            logical_time=world.logical_time,
            measures=compute_measures(events, world),
            attrition_reason=outcome.limit_hit if state is RunState.POLICY_BLOCKED else None,
            reproducibility=ReproducibilityLevel(record.reproducibility),
        )
        store.put_run(result.run)
        store.put_attempt(attempt.model_copy(update={"state": state}))
        store.put_result(record.run_id, result.model_dump(mode="json"))
        return result

    def _verify(
        self, record: RunRecord, world: World, journal: EventJournal
    ) -> VerifierResult | None:
        verifiers = self.c.pack.verifiers
        verifier = verifiers.get(record.task_id) or verifiers.get("default")
        if verifier is None:
            return None
        from testbed_contracts.events import EventView

        events = EventView(self.c.store.read(record.run_id), view="omniscient")
        result = verifier.verify(world.snapshot_view(), events)
        if result.per_agent_payoff:
            world.assign_payoffs(result.per_agent_payoff)
        journal.commit(
            kind=EventKind.VERIFIER_RESULT,
            actor_id=WORLD_ACTOR,
            logical_time=world.logical_time,
            payload={
                "verifier": verifier.name,
                "version": verifier.version,
                "success": result.success,
                "reward": result.reward,
                "per_agent_payoff": result.per_agent_payoff,
                "detail": result.detail,
            },
        )
        return result

    @staticmethod
    def _terminal_state(outcome: DriverOutcome, verifier: VerifierResult | None) -> RunState:
        """Budget exhaustion is `policy_blocked`, not a task failure.

        Running out of logical time or events is an ordinary episode end: the
        verifier still judges whatever the agents achieved.
        """
        soft_limits = {"max_logical_time", "max_events"}
        if outcome.limit_hit and outcome.limit_hit not in soft_limits:
            return RunState.POLICY_BLOCKED
        if verifier is None:
            return RunState.COMPLETE
        return RunState.COMPLETE if verifier.success else RunState.TASK_FAILED


class SimulatedControllerDeath(RuntimeError):
    """Raised by tests at a checkpoint boundary to emulate a dead controller."""

    def __init__(self, checkpoint_id: str) -> None:
        super().__init__(f"controller died after checkpoint {checkpoint_id}")
        self.checkpoint_id = checkpoint_id


def run_event_hash(store: Any, run_id: str) -> str:
    """Determinism fingerprint of a run's canonical event sequence."""
    return canonical_event_hash(list(store.read(run_id)))


def manifest_fingerprint(manifest: ExperimentManifest) -> str:
    return content_hash(manifest.normalized())
