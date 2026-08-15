"""The M0 gate.

A fake agent, world, scorer, store and runner compose into a working run
without importing a single concrete adapter, plug-in or pack. Everything below
is defined in this file from contracts and the Pack SDK alone.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

import anyio

from testbed_contracts import (
    AgentDescriptor,
    AgentEvent,
    AgentEventKind,
    AgentRequest,
    BlobRef,
    Event,
    EventView,
    ExperimentManifest,
    Health,
    Score,
    TaskPackSpec,
)
from testbed_contracts.manifest import AgentSpec, LimitsSpec, WorldSpec
from testbed_contracts.results import VerifierResult
from testbed_kernel import Composition, RunController
from testbed_pack_sdk import (
    Pack,
    Proposal,
    RoutingDecision,
    ScorerContext,
    StateChange,
    TaskCase,
    WorldAction,
    WorldSnapshotView,
)


class FakeStore:
    """An in-memory `EventStore` with the one guarantee that matters."""

    def __init__(self) -> None:
        self.events: list[Event] = []
        self.keys: set[str] = set()
        self.runs: dict[str, Any] = {}
        self.attempts: list[Any] = []
        self.checkpoints: list[Any] = []
        self.manifests: dict[str, Any] = {}
        self.results: dict[str, Any] = {}

    def append(self, event: Event) -> bool:
        if event.idempotency_key and event.idempotency_key in self.keys:
            return False
        if event.idempotency_key:
            self.keys.add(event.idempotency_key)
        self.events.append(event)
        return True

    def read(self, run_id: str, *, attempt_id: str | None = None) -> Sequence[Event]:
        return [e for e in self.events if e.run_id == run_id]

    def next_sequence(self, run_id: str) -> int:
        return len(self.read(run_id))

    def put_manifest(self, manifest: Any) -> str:
        self.manifests[manifest.manifest_hash] = manifest
        return manifest.manifest_hash

    def get_manifest(self, manifest_hash: str) -> Any:
        return self.manifests[manifest_hash]

    def put_run(self, run: Any) -> None:
        self.runs[run.run_id] = run

    def get_run(self, run_id: str) -> Any:
        return self.runs[run_id]

    def put_attempt(self, attempt: Any) -> None:
        self.attempts.append(attempt)

    def next_attempt_number(self, run_id: str) -> int:
        return len([a for a in self.attempts if a.run_id == run_id]) + 1

    def put_checkpoint(self, checkpoint: Any) -> None:
        self.checkpoints.append(checkpoint)

    def latest_checkpoint(self, run_id: str) -> Any | None:
        relevant = [c for c in self.checkpoints if c.run_id == run_id]
        return relevant[-1] if relevant else None

    def put_result(self, run_id: str, body: dict[str, Any]) -> None:
        self.results[run_id] = body

    def get_result(self, run_id: str) -> dict[str, Any] | None:
        return self.results.get(run_id)

    def put_scores(self, scores: Any) -> None:
        return None


class FakeAgent:
    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        self.turns = 0

    async def describe(self) -> AgentDescriptor:
        return AgentDescriptor(agent_id=self.agent_id, adapter="fake", model="fake/none")

    async def invoke(self, request: AgentRequest) -> AsyncIterator[AgentEvent]:
        self.turns += 1
        yield AgentEvent(
            kind=AgentEventKind.WORLD_ACTION,
            action={"kind": "press", "button": "red"},
        )

    async def cancel(self, invocation_id: str) -> None: ...

    async def health(self) -> Health:
        return Health()

    async def snapshot(self) -> BlobRef | None:
        return None

    async def restore(self, snapshot: BlobRef) -> None: ...


class FakeRunner:
    name = "fake_runner"

    def __init__(self) -> None:
        self.provisioned = False

    async def provision(self, manifest: Any) -> None:
        self.provisioned = True

    async def agent_handles(self, manifest: Any) -> dict[str, Any]:
        return {spec.agent_id: FakeAgent(spec.agent_id) for spec in manifest.agents}

    async def teardown(self) -> None: ...


class FakeTopology:
    name = "fake_topology"

    def route(self, proposal: Proposal, world: Any) -> RoutingDecision:
        return RoutingDecision(recipients=())

    def opening_instruction(self, agent_id: str, world: Any) -> str | None:
        return "go"


class PressHandler:
    handles = ("press",)

    def apply(self, action: WorldAction, state: WorldSnapshotView) -> StateChange:
        return StateChange(updates={"pressed": action.args.get("button")}, finished=True)


class FakeTasks:
    name = "fake"
    revision = "0.0.1"

    def cases(self) -> Sequence[TaskCase]:
        return (TaskCase(task_id="press_it", instruction="press the red button"),)


class FakeVerifier:
    name = "fake_verifier"
    version = "0.0.1"

    def verify(self, state: WorldSnapshotView, events: EventView) -> VerifierResult:
        return VerifierResult(success=state.state.get("pressed") == "red", reward=1.0)


class FakeScorer:
    name = "fake_scorer"
    version = "0.0.1"
    kind = "deterministic"

    def score(self, events: EventView, context: ScorerContext) -> Sequence[Score]:
        return [
            Score(
                run_id=context.run_id,
                scorer=self.name,
                version=self.version,
                kind=self.kind,
                value=float(len(events)),
            )
        ]


def _manifest() -> ExperimentManifest:
    return ExperimentManifest(
        experiment_id="m0_fake",
        task_pack=TaskPackSpec(name="fake", revision="0.0.1"),
        runner="fake_runner",
        world=WorldSpec(topology="fake_topology"),
        agents=(AgentSpec(agent_id="a1", adapter="fake"),),
        limits=LimitsSpec(max_logical_time=10, checkpoint_every_events=3),
    )


def test_fakes_compose_into_a_complete_run():
    store = FakeStore()
    pack = Pack(
        name="fake",
        revision="0.0.1",
        tasks=FakeTasks(),
        action_handlers=(PressHandler(),),
        verifiers={"default": FakeVerifier()},
    )
    runner = FakeRunner()
    composition = Composition(
        store=store, artifacts=None, pack=pack, topology=FakeTopology(), runner=runner
    )
    controller = RunController(composition)
    manifest = _manifest()
    records = controller.plan(manifest)
    assert len(records) == 1

    async def go():
        await runner.provision(manifest)
        handles = await runner.agent_handles(manifest)
        return await controller.execute(manifest, records[0], handles)

    result = anyio.run(go)
    assert result.state == "complete"
    assert result.verifier is not None and result.verifier.success

    scores = FakeScorer().score(EventView(store.read(records[0].run_id), "omniscient"),
                                ScorerContext(run_id=records[0].run_id, task_id="press_it"))
    assert scores[0].value > 0


def test_no_concrete_adapter_is_reachable_from_the_kernel():
    """Importing the kernel must not drag an adapter, plug-in or pack into memory.

    Checked in a subprocess: within one pytest session other tests have already
    imported the concrete plug-ins, so an in-process check would only measure
    test ordering.
    """
    import subprocess
    import sys

    script = (
        "import sys, testbed_kernel, testbed_contracts, testbed_pack_sdk;"
        "bad=[m for m in sys.modules if m.startswith(('testbed_adapters',"
        "'testbed_plugins','testbed_packs','testbed_cli'))];"
        "print(bad); sys.exit(1 if bad else 0)"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, f"kernel pulled in concrete plug-ins: {result.stdout.strip()}"
