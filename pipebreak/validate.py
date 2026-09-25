"""Benchmark validity checks.

Two invariants have to hold or the scores mean nothing:

  non-inert   every corruption visibly damages the mart it claims to damage,
              otherwise there is nothing for an agent to find

  solvable    every repairable task can be restored to exact ground truth by a
              reference fix, otherwise the repair axis penalises agents for a
              defect in the benchmark rather than in their reasoning

The reference fixes live in the catalogue and are never shown to the agent.
"""

from __future__ import annotations

import shutil

import duckdb

from pipebreak import config
from pipebreak.corruptions import REPAIRABLE
from pipebreak.grading.compare import compare_all
from pipebreak.tasks.build import load_tasks
from pipebreak.warehouse import transform

WORKDIR = "validate"


def check_solvable(verbose: bool = True) -> list[str]:
    """Apply each reference fix and require an exact restoration."""
    tasks = {t.task_id: t for t in load_tasks()}
    failures: list[str] = []

    for corruption in REPAIRABLE:
        task = tasks.get(corruption.id)
        if task is None:
            failures.append(f"{corruption.id}: no task instance")
            continue
        if not corruption.repair_sql:
            failures.append(f"{corruption.id}: no reference fix defined")
            continue

        db = config.RUNS_DIR / WORKDIR / f"{corruption.id}.duckdb"
        db.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(task.db_path, db)

        con = duckdb.connect(str(db))
        try:
            for stmt in corruption.repair_sql:
                con.execute(stmt)
        finally:
            con.close()

        try:
            transform.build_path(db)
        except transform.BuildError as exc:
            failures.append(f"{corruption.id}: reference fix breaks the build: {exc}")
            continue

        diffs = compare_all(db)
        off = [m for m, d in diffs.items() if not d.exact]
        if verbose:
            detail = "exact" if not off else "NOT EXACT: " + ", ".join(
                f"{m}={diffs[m].jaccard:.4f}" for m in off)
            print(f"  {corruption.id:24s} {detail}")
        if off:
            failures.append(f"{corruption.id}: not restored ({', '.join(off)})")

    return failures


def check_all(verbose: bool = True) -> None:
    if verbose:
        print("verifying every repairable task is solvable")
    failures = check_solvable(verbose)
    if failures:
        raise RuntimeError("benchmark validity check failed:\n  "
                           + "\n  ".join(failures))
    if verbose:
        print(f"  all {len(REPAIRABLE)} repairable tasks restore to ground truth")
