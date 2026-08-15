"""The raw session runner.

Its whole job is to provision dependencies and hand back agent handles. It
deliberately owns no routing, spawning, timeout resolution, visibility, faults
or retries: World owns those on every execution path, which is what lets a
second runner (an Inspect bridge, say) be compared against this one on the same
normalised event sequence.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from testbed_contracts.manifest import ExperimentManifest
from testbed_contracts.ports import AgentHandle

AgentFactory = Callable[..., AgentHandle]


class RawSessionRunner:
    name = "raw_session"

    def __init__(
        self,
        *,
        adapters: Mapping[str, AgentFactory],
        artifacts: Any = None,
        sandbox: Any = None,
    ) -> None:
        self.adapters = dict(adapters)
        self.artifacts = artifacts
        self.sandbox = sandbox
        self._handles: dict[str, AgentHandle] = {}

    async def provision(self, manifest: ExperimentManifest) -> None:
        if self.sandbox is not None:
            await self.sandbox.start()

    async def agent_handles(self, manifest: ExperimentManifest) -> dict[str, AgentHandle]:
        handles: dict[str, AgentHandle] = {}
        for spec in manifest.agents:
            factory = self.adapters.get(spec.adapter)
            if factory is None:
                raise KeyError(
                    f"agent {spec.agent_id} wants adapter {spec.adapter!r}, "
                    f"which is not registered ({sorted(self.adapters)})"
                )
            handles[spec.agent_id] = factory(spec, artifacts=self.artifacts)
        self._handles = handles
        return handles

    async def teardown(self) -> None:
        if self.sandbox is not None:
            await self.sandbox.stop()


def build(*, adapters: Mapping[str, AgentFactory], artifacts: Any = None, sandbox: Any = None):
    return RawSessionRunner(adapters=adapters, artifacts=artifacts, sandbox=sandbox)


RUNNER = build
