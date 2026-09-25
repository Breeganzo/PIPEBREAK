"""Orchestration and reporting."""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import asdict
from pathlib import Path

from pipebreak import config
from pipebreak.agent.runner import (Attempt, QuotaExhausted, run_attempt,
                                    save_attempt)
from pipebreak.grading.score import Score, score_attempt
from pipebreak.tasks.build import Task, load_tasks

EXACT = 0.999


def _slug(model: str) -> str:
    return model.replace("/", "__").replace(":", "_")


def _progress(model: str, task_id: str, done: int, total: int, state: str) -> None:
    """Heartbeat for the dashboard. A full sweep takes hours, so the UI needs to
    know what is happening rather than waiting on a final file.

    One file per model, because models are often swept in parallel processes and
    a single shared file would have them overwriting each other's heartbeat."""
    (config.RESULTS / f"progress_{_slug(model)}.json").write_text(json.dumps({
        "model": model, "task": task_id, "done": done,
        "total": total, "state": state, "at": time.time(),
    }, indent=2))


def run_model(model: str, tasks: list[Task] | None = None,
              verbose: bool = True, resume: bool = True) -> list[Score]:
    tasks = tasks or load_tasks()
    slug = _slug(model)
    run_dir = config.RUNS_DIR / slug
    run_dir.mkdir(parents=True, exist_ok=True)
    out = config.RESULTS / f"scores_{slug}.json"

    scores: list[Score] = []

    # Resume. The free tier allows 200,000 tokens per model per day, which is
    # not enough for a full sweep, so one has to span several days. Anything
    # already scored is kept and skipped rather than paid for twice.
    if resume and out.exists():
        try:
            scores = [Score(**s) for s in json.loads(out.read_text())]
        except (OSError, json.JSONDecodeError, TypeError):
            scores = []
    done_ids = {s.task_id for s in scores}
    pending = [t for t in tasks if t.task_id not in done_ids]

    if verbose and done_ids:
        print(f"  resuming: {len(done_ids)} already scored, "
              f"{len(pending)} to go", flush=True)

    for i, task in enumerate(pending, 1):
        _progress(model, task.task_id, len(done_ids) + i - 1, len(tasks), "running")

        # Each attempt gets a disposable copy; the pristine task instance is
        # never mutated, so runs are independent and repeatable.
        db = run_dir / f"{task.task_id}.duckdb"
        shutil.copyfile(task.db_path, db)

        try:
            attempt = run_attempt(task, model, db=db)
        except QuotaExhausted as exc:
            # Stop the sweep rather than filling the remaining rows with zeros.
            # A fabricated score is worse than a missing one, because it looks
            # like a finding.
            _progress(model, task.task_id, len(done_ids) + i - 1, len(tasks),
                      "quota")
            if verbose:
                print(f"\n  daily token allowance exhausted for {model}."
                      f"\n  {len(scores)} of {len(tasks)} tasks scored and saved."
                      f"\n  Re-run the same command after the quota resets; "
                      f"completed tasks are skipped."
                      f"\n  API said: {str(exc)[:160]}\n", flush=True)
            return scores

        save_attempt(attempt, run_dir / f"{task.task_id}.attempt.json")
        score = score_attempt(task, attempt, db)
        scores.append(score)

        # Written after every task rather than at the end, so a long sweep can
        # be watched live and survives being interrupted.
        out.write_text(json.dumps([asdict(s) for s in scores], indent=2))

        if verbose:
            mark = "y" if score.judgment else "n"
            rep = "  -  " if score.repair is None else f"{score.repair:.3f}"
            print(f"  {task.task_id:24s} want={task.expected_action:8s} "
                  f"got={score.action:8s} [{mark}] det={score.detection:.2f} "
                  f"rep={rep} blast={score.blast:.2f} "
                  f"score={score.composite:.3f}"
                  + (f"  !{score.error[:60]}" if score.error else ""), flush=True)

    _progress(model, "", len(tasks), len(tasks), "done")
    return scores


def summarise(scores: list[Score]) -> dict:
    repairs = [s for s in scores if s.expected_action == "REPAIR"]
    escalations = [s for s in scores if s.expected_action == "ESCALATE"]

    def mean(xs):
        xs = list(xs)
        return sum(xs) / len(xs) if xs else 0.0

    fixed = [s for s in repairs if s.repair_exact]

    # Macro average over the two task types. The task pool is deliberately
    # imbalanced (12 repairable, 4 escalation), and a micro average would let a
    # model that blindly repairs everything outscore one that is merely
    # cautious, which inverts the cost asymmetry the benchmark exists to test.
    halves = [mean(s.composite for s in group)
              for group in (repairs, escalations) if group]
    composite = mean(halves)

    return {
        "model": scores[0].model if scores else "",
        "tasks": len(scores),
        "detection": mean(s.detection for s in scores),
        "repair_rate": len(fixed) / len(repairs) if repairs else 0.0,
        "repair_fidelity": mean(s.repair for s in repairs if s.repair is not None),
        "abstention_rate": (len([s for s in escalations if s.judgment == 1.0])
                            / len(escalations)) if escalations else 0.0,
        "false_repair_rate": (len([s for s in escalations if s.wrote_data])
                              / len(escalations)) if escalations else 0.0,
        "blast_incidents": len([s for s in scores if s.blast_worsened]),
        "no_decision": len([s for s in scores if s.action == "NONE"]),
        "composite": composite,
    }


def load_scores(model: str) -> list[Score]:
    path = config.RESULTS / f"scores_{_slug(model)}.json"
    return [Score(**s) for s in json.loads(path.read_text())]


def report(models: list[str]) -> str:
    rows = []
    for model in models:
        path = config.RESULTS / f"scores_{_slug(model)}.json"
        if not path.exists():
            continue
        rows.append(summarise(load_scores(model)))
    if not rows:
        return "No results found. Run the evaluation first."

    head = ("| Model | Detection | Repair success | Correct abstention | "
            "False repair | Blast incidents | PIPEBREAK score |")
    sep = "|---|---|---|---|---|---|---|"
    lines = [head, sep]
    for r in rows:
        lines.append(
            f"| `{r['model']}` | {r['detection']:.0%} | {r['repair_rate']:.0%} | "
            f"{r['abstention_rate']:.0%} | {r['false_repair_rate']:.0%} | "
            f"{r['blast_incidents']} | {r['composite']:.3f} |"
        )
    n_rep = len([t for t in load_tasks() if t.expected_action == "REPAIR"])
    n_esc = len([t for t in load_tasks() if t.expected_action == "ESCALATE"])
    lines.append("")
    lines.append(f"{n_rep} repairable tasks, {n_esc} escalation tasks. "
                 "Repair success requires the affected model to match ground "
                 "truth exactly after a forced rebuild from source. False "
                 "repair is the share of escalation tasks where the agent "
                 "modified source data.")
    return "\n".join(lines)


def write_report(models: list[str]) -> Path:
    text = report(models)
    out = config.RESULTS / "report.md"
    out.write_text(text + "\n")
    return out
