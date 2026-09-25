"""Adversarial validation of the scorer.

A benchmark can be quietly meaningless while producing entirely plausible
numbers. The defence is to attack it with agents whose behaviour is fixed and
known in advance, and check that the ordering it produces is the one the design
demands.

The strategies below never look at the data. They are deliberately degenerate,
and the scorer must rank them the way a domain expert would:

  never-decides    < always-repair < always-escalate < judgment-only
                                                     < oracle-no-repair

Two properties matter more than the exact figures:

  1. Caution must beat recklessness. always-escalate must outrank
     always-repair, because fabricating numbers is worse than wasting time.
  2. Refusing everything must not be a winning strategy. always-escalate must
     stay far below genuine work, or the benchmark simply rewards abstention.

This exercise caught three real defects during development: micro-averaging let
always-repair win, absolute similarity scoring gave a do-nothing agent 0.95,
and a rounded baseline made every task look like collateral damage. It is kept
runnable so that anyone can re-verify the claim rather than take it on trust.

Run with:  python -m pipebreak validate-scoring
"""

from __future__ import annotations

import shutil

from pipebreak import config
from pipebreak.agent.runner import Attempt
from pipebreak.corruptions import BY_ID
from pipebreak.grading.score import score_attempt
from pipebreak.tasks.build import load_tasks

# The ordering the design requires. Checked, not just printed.
EXPECTED_ORDER = [
    "never-decides",
    "always-repair",
    "always-escalate",
    "judgment-only",
    "oracle-no-repair",
]

# always-escalate must stay clearly below an agent that actually diagnoses, or
# the benchmark rewards blanket refusal.
ABSTENTION_CEILING = 0.70


def _attempt(task, strategy: str) -> Attempt:
    """Build a fake attempt for one strategy without touching the warehouse."""
    corruption = BY_ID[task.task_id]
    attempt = Attempt(task_id=task.task_id, model=f"[{strategy}]",
                      expected_action=task.expected_action)

    if strategy == "never-decides":
        return attempt  # no action, no diagnosis

    if strategy == "always-repair":
        attempt.action = "REPAIR"
    elif strategy == "always-escalate":
        attempt.action = "ESCALATE"
    else:
        # Perfect judgment: the oracle strategies know the right verdict but
        # are still measured on whether they diagnose and repair.
        attempt.action = task.expected_action

    if strategy == "oracle-no-repair":
        # Also knows the true root cause, but never fixes anything. This is the
        # upper bound for an agent that diagnoses perfectly and then stops, and
        # it must not reach 1.0.
        attempt.table = corruption.table
        attempt.column = corruption.column or ""
        attempt.failure_mode = " ".join(corruption.keywords)

    attempt.rationale = f"fixed strategy: {strategy}"
    return attempt


def run() -> list[tuple[str, float]]:
    tasks = load_tasks()
    scratch = config.RUNS_DIR / "_validate_scoring"
    scratch.mkdir(parents=True, exist_ok=True)

    results: list[tuple[str, float]] = []
    for strategy in EXPECTED_ORDER:
        per_class: dict[str, list[float]] = {"REPAIR": [], "ESCALATE": []}
        for task in tasks:
            # A disposable copy, so a strategy that "repairs" cannot leak into
            # the next one. None of these write, but the scorer rebuilds from
            # source and that must not touch the pristine task database.
            db = scratch / f"{task.task_id}.duckdb"
            shutil.copyfile(task.db_path, db)
            score = score_attempt(task, _attempt(task, strategy), db)
            per_class[task.expected_action].append(score.composite)

        # Macro average, matching evaluate.summarise: the pool is imbalanced
        # 12:4 and a micro average inverts the cost asymmetry being tested.
        halves = [sum(v) / len(v) for v in per_class.values() if v]
        results.append((strategy, sum(halves) / len(halves)))

    shutil.rmtree(scratch, ignore_errors=True)
    return results


def check() -> list[tuple[str, float]]:
    """Run the strategies and assert the ordering the design requires."""
    results = run()
    scores = [s for _, s in results]

    for (name_a, a), (name_b, b) in zip(results, results[1:]):
        if not a < b:
            raise AssertionError(
                f"scorer ordering violated: {name_a} ({a:.3f}) should score "
                f"below {name_b} ({b:.3f}). The scorer is not measuring what "
                f"it claims to measure."
            )

    escalate = dict(results)["always-escalate"]
    if escalate >= ABSTENTION_CEILING:
        raise AssertionError(
            f"always-escalate scored {escalate:.3f}, at or above the "
            f"{ABSTENTION_CEILING} ceiling. Blanket refusal must not be a "
            f"winning strategy."
        )

    if scores[-1] >= 1.0:
        raise AssertionError(
            f"oracle-no-repair scored {scores[-1]:.3f}. An agent that never "
            f"repairs anything must not achieve a perfect score."
        )

    return results


def report() -> str:
    results = check()
    lines = ["| Fake strategy | Score |", "|---|---|"]
    lines += [f"| {name} | {score:.3f} |" for name, score in results]
    lines.append("")
    lines.append("Ordering holds: caution outranks recklessness, and blanket "
                 "refusal does not win.")
    return "\n".join(lines)
