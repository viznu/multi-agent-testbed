"""Where task items come from.

Three sources produce the same normalised `Item`, so everything downstream --
prompt assembly, verification, scoring, events -- is identical regardless of
where the data originated. Only loading differs, which is what lets the whole
pack be tested offline while the `lm_eval` path supplies real datasets.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parent / "fixtures"

#: Sources this pack understands.
SOURCES = ("fixture", "jsonl", "lm_eval")


@dataclass(frozen=True)
class Item:
    """One materialised task item."""

    item_id: str
    input: str
    target: str
    choices: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


class LmEvalNotInstalled(RuntimeError):
    """Raised when the `lm_eval` source is requested without the dependency.

    The pack itself works without lm-evaluation-harness -- the fixture and jsonl
    sources need nothing extra -- so its catalog record declares no required
    import. This error is where the real dependency is enforced, precisely and
    with the command that fixes it.
    """

    def __init__(self, detail: str = "") -> None:
        super().__init__(
            "the 'lm_eval' source needs lm-evaluation-harness: "
            "pip install 'multi-agent-testbed[lm-eval]'" + (f"\n  {detail}" if detail else "")
        )


def _from_rows(rows: Sequence[Mapping[str, Any]], *, prefix: str) -> list[Item]:
    items: list[Item] = []
    for index, row in enumerate(rows):
        items.append(
            Item(
                item_id=str(row.get("id", f"{prefix}_{index:04d}")),
                input=str(row["input"]),
                target=str(row["target"]),
                choices=tuple(str(c) for c in row.get("choices", ())),
                metadata=dict(row.get("metadata", {})),
            )
        )
    return items


def load_jsonl(path: Path, *, prefix: str = "item") -> list[Item]:
    """Read materialised items from a JSONL file.

    This is also the interchange format: an item exported from any harness can
    be replayed here without that harness installed, which keeps a stored
    experiment runnable after its upstream dependency has moved on.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"no task file at {path}")
    rows = [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]
    return _from_rows(rows, prefix=prefix)


def load_fixture(name: str) -> list[Item]:
    """Load one of the synthetic fixtures shipped with this pack."""
    path = FIXTURES / f"{name}.jsonl"
    if not path.exists():
        available = sorted(p.stem for p in FIXTURES.glob("*.jsonl"))
        raise FileNotFoundError(f"no fixture {name!r}; available: {available}")
    return load_jsonl(path, prefix=name)


def load_lm_eval(task: str, *, split: str = "test", limit: int | None = None) -> list[Item]:
    """Materialise items from an installed lm-evaluation-harness task.

    Only the *data* is taken: the document text, the target, and the choice list
    for multiple-choice tasks. Execution stays here, so World remains the only
    scheduler and the events of an lm-eval-sourced run are the same events as
    any other run.

    Upstream's Python API has changed shape across releases, so this probes for
    the entry points it knows and fails with the version it found rather than
    silently returning nothing.
    """
    try:
        import lm_eval  # noqa: F401
        from lm_eval.tasks import TaskManager, get_task_dict
    except ImportError as exc:  # pragma: no cover - depends on the extra
        raise LmEvalNotInstalled(str(exc)) from exc

    try:  # pragma: no cover - depends on the extra
        task_dict = get_task_dict([task], TaskManager())
        resolved = task_dict[task]
        docs = _select_docs(resolved, split)
        rows = []
        for index, doc in enumerate(docs):
            if limit is not None and index >= limit:
                break
            rows.append(
                {
                    "id": f"{task}_{index:04d}",
                    "input": resolved.doc_to_text(doc),
                    "target": str(resolved.doc_to_target(doc)),
                    "choices": _doc_choices(resolved, doc),
                    "metadata": {"task": task, "split": split},
                }
            )
    except LmEvalNotInstalled:  # pragma: no cover
        raise
    except Exception as exc:  # pragma: no cover - depends on the extra
        import lm_eval as installed

        version = getattr(installed, "__version__", "unknown")
        raise RuntimeError(
            f"could not materialise lm-eval task {task!r} with lm_eval=={version}: {exc}. "
            "The upstream Python API has changed across releases; pin a supported "
            "revision or export the items to JSONL and use source: jsonl."
        ) from exc
    return _from_rows(rows, prefix=task)


def _select_docs(task: Any, split: str) -> Iterator[Any]:  # pragma: no cover - needs the extra
    for attribute in (f"{split}_docs", "test_docs", "validation_docs", "training_docs"):
        getter = getattr(task, attribute, None)
        if getter is None:
            continue
        try:
            docs = getter()
        except Exception:
            continue
        if docs is not None:
            return iter(docs)
    raise RuntimeError(f"task exposes no documents for split {split!r}")


def _doc_choices(task: Any, doc: Any) -> tuple[str, ...]:  # pragma: no cover - needs the extra
    getter = getattr(task, "doc_to_choice", None)
    if getter is None:
        return ()
    try:
        return tuple(str(c) for c in getter(doc))
    except Exception:
        return ()


def load_items(config: Mapping[str, Any]) -> list[Item]:
    """Dispatch on `source`."""
    source = str(config.get("source", "fixture"))
    if source not in SOURCES:
        raise ValueError(f"unknown source {source!r}; expected one of {SOURCES}")
    limit = config.get("limit")
    limit = int(limit) if limit is not None else None

    if source == "fixture":
        items = load_fixture(str(config.get("fixture", "synthetic_arithmetic")))
    elif source == "jsonl":
        path = config.get("path")
        if not path:
            raise ValueError("source: jsonl needs a 'path'")
        items = load_jsonl(Path(path))
    else:
        task = config.get("task")
        if not task:
            raise ValueError("source: lm_eval needs a 'task', for example task: gsm8k")
        return load_lm_eval(str(task), split=str(config.get("split", "test")), limit=limit)

    return items[:limit] if limit is not None else items
