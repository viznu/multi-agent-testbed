"""Answer-matching metrics.

These mirror the *conventions* of lm-evaluation-harness, not its code. Nothing
here is verified to agree with upstream scoring until the parity job in
`tests/parity/` runs with `lm_eval` installed, and until then every catalog
record involved stays `experimental`.

One difference is structural rather than incidental, and matters more than any
normalisation detail: lm-eval scores multiple-choice tasks by comparing the
log-likelihood the model assigns to each choice. This testbed observes an agent
that *generates and submits* an answer. Generative accuracy and log-likelihood
accuracy are different measurements of different things, and a number produced
here must never be compared with a published log-likelihood score.
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass

ARTICLES = re.compile(r"\b(a|an|the)\b", re.IGNORECASE)
WHITESPACE = re.compile(r"\s+")
PUNCTUATION = str.maketrans("", "", string.punctuation)
NUMBER = re.compile(r"-?\d+(?:[\d,]*\d)?(?:\.\d+)?")

#: Metric names this pack understands.
METRICS = ("exact_match", "normalized_match", "numeric", "multiple_choice")


def normalize(text: str) -> str:
    """Lowercase, drop articles and punctuation, collapse whitespace.

    The SQuAD-style normalisation most short-answer metrics use.
    """
    lowered = text.strip().lower()
    without_punctuation = lowered.translate(PUNCTUATION)
    without_articles = ARTICLES.sub(" ", without_punctuation)
    return WHITESPACE.sub(" ", without_articles).strip()


def last_number(text: str) -> str | None:
    """The final number in a string.

    Chain-of-thought answers to arithmetic tasks conventionally end with the
    result, and upstream harnesses extract it the same way. `####` takes
    priority when present, since that is the explicit answer marker.
    """
    if "####" in text:
        text = text.split("####")[-1]
    matches = NUMBER.findall(text)
    if not matches:
        return None
    return matches[-1].replace(",", "")


def _numbers_equal(left: str, right: str) -> bool:
    try:
        return abs(float(left) - float(right)) < 1e-6
    except ValueError:
        return left == right


@dataclass(frozen=True)
class MatchResult:
    correct: bool
    extracted: str | None
    expected: str
    metric: str

    def detail(self) -> dict[str, object]:
        return {
            "metric": self.metric,
            "extracted": self.extracted,
            "expected": self.expected,
            "correct": self.correct,
        }


def score_answer(
    submission: str,
    target: str,
    *,
    metric: str = "exact_match",
    choices: tuple[str, ...] = (),
) -> MatchResult:
    """Judge one submission. Deterministic; no model is involved."""
    if metric not in METRICS:
        raise ValueError(f"unknown metric {metric!r}; expected one of {METRICS}")
    submission = (submission or "").strip()

    if metric == "exact_match":
        return MatchResult(submission == target.strip(), submission, target, metric)

    if metric == "normalized_match":
        return MatchResult(
            normalize(submission) == normalize(target), normalize(submission), target, metric
        )

    if metric == "numeric":
        extracted = last_number(submission)
        expected = last_number(target) or target.strip()
        correct = extracted is not None and _numbers_equal(extracted, expected)
        return MatchResult(correct, extracted, expected, metric)

    # multiple_choice: accept the choice text or its letter, so a scaffold that
    # answers "B" is not marked wrong for formatting.
    resolved = _resolve_choice(submission, choices)
    expected = _resolve_choice(target, choices) or target.strip()
    return MatchResult(resolved is not None and resolved == expected, resolved, expected, metric)


def _resolve_choice(answer: str, choices: tuple[str, ...]) -> str | None:
    answer = (answer or "").strip()
    if not answer:
        return None
    if not choices:
        return normalize(answer)
    normalized = [normalize(c) for c in choices]
    if normalize(answer) in normalized:
        return normalize(answer)
    stripped = answer.strip().strip(".)").upper()
    if len(stripped) == 1 and stripped.isalpha():
        index = ord(stripped) - ord("A")
        if 0 <= index < len(choices):
            return normalized[index]
    # A bare digit is deliberately NOT treated as a choice index. "2" could mean
    # the second or the third option depending on whether the scaffold counts
    # from zero, and silently picking one convention would shift scores without
    # anyone noticing. A digit that is itself a choice still matches above, by
    # text.
    return None
