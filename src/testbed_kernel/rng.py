"""Seeded randomness with recorded, restorable state.

Every non-deterministic choice the scheduler or a topology plugin makes goes
through here, so a checkpoint can restore the exact stream and a resumed attempt
makes the same decisions as an uninterrupted one.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from typing import Any

from testbed_contracts.ids import short_hash


class DeterministicRng:
    """A labelled RNG.

    Draws are labelled so that adding a new call site in unrelated code cannot
    silently shift an existing decision stream: each label keeps its own
    counter, and the value is derived from (seed, label, counter).
    """

    def __init__(self, seed: int, *, counters: dict[str, int] | None = None) -> None:
        self.seed = int(seed)
        self.counters: dict[str, int] = dict(counters or {})
        self.decisions: list[dict[str, Any]] = []

    def _next(self, label: str) -> random.Random:
        index = self.counters.get(label, 0)
        self.counters[label] = index + 1
        stream_seed = int(short_hash({"seed": self.seed, "label": label, "i": index}, 16), 16)
        return random.Random(stream_seed)

    def random(self, label: str) -> float:
        value = self._next(label).random()
        self.decisions.append({"label": label, "kind": "random", "value": value})
        return value

    def randint(self, label: str, low: int, high: int) -> int:
        value = self._next(label).randint(low, high)
        self.decisions.append({"label": label, "kind": "randint", "value": value})
        return value

    def choice(self, label: str, options: Sequence[Any]) -> Any:
        if not options:
            raise ValueError("cannot choose from an empty sequence")
        index = self._next(label).randrange(len(options))
        self.decisions.append({"label": label, "kind": "choice", "value": index})
        return options[index]

    def shuffled(self, label: str, options: Sequence[Any]) -> list[Any]:
        items = list(options)
        self._next(label).shuffle(items)
        self.decisions.append({"label": label, "kind": "shuffle", "value": len(items)})
        return items

    # -- persistence -------------------------------------------------------

    def state(self) -> dict[str, Any]:
        return {"seed": self.seed, "counters": dict(self.counters)}

    @classmethod
    def restore(cls, state: dict[str, Any]) -> DeterministicRng:
        return cls(int(state["seed"]), counters=dict(state.get("counters", {})))
