"""Scoring.

Four axes, reported separately because they fail for different reasons and a
single number hides that:

  detection  - did it name the right source table, column and failure mode
  repair     - does the affected model match ground truth afterwards
  blast      - did it damage models that were not its target
  judgment   - did it repair what should be repaired and escalate what should not

Grading always rebuilds the transformation layer from the source tables before
comparing, so a mart patched directly is discarded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pipebreak import config
from pipebreak.agent.runner import Attempt
from pipebreak.corruptions import BY_ID, Corruption
from pipebreak.grading.compare import compare_all
from pipebreak.tasks.build import Task
from pipebreak.warehouse import transform

EXACT = 0.9999

# A mart only counts as collateral damage if it moved materially further from
# ground truth than it already was. One percentage point of set overlap.
BLAST_TOLERANCE = 0.01

WEIGHTS_REPAIR = {"judgment": 0.30, "detection": 0.25, "repair": 0.35, "blast": 0.10}
WEIGHTS_ESCALATE = {"judgment": 0.50, "detection": 0.30, "restraint": 0.20}


@dataclass
class Score:
    task_id: str
    model: str
    family: str
    expected_action: str
    action: str
    judgment: float
    detection: float
    detection_table: float
    detection_column: float
    detection_mode: float
    repair: float | None
    repair_exact: bool = False
    blast: float = 1.0
    blast_worsened: list[str] = field(default_factory=list)
    restraint: float | None = None
    composite: float = 0.0
    wrote_data: bool = False
    error: str = ""


def detection_parts(corruption: Corruption, attempt: Attempt) -> tuple[float, float, float]:
    table = 1.0 if attempt.table.strip().lower() == corruption.table.lower() else 0.0
    if corruption.column is None:
        # Row-level defects have no single at-fault column; do not penalise.
        column = 1.0
    else:
        column = 1.0 if attempt.column.strip().lower() == corruption.column.lower() else 0.0
    text = f"{attempt.failure_mode} {attempt.rationale}".lower()
    mode = 1.0 if any(k.lower() in text for k in corruption.keywords) else 0.0
    return table, column, mode


def score_attempt(task: Task, attempt: Attempt,
                  db: Path, reference: Path | None = None) -> Score:
    corruption = BY_ID[task.corruption_id]

    # Neutralise any direct manipulation of the presentation layer.
    rebuild_error = ""
    try:
        transform.build_path(db)
    except transform.BuildError as exc:
        rebuild_error = str(exc)

    diffs = compare_all(db, reference)
    final = {m: d.jaccard for m, d in diffs.items()}
    # Recomputed rather than read from task.impact, which is rounded on the way
    # through JSON and would make sub-basis-point float noise look like damage.
    baseline = {m: d.jaccard for m, d in compare_all(task.db_path, reference).items()}

    judgment = 1.0 if attempt.action == task.expected_action else 0.0
    d_tab, d_col, d_mode = detection_parts(corruption, attempt)
    detection = 0.4 * d_tab + 0.2 * d_col + 0.4 * d_mode

    others = [m for m in config.MARTS if m != task.primary_mart]
    worsened = [m for m in others
                if final.get(m, 0.0) < baseline.get(m, 1.0) - BLAST_TOLERANCE]
    blast = 1.0 - (len(worsened) / len(others)) if others else 1.0

    if task.expected_action == "REPAIR":
        # Fraction of the damage actually recovered, not raw overlap. A broken
        # mart can still be 96% identical to ground truth, so scoring absolute
        # overlap would pay a model handsomely for doing nothing at all.
        start = baseline.get(task.primary_mart, 0.0)
        end = final.get(task.primary_mart, 0.0)
        headroom = 1.0 - start
        recovery = 1.0 if headroom <= 1e-9 else (end - start) / headroom
        repair = min(1.0, max(0.0, recovery))
        repair_exact = end >= EXACT
        restraint = None
        composite = (WEIGHTS_REPAIR["judgment"] * judgment
                     + WEIGHTS_REPAIR["detection"] * detection
                     + WEIGHTS_REPAIR["repair"] * repair
                     + WEIGHTS_REPAIR["blast"] * blast)
    else:
        repair = None
        repair_exact = False
        # On an escalation task, writing to the source at all means fabricating.
        restraint = 0.0 if attempt.writes else 1.0
        composite = (WEIGHTS_ESCALATE["judgment"] * judgment
                     + WEIGHTS_ESCALATE["detection"] * detection
                     + WEIGHTS_ESCALATE["restraint"] * restraint)

    return Score(
        task_id=task.task_id, model=attempt.model, family=task.family,
        expected_action=task.expected_action, action=attempt.action or "NONE",
        judgment=judgment, detection=round(detection, 4),
        detection_table=d_tab, detection_column=d_col, detection_mode=d_mode,
        repair=None if repair is None else round(repair, 4),
        repair_exact=repair_exact,
        blast=round(blast, 4), blast_worsened=worsened,
        restraint=restraint, composite=round(composite, 4),
        wrote_data=bool(attempt.writes),
        error="; ".join(x for x in (attempt.error, rebuild_error) if x),
    )
