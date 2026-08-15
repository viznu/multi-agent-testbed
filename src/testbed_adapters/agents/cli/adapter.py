"""A CLI agent adapter.

The subprocess receives one JSON request on stdin and writes JSON agent events,
one per line, to stdout. This is the same shape used by most command-line
agents, and it demonstrates that an opaque external process satisfies the agent
port without the kernel learning anything about it.

Such an agent cannot be snapshotted, so it declares `can_snapshot=False` and
`environment_exact` reproducibility. The kernel then treats it as
`playback_only`: its transcript can be replayed, but a fresh generation is not
claimed to be reproducible.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

from testbed_contracts.enums import AgentEventKind, ReproducibilityLevel
from testbed_contracts.events import ResourceDelta
from testbed_contracts.ports import (
    AgentDescriptor,
    AgentEvent,
    AgentRequest,
    BlobRef,
    Health,
)


class CliAgent:
    adapter = "cli"

    def __init__(
        self,
        *,
        agent_id: str,
        command: Sequence[str],
        model: str = "external/cli",
        role: str = "worker",
        timeout: float = 60.0,
    ) -> None:
        self.agent_id = agent_id
        self.command = list(command)
        self.model = model
        self.role = role
        self.timeout = timeout
        self._last_error: str = ""

    async def describe(self) -> AgentDescriptor:
        return AgentDescriptor(
            agent_id=self.agent_id,
            adapter=self.adapter,
            model=self.model,
            role=self.role,
            can_snapshot=False,
            reproducibility=ReproducibilityLevel.ENVIRONMENT_EXACT,
        )

    async def invoke(self, request: AgentRequest) -> AsyncIterator[AgentEvent]:
        payload = json.dumps(
            {
                "invocation_id": request.invocation_id,
                "agent_id": request.agent_id,
                "instruction": request.instruction,
                "inbox": list(request.inbox),
                "observation": request.observation,
                "private_facts": request.private_facts,
                "logical_time": request.logical_time,
                "seed": request.seed,
            }
        ).encode()

        process = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(payload), timeout=self.timeout
            )
        except TimeoutError:
            process.kill()
            self._last_error = "timeout"
            yield AgentEvent(kind=AgentEventKind.ERROR, content="cli agent timed out")
            return

        if process.returncode != 0:
            self._last_error = stderr.decode("utf-8", "replace")[:500]
            yield AgentEvent(
                kind=AgentEventKind.ERROR,
                content=f"cli agent exited {process.returncode}: {self._last_error}",
            )
            return

        for line in stdout.decode("utf-8", "replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw: dict[str, Any] = json.loads(line)
            except json.JSONDecodeError:
                yield AgentEvent(
                    kind=AgentEventKind.ERROR, content=f"cli agent emitted non-JSON: {line[:200]}"
                )
                continue
            yield AgentEvent(
                kind=AgentEventKind(raw.get("kind", "message")),
                content=str(raw.get("content", "")),
                recipients=tuple(raw.get("recipients", ())),
                action=dict(raw.get("action", {})),
                tool_name=raw.get("tool"),
                tool_args=dict(raw.get("tool_args", {})),
                resource_delta=ResourceDelta(**raw.get("resource_delta", {"model_calls": 1})),
                private=bool(raw.get("private", False)),
            )

    async def cancel(self, invocation_id: str) -> None:
        return None

    async def health(self) -> Health:
        return Health(ok=not self._last_error, detail=self._last_error)

    async def snapshot(self) -> BlobRef | None:
        # An opaque external process has no state we can capture honestly.
        return None

    async def restore(self, snapshot: BlobRef) -> None:
        raise NotImplementedError("the CLI agent adapter is playback_only")


def build(spec: Any, *, artifacts: Any = None) -> CliAgent:
    config = dict(spec.config)
    command = config.get("command")
    if not command:
        raise ValueError(f"agent {spec.agent_id}: the cli adapter needs a 'command' in config")
    return CliAgent(
        agent_id=spec.agent_id,
        command=command,
        model=spec.model,
        role=spec.role,
        timeout=float(config.get("timeout", 60.0)),
    )


ADAPTER = build
