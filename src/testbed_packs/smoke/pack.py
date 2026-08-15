"""The smoke pack: tasks, action handlers and deterministic verifiers."""

from __future__ import annotations

from collections.abc import Sequence

from testbed_contracts.events import EventView
from testbed_contracts.results import VerifierResult
from testbed_pack_sdk import (
    Pack,
    StateChange,
    TaskCase,
    WorldAction,
    WorldSnapshotView,
)

PACK_NAME = "smoke"
PACK_REVISION = "1.0.0"

POT = 10.0
"""The pot the mixed-motive task divides. Fixed so payoffs stay comparable."""

SOLO_CODE = "AC-40921"
"""The answer to `solo_lookup`, held privately by the single agent."""

CODE_WORD = "alphaomega"
"""The answer to `coop_codeword`: the two private halves, first then second."""


class SmokeTasks:
    name = PACK_NAME
    revision = PACK_REVISION

    def cases(self) -> Sequence[TaskCase]:
        return (
            TaskCase(
                task_id="solo_lookup",
                instruction="Look up the access code and submit it.",
                initial_state={"submitted": None},
                public_facts={"expected_length": 8},
                private_facts={"solo": {"code": SOLO_CODE}},
                metadata={"shape": "single_agent", "requires_tool": True},
            ),
            TaskCase(
                task_id="coop_codeword",
                instruction=(
                    "Each of you holds half of the code word. Combine them and submit "
                    "the whole word exactly once."
                ),
                initial_state={"submitted": None},
                public_facts={"format": "two halves, first then second"},
                private_facts={
                    "researcher_1": {"share": "alpha"},
                    "researcher_2": {"share": "omega"},
                },
                metadata={
                    "shape": "cooperative",
                    "expected": CODE_WORD,
                    # Neither agent can solve this alone; that is the point of the case.
                    "requires_communication": True,
                },
            ),
            TaskCase(
                task_id="mixed_split",
                instruction=(
                    f"There is a pot of {POT:g}. Claim a share. If the claims together "
                    "exceed the pot, everyone gets nothing."
                ),
                initial_state={"claims": {}},
                public_facts={"pot": POT},
                private_facts={
                    "trader_1": {"private_value": 6.0},
                    "trader_2": {"private_value": 4.0},
                },
                metadata={
                    "shape": "mixed_motive",
                    "hidden_information": True,
                    "individual_and_team_payoff": True,
                },
            ),
        )


class SubmitHandler:
    """`submit` writes the answer and ends the episode."""

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


class ClaimHandler:
    """`claim` records one agent's claim; the episode ends when all have claimed."""

    handles = ("claim",)

    def apply(self, action: WorldAction, state: WorldSnapshotView) -> StateChange:
        try:
            amount = float(action.args.get("amount", 0))
        except (TypeError, ValueError):
            return StateChange(rejected_reason="claim amount is not a number")
        if amount < 0:
            return StateChange(rejected_reason="claim amount is negative")
        claims = dict(state.state.get("claims", {}))
        if action.actor_id in claims:
            return StateChange(rejected_reason="already claimed")
        claims[action.actor_id] = amount
        everyone_claimed = set(claims) >= set(state.agent_ids)
        return StateChange(
            updates={"claims": claims},
            finished=everyone_claimed,
            note="all claims in" if everyone_claimed else "claim recorded",
        )


class NoopHandler:
    """Absorbs any other action so an unknown action is a recorded no-op rather
    than an infrastructure failure."""

    handles = ("noop",)

    def apply(self, action: WorldAction, state: WorldSnapshotView) -> StateChange:
        return StateChange(note="noop")


class SubmissionVerifier:
    """Deterministic: compares the submission with the expected answer.

    The expected answer is a property of the *task case*, not of any agent's
    private facts, so a single-agent baseline that holds all the information is
    judged by exactly the same standard as the two-agent team.
    """

    name = "smoke_submission"
    version = "1.0.0"

    def __init__(self, expected: str) -> None:
        self.expected = expected

    def verify(self, state: WorldSnapshotView, events: EventView) -> VerifierResult:
        submitted = state.state.get("submitted")
        success = submitted is not None and str(submitted) == self.expected
        return VerifierResult(
            success=success,
            reward=1.0 if success else 0.0,
            per_agent_payoff={a: (1.0 if success else 0.0) for a in state.agent_ids},
            detail={"submitted": submitted, "expected": self.expected},
        )


class SplitVerifier:
    """Mixed-motive payoff: individual claims are honoured only if the pot holds.

    Team success and individual payoff are computed separately, so a run where
    one agent grabbed everything is visibly different from an equitable one even
    when both "succeed".
    """

    name = "smoke_split"
    version = "1.0.0"

    def verify(self, state: WorldSnapshotView, events: EventView) -> VerifierResult:
        claims = {k: float(v) for k, v in state.state.get("claims", {}).items()}
        total = sum(claims.values())
        overclaimed = total > POT
        payoffs = (
            {agent: 0.0 for agent in state.agent_ids}
            if overclaimed
            else {agent: claims.get(agent, 0.0) for agent in state.agent_ids}
        )
        everyone_claimed = set(claims) >= set(state.agent_ids)
        success = everyone_claimed and not overclaimed
        return VerifierResult(
            success=success,
            reward=sum(payoffs.values()) / POT if POT else 0.0,
            per_agent_payoff=payoffs,
            constraints_satisfied=not overclaimed,
            detail={
                "claims": claims,
                "total_claimed": total,
                "pot": POT,
                "overclaimed": overclaimed,
                "everyone_claimed": everyone_claimed,
            },
        )


def build_pack() -> Pack:
    return Pack(
        name=PACK_NAME,
        revision=PACK_REVISION,
        tasks=SmokeTasks(),
        action_handlers=(SubmitHandler(), ClaimHandler(), NoopHandler()),
        verifiers={
            "solo_lookup": SubmissionVerifier(SOLO_CODE),
            "coop_codeword": SubmissionVerifier(CODE_WORD),
            "mixed_split": SplitVerifier(),
        },
        description="Three tiny deterministic fixtures for the vertical slice.",
    )


PACK = build_pack()
