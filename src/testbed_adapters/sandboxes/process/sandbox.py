"""A local process sandbox.

This is the honest minimum: a working directory, an environment allowlist, a
timeout and no network helper. It reduces accidents; it is *not* a boundary for
hostile workloads. The plan reserves rootless OCI for trusted fixtures and
gVisor / Kata / microVMs for untrusted ones, and neither ships here.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from collections.abc import Sequence
from pathlib import Path


class ProcessSandbox:
    name = "process"
    #: Stated plainly so no experiment mistakes this for isolation.
    isolation_note = "same-host process; not an isolation boundary for untrusted code"

    def __init__(self, *, env_allowlist: Sequence[str] = ("PATH",), workdir: Path | None = None):
        self.env_allowlist = tuple(env_allowlist)
        self._workdir = workdir
        self._owned_workdir: Path | None = None

    async def start(self) -> None:
        if self._workdir is None:
            self._owned_workdir = Path(tempfile.mkdtemp(prefix="matb-sandbox-"))
            self._workdir = self._owned_workdir
        self._workdir.mkdir(parents=True, exist_ok=True)

    async def exec(self, command: Sequence[str], *, timeout: float) -> tuple[int, str, str]:
        env = {k: os.environ[k] for k in self.env_allowlist if k in os.environ}
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(self._workdir),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError:
            process.kill()
            return (124, "", f"timed out after {timeout}s")
        return (
            process.returncode or 0,
            stdout.decode("utf-8", "replace"),
            stderr.decode("utf-8", "replace"),
        )

    async def stop(self) -> None:
        if self._owned_workdir and self._owned_workdir.exists():
            shutil.rmtree(self._owned_workdir, ignore_errors=True)


SANDBOX = ProcessSandbox
