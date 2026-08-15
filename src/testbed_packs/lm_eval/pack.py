"""A task pack over lm-evaluation-harness datasets.

The integration direction is deliberate. lm-eval is a *runner* upstream; here it
is a **source of task items and metric conventions only**. World still schedules,
the agent port is still the agent port, and the events an lm-eval-sourced run
emits are the same events any other run emits. That is what makes a static
reasoning item comparable with an agentic one, and what lets the same item be
wrapped in a debate, jury or committee protocol by changing the topology.

What this pack does NOT do:

* It does not reproduce log-likelihood scoring. Agents generate and submit an
  answer, which is a different measurement (see `metrics`).
* It does not claim parity with upstream scoring. `tests/parity/` compares the
  two when `lm_eval` is installed; until that job has run for a given task, any
  agreement is unverified.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from testbed_contracts.enums import EvalSetKind
from testbed_contracts.events import EventView
from testbed_contracts.results import VerifierResult
from testbed_pack_sdk import (
    Pack,
    StateChange,
    TaskCase,
    WorldAction,
    WorldSnapshotView,
)
from testbed_packs.lm_eval.metrics import METRICS, score_answer
from testbed_packs.lm_eval.source import Item, load_items

PACK_NAME = "lm_eval"
PACK_REVISION = "0.1.0"

#: Where the expected answer is kept in world state. The leading underscore
#: keeps it out of `World.visible_state`, so no agent can observe the target it
#: is being asked to produce.
TARGET_KEY = "_target"
CHOICES_KEY = "_choices"

DEFAULT_INSTRUCTION_SUFFIX = (
    "\n\nSubmit your final answer with the `submit` action. Answer only; do not "
    "restate the question."
)


class LmEvalTasks:
    """Hook family 1: task cases materialised from a dataset source."""

    name = PACK_NAME
    revision = PACK_REVISION

    def __init__(
        self,
        items: Sequence[Item] = (),
        *,
        metric: str = "numeric",
        fewshot: Sequence[Item] = (),
        eval_set_kind: EvalSetKind = EvalSetKind.FROZEN_EVAL,
        answer_hint: bool = False,
        task_label: str = "fixture",
    ) -> None:
        self.items = list(items)
        self.fewshot = list(fewshot)
        self.metric = metric
        self.eval_set_kind = eval_set_kind
        self.answer_hint = answer_hint
        self.task_label = task_label

    def _prefix(self) -> str:
        if not self.fewshot:
            return ""
        blocks = [f"Q: {shot.input}\nA: {shot.target}" for shot in self.fewshot]
        return "\n\n".join(blocks) + "\n\n"

    def cases(self) -> Sequence[TaskCase]:
        prefix = self._prefix()
        cases = []
        for item in self.items:
            instruction = f"{prefix}Q: {item.input}" + DEFAULT_INSTRUCTION_SUFFIX
            if item.choices:
                options = "\n".join(
                    f"  {chr(ord('A') + i)}. {choice}" for i, choice in enumerate(item.choices)
                )
                instruction = (
                    f"{prefix}Q: {item.input}\nOptions:\n{options}" + DEFAULT_INSTRUCTION_SUFFIX
                )
            cases.append(
                TaskCase(
                    task_id=item.item_id,
                    instruction=instruction,
                    initial_state={
                        "submitted": None,
                        TARGET_KEY: item.target,
                        CHOICES_KEY: list(item.choices),
                    },
                    public_facts={"answer_format": "a single short answer"},
                    # The wiring-check mode hands the answer over as a private
                    # fact. It measures the plumbing, never capability, and the
                    # metadata says so on every case it produces.
                    private_facts=(
                        {"solver": {"answer": item.target}} if self.answer_hint else {}
                    ),
                    eval_set_kind=self.eval_set_kind,
                    metadata={
                        "metric": self.metric,
                        "task": self.task_label,
                        "num_fewshot": len(self.fewshot),
                        "choices": list(item.choices),
                        "wiring_check_only": self.answer_hint,
                        **dict(item.metadata),
                    },
                )
            )
        return cases


class SubmitHandler:
    """Hook family 3: `submit` records an answer and ends the episode."""

    handles = ("submit",)

    def apply(self, action: WorldAction, state: WorldSnapshotView) -> StateChange:
        answer = str(action.args.get("answer", "")).strip()
        if not answer:
            return StateChange(rejected_reason="empty submission")
        if state.state.get("submitted") is not None:
            return StateChange(rejected_reason="already submitted")
        return StateChange(
            updates={"submitted": answer, "submitted_by": action.actor_id},
            finished=True,
            note="submitted",
        )


class AnswerVerifier:
    """Hook family 4: deterministic answer matching.

    Reads the target from world state rather than from a closure, so one
    verifier serves every item in the pack and the target stays out of every
    agent's view.
    """

    name = "lm_eval_answer"
    version = "0.1.0"

    def __init__(self, metric: str = "numeric") -> None:
        if metric not in METRICS:
            raise ValueError(f"unknown metric {metric!r}; expected one of {METRICS}")
        self.metric = metric

    def verify(self, state: WorldSnapshotView, events: EventView) -> VerifierResult:
        submitted = state.state.get("submitted")
        target = str(state.state.get(TARGET_KEY, ""))
        choices = tuple(str(c) for c in state.state.get(CHOICES_KEY, ()) or ())

        if submitted is None:
            return VerifierResult(
                success=False,
                reward=0.0,
                per_agent_payoff={a: 0.0 for a in state.agent_ids},
                detail={"metric": self.metric, "reason": "no answer submitted"},
            )
        result = score_answer(str(submitted), target, metric=self.metric, choices=choices)
        return VerifierResult(
            success=result.correct,
            reward=1.0 if result.correct else 0.0,
            per_agent_payoff={a: (1.0 if result.correct else 0.0) for a in state.agent_ids},
            detail={"submitted": submitted, **result.detail()},
        )


def configure(pack: Pack, config: Mapping[str, Any]) -> Pack:
    """Shape the pack to one experiment.

    Recognised keys:

    ``source``        ``fixture`` (default), ``jsonl`` or ``lm_eval``
    ``fixture``       fixture name, for ``source: fixture``
    ``path``          JSONL path, for ``source: jsonl``
    ``task``/``split`` upstream task, for ``source: lm_eval``
    ``limit``         maximum evaluation items
    ``metric``        one of ``exact_match``, ``normalized_match``, ``numeric``,
                      ``multiple_choice``
    ``num_fewshot``   items taken from the front of the set as prompt examples;
                      they are removed from the evaluation set, so a few-shot
                      run never scores an item it was shown
    ``eval_set_kind`` defaults to ``frozen_eval``
    ``answer_hint``   wiring-check mode: hand the answer to the agent as a
                      private fact. Measures nothing about capability.
    """
    metric = str(config.get("metric", "numeric"))
    num_fewshot = int(config.get("num_fewshot", 0))
    items = load_items(config)

    if num_fewshot >= len(items):
        raise ValueError(
            f"num_fewshot={num_fewshot} leaves no evaluation items out of {len(items)}"
        )
    fewshot, evaluated = items[:num_fewshot], items[num_fewshot:]

    tasks = LmEvalTasks(
        evaluated,
        metric=metric,
        fewshot=fewshot,
        eval_set_kind=EvalSetKind(str(config.get("eval_set_kind", "frozen_eval"))),
        answer_hint=bool(config.get("answer_hint", False)),
        task_label=str(config.get("task") or config.get("fixture") or "fixture"),
    )
    return pack.model_copy(
        update={"tasks": tasks, "verifiers": {"default": AnswerVerifier(metric)}}
    )


def build_pack() -> Pack:
    return Pack(
        name=PACK_NAME,
        revision=PACK_REVISION,
        tasks=LmEvalTasks(),
        action_handlers=(SubmitHandler(),),
        verifiers={"default": AnswerVerifier()},
        configurator=configure,
        description=(
            "Static reasoning items from lm-evaluation-harness datasets, executed "
            "through World rather than through the upstream runner."
        ),
    )


PACK = build_pack()
