"""Run validated SQL as the least-privilege copilot_ro role, inside a read-only transaction."""

from __future__ import annotations

import time
from dataclasses import dataclass

import pandas as pd
import psycopg

from analytics_copilot.config import get_settings
from analytics_copilot.safety.validator import Policy, default_policy


class QueryError(Exception):
    """The database refused or failed the query. `kind` is a short machine-readable reason."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


@dataclass
class QueryResult:
    df: pd.DataFrame
    elapsed_ms: float
    truncated: bool       # the row limit was reached, so there may be more rows
    plan_cost: float | None


def execute(sql: str, *, limit: int | None = None, timeout_ms: int = 5000,
            policy: Policy | None = None, dsn: str | None = None) -> QueryResult:
    policy = policy or default_policy()
    dsn = dsn or get_settings().dsn()
    t0 = time.perf_counter()
    try:
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            conn.read_only = True  # BEGIN READ ONLY: the server rejects any write
            with conn.cursor() as cur:
                cur.execute(f"SET LOCAL statement_timeout = {int(timeout_ms)}")
                cur.execute(f"EXPLAIN (FORMAT JSON) {sql}")
                cost = float(cur.fetchone()[0][0]["Plan"]["Total Cost"])
                if cost > policy.max_plan_cost:
                    raise QueryError("too_expensive",
                                     f"estimated plan cost {cost:,.0f} exceeds the limit of "
                                     f"{policy.max_plan_cost:,.0f}; add filters or aggregate "
                                     "before joining")
                cur.execute(sql)
                cols = [d.name for d in cur.description]
                rows = cur.fetchall()
            conn.rollback()
    except QueryError:
        raise
    except psycopg.errors.QueryCanceled as e:
        raise QueryError("timeout", f"query exceeded {timeout_ms} ms") from e
    except psycopg.errors.InsufficientPrivilege as e:
        raise QueryError("permission_denied", _clean(e)) from e
    except psycopg.errors.ReadOnlySqlTransaction as e:
        raise QueryError("read_only", _clean(e)) from e
    except psycopg.Error as e:
        raise QueryError("sql_error", _clean(e)) from e
    df = pd.DataFrame(rows, columns=cols)
    df = _coerce_numeric(df)
    return QueryResult(df=df, elapsed_ms=(time.perf_counter() - t0) * 1000,
                       truncated=limit is not None and len(df) >= limit, plan_cost=cost)


def _coerce_numeric(df: pd.DataFrame) -> pd.DataFrame:
    """Postgres numeric arrives as Decimal; analysis and charts want floats."""
    for col in df.columns:
        if df[col].dtype == object and len(df) and df[col].map(
                lambda v: v is None or type(v).__name__ == "Decimal").all():
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _clean(e: psycopg.Error) -> str:
    return str(e).strip().splitlines()[0]
