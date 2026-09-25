"""The tool surface exposed to the agent.

Deliberately narrow: enough to investigate a warehouse the way an analytics
engineer would, and nothing more. Two constraints matter for grading integrity:

* ``run_sql`` is read-only, so investigation cannot mutate state by accident.
* ``apply_fix`` may only write to ``raw_*`` tables. Grading always rebuilds the
  transformation layer from raw before comparing, so patching a mart directly
  would be erased. Fixes have to address the source of the problem.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import pandas as pd

from pipebreak import config
from pipebreak.warehouse import transform

# Every step resends the whole message history, so a tool result is not paid for
# once - it is paid for on every subsequent step. Oversized results therefore
# cost quadratically. These caps are the main lever on the price of a run, and
# 15 rows is ample for the comparisons these tasks need (per-carrier,
# per-currency, per-month aggregates).
MAX_ROWS = 10
MAX_CHARS = 900

READ_ONLY_PREFIXES = ("select", "with", "describe", "show", "summarize", "pragma", "explain")
WRITE_TARGET = re.compile(
    r"\b(?:update|insert\s+into|delete\s+from|alter\s+table|create\s+(?:or\s+replace\s+)?"
    r"(?:table|view)|drop\s+table|drop\s+view|truncate)\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)


def _render(df: pd.DataFrame) -> str:
    if df.empty:
        return "(0 rows)"
    truncated = len(df) > MAX_ROWS
    text = df.head(MAX_ROWS).to_string(index=False, max_colwidth=48)
    if truncated:
        text += f"\n... ({len(df)} rows total, {MAX_ROWS} shown)"
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n... (output truncated)"
    return text


@dataclass
class Finish:
    action: str
    table: str
    column: str
    failure_mode: str
    rationale: str


@dataclass
class Toolbox:
    db: Path
    writes: list[str] = field(default_factory=list)
    rebuilds: int = 0
    finished: Finish | None = None

    # ------------------------------------------------------------- read tools
    def list_tables(self) -> str:
        con = duckdb.connect(str(self.db), read_only=True)
        try:
            rows = con.execute(
                "SELECT table_name, table_type FROM information_schema.tables "
                "ORDER BY table_type, table_name"
            ).fetchall()
            out = []
            for name, kind in rows:
                n = con.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
                label = "source table" if name.startswith("raw_") else kind.lower()
                out.append(f"{name:30s} {label:14s} {n:>8,} rows")
            return "\n".join(out)
        finally:
            con.close()

    def describe_table(self, table: str) -> str:
        con = duckdb.connect(str(self.db), read_only=True)
        try:
            cols = con.execute(f"DESCRIBE {table}").df()
            sample = con.execute(f"SELECT * FROM {table} LIMIT 5").df()
            return f"columns:\n{_render(cols)}\n\nsample rows:\n{_render(sample)}"
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"
        finally:
            con.close()

    def run_sql(self, query: str) -> str:
        stripped = query.strip().lstrip("(").lower()
        if not stripped.startswith(READ_ONLY_PREFIXES):
            return ("ERROR: run_sql is read-only. Use apply_fix to modify data.")
        con = duckdb.connect(str(self.db), read_only=True)
        try:
            return _render(con.execute(query).df())
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {str(exc).strip()}"
        finally:
            con.close()

    def read_model(self, model: str) -> str:
        models = transform.load_models()
        if model not in models:
            return (f"ERROR: no model named '{model}'. Available: "
                    f"{', '.join(sorted(models))}")
        m = models[model]
        return (f"-- {m.path.relative_to(config.DBT_DIR)} "
                f"(materialised as {m.materialization})\n{m.sql}")

    # ------------------------------------------------------------ write tools
    def apply_fix(self, sql: str) -> str:
        targets = WRITE_TARGET.findall(sql)
        if not targets:
            return ("ERROR: could not identify a write target. apply_fix expects "
                    "a single UPDATE, INSERT, DELETE or ALTER statement.")
        illegal = [t for t in targets if not t.lower().startswith("raw_")]
        if illegal:
            return (f"ERROR: apply_fix may only modify source tables (raw_*). "
                    f"Refused write to: {', '.join(illegal)}. The transformation "
                    f"layer is rebuilt from source during grading, so fixes must "
                    f"be applied at the source.")
        con = duckdb.connect(str(self.db))
        try:
            con.execute(sql)
            self.writes.append(sql.strip())
            return "OK: statement applied. Call rebuild to refresh the models."
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {str(exc).strip()}"
        finally:
            con.close()

    def rebuild(self) -> str:
        self.rebuilds += 1
        try:
            built = transform.build_path(self.db)
            return f"OK: rebuilt {len(built)} models."
        except transform.BuildError as exc:
            return f"BUILD FAILED: {exc}"

    # --------------------------------------------------------------- terminal
    def finish(self, action: str, table: str, column: str,
               failure_mode: str, rationale: str) -> str:
        action = (action or "").strip().upper()
        if action not in ("REPAIR", "ESCALATE"):
            return "ERROR: action must be exactly 'REPAIR' or 'ESCALATE'."
        self.finished = Finish(action, table or "", column or "",
                               failure_mode or "", rationale or "")
        return "Recorded. Investigation complete."

    # ------------------------------------------------------------- dispatch
    def call(self, name: str, args: dict) -> str:
        fn = {
            "list_tables": lambda: self.list_tables(),
            "describe_table": lambda: self.describe_table(args.get("table", "")),
            "run_sql": lambda: self.run_sql(args.get("query", "")),
            "read_model": lambda: self.read_model(args.get("model", "")),
            "apply_fix": lambda: self.apply_fix(args.get("sql", "")),
            "rebuild": lambda: self.rebuild(),
            "finish": lambda: self.finish(
                args.get("action", ""), args.get("table", ""),
                args.get("column", ""), args.get("failure_mode", ""),
                args.get("rationale", "")),
        }.get(name)
        if fn is None:
            return f"ERROR: unknown tool '{name}'."
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {type(exc).__name__}: {exc}"


def _p(**props):
    return {"type": "object", "properties": props,
            "required": list(props), "additionalProperties": False}


SCHEMAS = [
    {"type": "function", "function": {
        "name": "list_tables",
        "description": "List every table and view in the warehouse with row counts.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "describe_table",
        "description": "Show the columns, types and five sample rows of a table.",
        "parameters": _p(table={"type": "string", "description": "Table name."})}},
    {"type": "function", "function": {
        "name": "run_sql",
        "description": "Run a read-only SQL query (DuckDB dialect) and return the result.",
        "parameters": _p(query={"type": "string", "description": "A SELECT statement."})}},
    {"type": "function", "function": {
        "name": "read_model",
        "description": "Read the SQL source of a transformation model.",
        "parameters": _p(model={"type": "string", "description": "Model name, e.g. stg_fx."})}},
    {"type": "function", "function": {
        "name": "apply_fix",
        "description": ("Apply a single data-modifying statement to a raw_* source "
                        "table. Only use this once you are confident of the root cause."),
        "parameters": _p(sql={"type": "string",
                              "description": "One UPDATE, DELETE, INSERT or ALTER statement."})}},
    {"type": "function", "function": {
        "name": "rebuild",
        "description": "Rebuild all models from the source tables and report success or the error.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "finish",
        "description": ("End the investigation. Call exactly once, when you have "
                        "either completed a repair or concluded that the issue must "
                        "be escalated rather than repaired."),
        "parameters": _p(
            action={"type": "string", "enum": ["REPAIR", "ESCALATE"],
                    "description": "REPAIR if you fixed it; ESCALATE if it must not be fixed here."},
            table={"type": "string", "description": "Source table where the problem originates."},
            column={"type": "string", "description": "Column at fault, or empty if row-level."},
            failure_mode={"type": "string", "description": "One sentence naming the defect."},
            rationale={"type": "string", "description": "Why this action is correct."})}},
]

TOOL_NAMES = [s["function"]["name"] for s in SCHEMAS]


def parse_args(raw: str | dict) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
