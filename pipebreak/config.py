"""Paths and global configuration for PIPEBREAK."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ENV_FILE = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"


def load_env(path: Path | None = None) -> None:
    """Read key=value pairs from .env into the environment.

    Hand-rolled rather than pulled from a package: it is fifteen lines, it keeps
    the benchmark dependency-free for anyone reproducing the results, and a real
    environment variable always wins so CI can override the file.
    """
    path = path or ENV_FILE
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        # An untouched placeholder is treated as absent, so it never lands in
        # the environment where another library might read it as a real value.
        if not key or not value or value.startswith("your_"):
            continue
        if not os.environ.get(key):
            os.environ[key] = value


load_env()

DBT_DIR = ROOT / "dbt"
WORK = ROOT / "work"
RESULTS = ROOT / "results"

CLEAN_DB = WORK / "clean.duckdb"
REFERENCE = WORK / "reference"
TASKS_DIR = WORK / "tasks"
RUNS_DIR = WORK / "runs"

SEED = 20260922

# Marts are the observable surface of the warehouse. Repair fidelity is measured
# on the mart the symptom points at; blast radius is measured on all the others.
MARTS = [
    "fct_order_revenue",
    "fct_daily_revenue",
    "dim_product_margin",
    "fct_shipment_performance",
    "mart_monthly_margin",
]

# Groq serves an OpenAI-compatible endpoint, so the official OpenAI client works
# unchanged once the base URL is redirected.
GROQ_BASE_URL = "https://api.groq.com/openai/v1"

# Three tool-calling models spanning two families and a 6x size range. Override
# with PIPEBREAK_MODELS in .env; availability varies by Groq account.
DEFAULT_MODELS = [
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "qwen/qwen3.8-27b",
]

MAX_AGENT_STEPS = 14
AGENT_TEMPERATURE = 0.0

# Groq's free tier imposes two separate ceilings, and they bite differently:
#
#   8,000 tokens per MINUTE - caps a single request as well as throughput. A
#       request larger than this can never be served, however long you wait.
#   200,000 tokens per DAY  - caps how much of a sweep fits in one sitting.
#
# Because a tool-calling agent resends its whole history every step, cost grows
# with the square of the step count. The budget below keeps a single request far
# inside the per-minute cap and keeps a 16-task sweep inside roughly two days of
# the daily one. Sweeps resume, so spanning days is not a problem.
REQUEST_TOKEN_BUDGET = 2600


def api_key() -> str:
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key or key.startswith("your_"):
        raise RuntimeError(
            "GROQ_API_KEY is not set.\n\n"
            f"Put it in {ENV_FILE}:\n"
            "    GROQ_API_KEY=gsk_...\n\n"
            f"Copy {ENV_EXAMPLE.name} to .env if you have not already:\n"
            "    cp .env.example .env\n\n"
            "An exported environment variable also works and takes precedence."
        )
    return key


def model_list() -> list[str]:
    """Models to evaluate, overridable from .env as a comma-separated list."""
    raw = os.environ.get("PIPEBREAK_MODELS", "").strip()
    if not raw:
        return list(DEFAULT_MODELS)
    return [m.strip() for m in raw.split(",") if m.strip()]


def ensure_dirs() -> None:
    for d in (WORK, REFERENCE, TASKS_DIR, RUNS_DIR, RESULTS):
        d.mkdir(parents=True, exist_ok=True)
