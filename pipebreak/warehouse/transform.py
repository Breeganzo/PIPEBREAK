"""A minimal dbt-compatible transformation runner.

The model files use standard dbt syntax (``{{ ref('model') }}``) and the
staging/marts directory convention, so they remain drop-in compatible with
dbt-duckdb. Running them here instead removes a heavy dependency and keeps the
benchmark harness hermetic and fast, which matters when every task instance
rebuilds the warehouse from scratch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from pipebreak import config

REF = re.compile(r"\{\{\s*ref\(\s*['\"]([A-Za-z0-9_]+)['\"]\s*\)\s*\}\}")


@dataclass
class Model:
    name: str
    path: Path
    sql: str
    materialization: str
    refs: list[str] = field(default_factory=list)


class BuildError(RuntimeError):
    """Raised when a model fails to compile or execute."""

    def __init__(self, model: str, message: str):
        self.model = model
        super().__init__(f"model '{model}' failed: {message}")


def load_models(models_dir: Path | None = None) -> dict[str, Model]:
    models_dir = Path(models_dir or config.DBT_DIR / "models")
    models: dict[str, Model] = {}
    for path in sorted(models_dir.rglob("*.sql")):
        sql = path.read_text()
        mat = "view" if "staging" in path.parts else "table"
        models[path.stem] = Model(
            name=path.stem, path=path, sql=sql,
            materialization=mat, refs=sorted(set(REF.findall(sql))),
        )
    return models


def _order(models: dict[str, Model]) -> list[Model]:
    """Depth-first topological sort. Raises on a circular dependency."""
    ordered: list[Model] = []
    state: dict[str, int] = {}

    def visit(name: str, trail: tuple[str, ...]) -> None:
        if state.get(name) == 2:
            return
        if state.get(name) == 1:
            raise BuildError(name, f"circular dependency: {' -> '.join(trail + (name,))}")
        state[name] = 1
        for dep in models[name].refs:
            if dep not in models:
                raise BuildError(name, f"ref('{dep}') does not resolve to a model")
            visit(dep, trail + (name,))
        state[name] = 2
        ordered.append(models[name])

    for name in models:
        visit(name, ())
    return ordered


def compile_sql(model: Model) -> str:
    return REF.sub(lambda m: m.group(1), model.sql)


def _drop_existing(con: duckdb.DuckDBPyConnection, name: str) -> None:
    """Drop a relation by its actual type.

    ``DROP VIEW IF EXISTS x`` raises in DuckDB when ``x`` exists as a table:
    IF EXISTS suppresses absence, not a type mismatch. Materialisations change
    between runs, so the catalog is consulted rather than assumed.
    """
    row = con.execute(
        "SELECT table_type FROM information_schema.tables WHERE table_name = ?",
        [name],
    ).fetchone()
    if row is None:
        return
    kind = "VIEW" if str(row[0]).upper() == "VIEW" else "TABLE"
    con.execute(f"DROP {kind} IF EXISTS {name}")


def build(con: duckdb.DuckDBPyConnection,
          models: dict[str, Model] | None = None) -> list[str]:
    """Materialise every model in dependency order. Returns models built."""
    models = models if models is not None else load_models()
    built: list[str] = []
    for model in _order(models):
        sql = compile_sql(model)
        kind = "VIEW" if model.materialization == "view" else "TABLE"
        try:
            _drop_existing(con, model.name)
            con.execute(f"CREATE {kind} {model.name} AS {sql}")
        except Exception as exc:  # noqa: BLE001 - surfaced verbatim to the agent
            raise BuildError(model.name, str(exc).strip()) from exc
        built.append(model.name)
    return built


def build_path(db_path: Path) -> list[str]:
    con = duckdb.connect(str(db_path))
    try:
        return build(con)
    finally:
        con.close()


def snapshot_marts(db_path: Path, dest: Path) -> None:
    """Write each mart to parquet so it can be compared byte-for-byte later."""
    dest.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        for mart in config.MARTS:
            out = (dest / f"{mart}.parquet").as_posix()
            con.execute(f"COPY (SELECT * FROM {mart}) TO '{out}' (FORMAT PARQUET)")
    finally:
        con.close()
