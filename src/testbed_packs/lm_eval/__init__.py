"""lm-evaluation-harness as a task source.

The pack runs without lm-evaluation-harness installed: the fixture and JSONL
sources need nothing extra, which keeps the whole code path testable offline.
Only `source: lm_eval` requires the `lm-eval` extra, and it says so precisely.
"""

from testbed_packs.lm_eval.pack import PACK, AnswerVerifier, LmEvalTasks, build_pack

__all__ = ["PACK", "AnswerVerifier", "LmEvalTasks", "build_pack"]
