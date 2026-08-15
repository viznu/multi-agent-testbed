"""Judge providers.

`HashRubricJudge` is a deterministic stand-in used so that judge-scored paths
are exercised in CI without a network call. It is *not* a model and its ratings
carry no semantic validity; it exists to prove that judge scores stay pinned,
reproducible and separable from hard success. A real judge is an adapter that
implements the same tiny interface.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from testbed_contracts.ids import short_hash


@runtime_checkable
class Judge(Protocol):
    model: str
    prompt_digest: str

    def rate(self, transcript: str, *, rubric: str, seed: int) -> float: ...


class HashRubricJudge:
    """Deterministic pseudo-judge. Same transcript in, same rating out."""

    def __init__(self, *, model: str = "fake/hash-rubric", prompt: str = "") -> None:
        self.model = model
        self.prompt = prompt
        self.prompt_digest = short_hash(prompt, 16)
        self.calibration_set_version = "none"

    def rate(self, transcript: str, *, rubric: str, seed: int) -> float:
        digest = short_hash({"t": transcript, "r": rubric, "s": seed}, 8)
        return round(int(digest, 16) % 1001 / 1000, 3)
