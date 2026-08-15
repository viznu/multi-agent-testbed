"""Contract conformance for agent adapters.

Every adapter, whatever it wraps, must satisfy the same small port and be
honest about whether it can be snapshotted. An adapter that cannot snapshot is
`playback_only`: its transcript replays, but a fresh generation is not claimed
to be reproducible.
"""

from __future__ import annotations

import sys
from pathlib import Path

import anyio
import pytest

from testbed_adapters.agents.cli.adapter import build as build_cli
from testbed_adapters.agents.python.scripted import build as build_scripted
from testbed_contracts.enums import AgentEventKind, ReproducibilityLevel
from testbed_contracts.manifest import AgentSpec
from testbed_contracts.ports import AgentHandle, AgentRequest
from testbed_store import LocalArtifactStore

CLI_AGENT_SCRIPT = '''
import json, sys
request = json.load(sys.stdin)
print(json.dumps({"kind": "message", "content": "hello from " + request["agent_id"]}))
print(json.dumps({"kind": "world_action", "action": {"kind": "submit", "answer": "AC-40921"}}))
'''


def _request(agent_id: str = "a1") -> AgentRequest:
    return AgentRequest(
        invocation_id="inv_test",
        run_id="run_test",
        agent_id=agent_id,
        logical_time=1,
        instruction="do the thing",
        private_facts={"code": "AC-40921"},
    )


def _scripted(tmp_path: Path):
    spec = AgentSpec(
        agent_id="a1",
        adapter="scripted",
        config={
            "steps": [
                {"kind": "message", "content": "I know {private.code}"},
                {"kind": "world_action", "action": {"kind": "submit", "answer": "{private.code}"}},
            ]
        },
    )
    return build_scripted(spec, artifacts=LocalArtifactStore(tmp_path / "artifacts"))


def _cli(tmp_path: Path):
    script = tmp_path / "agent.py"
    script.write_text(CLI_AGENT_SCRIPT, "utf-8")
    spec = AgentSpec(
        agent_id="a1", adapter="cli", config={"command": [sys.executable, str(script)]}
    )
    return build_cli(spec)


@pytest.fixture(params=["scripted", "cli"])
def handle(request, tmp_path: Path):
    return _scripted(tmp_path) if request.param == "scripted" else _cli(tmp_path)


def test_adapter_satisfies_the_agent_port(handle):
    assert isinstance(handle, AgentHandle)


def test_describe_reports_snapshot_capability_honestly(handle):
    descriptor = anyio.run(handle.describe)
    assert descriptor.agent_id == "a1"
    if not descriptor.can_snapshot:
        # A playback-only adapter must not also claim bit-exact reproducibility.
        assert descriptor.reproducibility is not ReproducibilityLevel.BIT_EXACT
        assert anyio.run(handle.snapshot) is None


def test_invoke_yields_normalised_agent_events(handle):
    async def collect():
        return [event async for event in handle.invoke(_request())]

    events = anyio.run(collect)
    assert events, "an invocation must emit at least one event"
    assert all(e.kind in set(AgentEventKind) for e in events)


def test_health_is_answerable(handle):
    assert anyio.run(handle.health).ok is not None


def test_scripted_agent_snapshots_and_restores(tmp_path: Path):
    agent = _scripted(tmp_path)

    async def go():
        async for _ in agent.invoke(_request()):
            pass
        ref = await agent.snapshot()
        cursor_after_first = agent.cursor
        async for _ in agent.invoke(_request()):
            pass
        assert agent.cursor == cursor_after_first + 1
        await agent.restore(ref)
        return agent.cursor, cursor_after_first

    restored, expected = anyio.run(go)
    assert restored == expected


def test_private_facts_are_substituted_but_never_invented(tmp_path: Path):
    agent = _scripted(tmp_path)

    async def go():
        return [e async for e in agent.invoke(_request())]

    events = anyio.run(go)
    assert "AC-40921" in events[0].content

    agent2 = _scripted(tmp_path)

    async def go2():
        request = _request().model_copy(update={"private_facts": {}})
        return [e async for e in agent2.invoke(request)]

    # A missing fact renders empty rather than crashing the run as infra failure.
    assert "AC-40921" not in anyio.run(go2)[0].content


def test_cli_agent_refuses_to_pretend_it_can_restore(tmp_path: Path):
    from testbed_contracts.ports import BlobRef

    agent = _cli(tmp_path)
    with pytest.raises(NotImplementedError):
        anyio.run(agent.restore, BlobRef(hash="sha256:deadbeef"))
