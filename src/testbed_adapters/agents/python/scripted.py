"""A deterministic in-process agent.

It exists so the contracts can be exercised end to end without a model: the
whole vertical-slice gate runs on it, which is what makes seeded runs
bit-exact. Its behaviour is a short list of steps declared in the manifest.

Placeholders available in step content:

    {agent_id}          this agent's id
    {task}              the instruction it was given
    {inbox}             the content of the message that triggered this turn
    {private.KEY}       a private fact from the information partition
    {obs.KEY}           a key of the observable world state
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping, Sequence
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


def _render(template: str, request: AgentRequest) -> str:
    """Substitute placeholders. Unknown keys render as an empty string rather
    than raising, so a missing private fact is visible in the transcript instead
    of crashing the run as an infrastructure failure."""
    out = template.replace("{agent_id}", request.agent_id)
    out = out.replace("{task}", request.instruction)
    inbox = request.inbox[0]["content"] if request.inbox else ""
    out = out.replace("{inbox}", str(inbox))
    for key, value in request.private_facts.items():
        out = out.replace(f"{{private.{key}}}", str(value))
    for key, value in request.observation.items():
        out = out.replace(f"{{obs.{key}}}", str(value))
    while "{private." in out:
        start = out.index("{private.")
        end = out.index("}", start)
        out = out[:start] + out[end + 1 :]
    while "{obs." in out:
        start = out.index("{obs.")
        end = out.index("}", start)
        out = out[:start] + out[end + 1 :]
    return out


def _render_any(value: Any, request: AgentRequest) -> Any:
    if isinstance(value, str):
        return _render(value, request)
    if isinstance(value, Mapping):
        return {k: _render_any(v, request) for k, v in value.items()}
    if isinstance(value, list):
        return [_render_any(v, request) for v in value]
    return value


class ScriptedAgent:
    """Emits one scripted step per invocation.

    Steps are consumed in order. Once the script is exhausted the agent emits a
    single `final` event and then stays silent, which lets a session reach
    quiescence instead of looping forever.
    """

    adapter = "scripted"

    def __init__(
        self,
        *,
        agent_id: str,
        model: str = "fake/deterministic",
        role: str = "worker",
        steps: Sequence[Mapping[str, Any]] = (),
        artifacts: Any = None,
        cost_per_step: float = 0.0,
    ) -> None:
        self.agent_id = agent_id
        self.model = model
        self.role = role
        self.steps = [list(s) if isinstance(s, list) else dict(s) for s in steps]
        self.artifacts = artifacts
        self.cost_per_step = cost_per_step
        self.cursor = 0
        self.cancelled: set[str] = set()

    async def describe(self) -> AgentDescriptor:
        return AgentDescriptor(
            agent_id=self.agent_id,
            adapter=self.adapter,
            model=self.model,
            role=self.role,
            can_snapshot=self.artifacts is not None,
            reproducibility=ReproducibilityLevel.BIT_EXACT,
        )

    async def invoke(self, request: AgentRequest) -> AsyncIterator[AgentEvent]:
        """One entry of `steps` is one turn.

        A turn may be a single emission or a list of emissions, which is how a
        solo agent does several things (call a tool, then submit) without
        needing a second message to wake it up.
        """
        if self.cursor >= len(self.steps):
            yield AgentEvent(
                kind=AgentEventKind.FINAL,
                content="script exhausted",
                resource_delta=ResourceDelta(model_calls=1, output_tokens=4),
            )
            return
        turn = self.steps[self.cursor]
        self.cursor += 1
        emissions = turn if isinstance(turn, list) else [turn]
        for step in emissions:
            for _ in range(int(step.get("repeat", 1))):
                yield self._build(step, request)

    def _build(self, step: Mapping[str, Any], request: AgentRequest) -> AgentEvent:
        kind = AgentEventKind(str(step.get("kind", "message")))
        content = _render(str(step.get("content", "")), request)
        delta = ResourceDelta(
            model_calls=int(step.get("model_calls", 1)),
            input_tokens=len(request.instruction.split()),
            output_tokens=len(content.split()),
            tool_calls=1 if kind is AgentEventKind.TOOL_CALL else 0,
            cost_usd=self.cost_per_step,
        )
        return AgentEvent(
            kind=kind,
            content=content,
            recipients=tuple(step.get("recipients", ())),
            action=_render_any(dict(step.get("action", {})), request),
            tool_name=step.get("tool"),
            tool_args=_render_any(dict(step.get("tool_args", {})), request),
            resource_delta=delta,
            private=bool(step.get("private", False)),
        )

    async def cancel(self, invocation_id: str) -> None:
        self.cancelled.add(invocation_id)

    async def health(self) -> Health:
        return Health(ok=True, detail=f"step {self.cursor}/{len(self.steps)}")

    async def snapshot(self) -> BlobRef | None:
        if self.artifacts is None:
            return None
        body = json.dumps({"cursor": self.cursor}, sort_keys=True).encode()
        return self.artifacts.put(body, media_type="application/json")

    async def restore(self, snapshot: BlobRef) -> None:
        if self.artifacts is None:
            return
        self.cursor = int(json.loads(self.artifacts.get(snapshot))["cursor"])


def build(spec: Any, *, artifacts: Any = None) -> ScriptedAgent:
    """Factory used by the composition root."""
    config = dict(spec.config)
    return ScriptedAgent(
        agent_id=spec.agent_id,
        model=spec.model,
        role=spec.role,
        steps=config.get("steps", ()),
        artifacts=artifacts,
        cost_per_step=float(config.get("cost_per_step", 0.0)),
    )


ADAPTER = build
