"""Comparison of a live mart against its pristine reference snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb
import pandas as pd

from pipebreak import config

ROUND = 2


@dataclass
class MartDiff:
    mart: str
    ok: bool
    jaccard: float
    rows_current: int
    rows_reference: int
    missing: int
    extra: int
    error: str = ""

    @property
    def exact(self) -> bool:
        return self.ok and self.jaccard >= 0.9999


def _normalise(df: pd.DataFrame) -> set[tuple]:
    out = df.copy()
    for col in out.columns:
        if pd.api.types.is_float_dtype(out[col]):
            out[col] = out[col].round(ROUND)
        out[col] = out[col].astype(str)
    return set(map(tuple, out.itertuples(index=False, name=None)))


def _read_parquet(path: Path) -> pd.DataFrame:
    """DuckDB reads parquet natively, so pyarrow is not required."""
    con = duckdb.connect()
    try:
        return con.execute(
            "SELECT * FROM read_parquet(?)", [path.as_posix()]
        ).df()
    finally:
        con.close()


def compare_mart(db_path: Path, mart: str,
                 reference: Path | None = None) -> MartDiff:
    reference = Path(reference or config.REFERENCE)
    ref_file = reference / f"{mart}.parquet"
    try:
        ref = _read_parquet(ref_file)
    except Exception as exc:  # noqa: BLE001
        return MartDiff(mart, False, 0.0, 0, 0, 0, 0, f"reference unreadable: {exc}")

    try:
        con = duckdb.connect(str(db_path), read_only=True)
        try:
            cur = con.execute(f"SELECT * FROM {mart}").df()
        finally:
            con.close()
    except Exception as exc:  # noqa: BLE001
        # The model does not exist or will not scan: a total failure, not a diff.
        return MartDiff(mart, False, 0.0, 0, len(ref), len(ref), 0, str(exc).strip())

    if list(cur.columns) != list(ref.columns):
        return MartDiff(mart, False, 0.0, len(cur), len(ref), len(ref), len(cur),
                        "column set changed")

    a, b = _normalise(cur), _normalise(ref)
    inter = len(a & b)
    union = len(a | b) or 1
    return MartDiff(
        mart=mart, ok=True, jaccard=inter / union,
        rows_current=len(cur), rows_reference=len(ref),
        missing=len(b - a), extra=len(a - b),
    )


def compare_all(db_path: Path,
                reference: Path | None = None) -> dict[str, MartDiff]:
    return {m: compare_mart(db_path, m, reference) for m in config.MARTS}
