"""Task instance construction.

Each task is an independent copy of the pristine warehouse with exactly one
corruption applied and the transformation layer rebuilt on top of it. Tasks are
verified to be non-inert: if a corruption leaves every mart identical to the
reference, there is nothing to detect and the instance is rejected.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import duckdb

from pipebreak import config
from pipebreak.corruptions import CATALOG, Corruption
from pipebreak.grading.compare import compare_all
from pipebreak.warehouse import seed, transform


@dataclass
class Task:
    task_id: str
    corruption_id: str
    family: str
    expected_action: str
    primary_mart: str
    symptom: str
    db: str
    impact: dict[str, float]
    build_error: str = ""

    @property
    def db_path(self) -> Path:
        return Path(self.db)


def build_reference(n_orders: int = 6000) -> None:
    """Seed the pristine warehouse and snapshot every mart."""
    config.ensure_dirs()
    seed.build(n_orders=n_orders)
    transform.build_path(config.CLEAN_DB)
    transform.snapshot_marts(config.CLEAN_DB, config.REFERENCE)


def _apply(db: Path, corruption: Corruption) -> None:
    con = duckdb.connect(str(db))
    try:
        for stmt in corruption.sql:
            con.execute(stmt)
    finally:
        con.close()


def build_task(corruption: Corruption) -> Task:
    db = config.TASKS_DIR / f"{corruption.id}.duckdb"
    seed.copy_clean(db)
    _apply(db, corruption)

    build_error = ""
    try:
        transform.build_path(db)
    except transform.BuildError as exc:
        build_error = str(exc)

    diffs = compare_all(db)
    impact = {m: round(d.jaccard, 4) for m, d in diffs.items()}
    return Task(
        task_id=corruption.id,
        corruption_id=corruption.id,
        family=corruption.family,
        expected_action=corruption.expected_action,
        primary_mart=corruption.primary_mart,
        symptom=corruption.symptom,
        db=db.as_posix(),
        impact=impact,
        build_error=build_error,
    )


def build_all(verbose: bool = True) -> list[Task]:
    config.ensure_dirs()

    # Self-test. If the pristine warehouse does not reproduce its own reference
    # snapshot exactly, every downstream measurement is meaningless - and the
    # failure is silent, because every task then looks maximally corrupted.
    baseline = compare_all(config.CLEAN_DB)
    broken = {m: d for m, d in baseline.items() if not d.exact}
    if broken:
        detail = "; ".join(f"{m}: {d.error or f'jaccard={d.jaccard:.4f}'}"
                           for m, d in broken.items())
        raise RuntimeError(f"reference self-test failed - {detail}")

    tasks: list[Task] = []
    problems: list[str] = []
    for corruption in CATALOG:
        task = build_task(corruption)
        primary = task.impact.get(corruption.primary_mart, 1.0)
        if task.build_error:
            problems.append(f"{corruption.id}: build failed - {task.build_error}")
            flag = "BUILD FAIL"
        elif primary >= 0.9999:
            problems.append(f"{corruption.id}: no observable effect on "
                            f"{corruption.primary_mart}")
            flag = "INERT"
        else:
            flag = "ok"
        tasks.append(task)
        if verbose:
            touched = sum(1 for v in task.impact.values() if v < 0.9999)
            print(f"  {corruption.id:24s} {corruption.expected_action:8s} "
                  f"primary={primary:.4f} marts_touched={touched}/{len(config.MARTS)} "
                  f"{flag}")

    out = config.WORK / "tasks.json"
    out.write_text(json.dumps([asdict(t) for t in tasks], indent=2))
    if problems:
        raise RuntimeError("task construction failed:\n  " + "\n  ".join(problems))
    return tasks


def load_tasks() -> list[Task]:
    raw = json.loads((config.WORK / "tasks.json").read_text())
    return [Task(**t) for t in raw]
