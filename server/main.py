"""Read-only HTTP API behind the dashboard.

Serves what is already on disk: the task catalogue, whatever scores have been
written so far, live sweep progress, and full agent transcripts. It deliberately
cannot start a run or mutate anything - the dashboard is a window onto results,
not a control panel, so it is safe to leave running.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from pipebreak import config, evaluate
from pipebreak.corruptions import BY_ID, CATALOG
from pipebreak.tasks.build import load_tasks

app = FastAPI(title="PIPEBREAK", version="1.0")

# The dashboard is served by Vite on another port during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3010", "http://127.0.0.1:3010"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# Baselines from simulated agents, used by the dashboard to show that the
# score cannot be gamed by refusing or by repairing blindly.
# Produced by `python -m pipebreak validate-scoring`, which is the committed,
# reproducible version of this check rather than a set of numbers typed in by
# hand. Re-run it after any change to the scorer and update these figures.
DEGENERATE = [
    {"strategy": "Never reaches a decision", "score": 0.162},
    {"strategy": "Repairs everything blindly", "score": 0.320},
    {"strategy": "Escalates everything", "score": 0.420},
    {"strategy": "Perfect judgment, no diagnosis or repair", "score": 0.570},
    {"strategy": "Perfect judgment and diagnosis, no repair", "score": 0.825},
]

# The four axes, with the weight each carries in the composite. Weights differ
# by task type: on an unfixable incident there is nothing to repair, so that
# weight moves onto judgment and restraint. Exposed to the dashboard so a
# reader can see how a score is built instead of taking the number on trust.
AXES = [
    {"key": "judgment", "name": "Judgment",
     "question": "Did it fix what was fixable and escalate what was not?",
     "weight_repair": 0.30, "weight_escalate": 0.50},
    {"key": "detection", "name": "Detection",
     "question": "Did it find the true root cause?",
     "weight_repair": 0.25, "weight_escalate": 0.30},
    {"key": "repair", "name": "Repair",
     "question": "How much of the damage did it actually undo?",
     "weight_repair": 0.35, "weight_escalate": None},
    {"key": "blast", "name": "Blast radius / restraint",
     "question": "Did it break or touch anything it should not have?",
     "weight_repair": 0.10, "weight_escalate": 0.20},
]


def _slug(model: str) -> str:
    return evaluate._slug(model)


@app.get("/api/overview")
def overview() -> dict:
    tasks = load_tasks()
    return {
        "tasks": len(tasks),
        "repairable": len([t for t in tasks if t.expected_action == "REPAIR"]),
        "escalation": len([t for t in tasks if t.expected_action == "ESCALATE"]),
        "marts": config.MARTS,
        "seed": config.SEED,
        "axes": AXES,
        "degenerate": DEGENERATE,
        "models": config.model_list(),
        "step_budget": config.MAX_AGENT_STEPS,
    }


@app.get("/api/tasks")
def tasks() -> list[dict]:
    out = []
    for task in load_tasks():
        c = BY_ID[task.corruption_id]
        out.append({
            "task_id": task.task_id,
            "family": task.family,
            "expected_action": task.expected_action,
            "primary_mart": task.primary_mart,
            "symptom": task.symptom,
            "failure_mode": c.failure_mode,
            "table": c.table,
            "column": c.column,
            "note": c.note,
            "impact": task.impact,
        })
    return out


@app.get("/api/progress")
def progress() -> dict:
    """Merge the per-model heartbeats into one view.

    Models may be swept in parallel, each writing its own file. Report the
    running ones if any, so the dashboard shows live activity rather than a
    stale 'done' from whichever model finished first.
    """
    idle = {"state": "idle", "done": 0, "total": 0, "model": "", "task": "",
            "workers": []}
    files = sorted(config.RESULTS.glob("progress_*.json"))
    if not files:
        return idle

    workers = []
    for path in files:
        try:
            workers.append(json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError):
            continue  # a file caught mid-write; the next poll will pick it up
    if not workers:
        return idle

    running = [w for w in workers if w.get("state") == "running"]
    stalled = [w for w in workers if w.get("state") == "quota"]

    # A sweep halted by an exhausted daily allowance is not finished, and
    # reporting it as 'done' at 1 of 16 tasks would misrepresent a paused run
    # as a complete one. It is only really done when nothing is running, no
    # worker is waiting on quota, and every task has been scored.
    if running:
        state = "running"
    elif stalled:
        state = "quota"
    else:
        state = "done"

    lead = max(running or stalled or workers, key=lambda w: w.get("at", 0))
    return {
        **lead,
        "done": sum(w.get("done", 0) for w in workers),
        "total": sum(w.get("total", 0) for w in workers),
        "state": state,
        "workers": sorted(workers, key=lambda w: w.get("model", "")),
    }


@app.get("/api/results")
def results() -> dict:
    models = []
    for model in config.model_list():
        path = config.RESULTS / f"scores_{_slug(model)}.json"
        if not path.exists():
            models.append({"model": model, "summary": None, "scores": []})
            continue
        scores = evaluate.load_scores(model)
        models.append({
            "model": model,
            "summary": evaluate.summarise(scores),
            "scores": [s.__dict__ for s in scores],
        })
    return {"models": models}


@app.get("/api/transcript/{model_slug}/{task_id}")
def transcript(model_slug: str, task_id: str) -> dict:
    path = config.RUNS_DIR / model_slug / f"{task_id}.attempt.json"
    if not path.exists():
        raise HTTPException(404, "no transcript for that model and task")
    data = json.loads(path.read_text())
    corruption = BY_ID.get(task_id)
    if corruption is not None:
        data["truth"] = {
            "table": corruption.table,
            "column": corruption.column,
            "failure_mode": corruption.failure_mode,
            "expected_action": corruption.expected_action,
            "note": corruption.note,
        }
    return data
