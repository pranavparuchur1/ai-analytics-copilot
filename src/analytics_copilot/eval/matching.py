"""Execution accuracy: does the predicted query return the same answer as the gold query?

Comparing SQL text is useless (many correct spellings of one query), so both queries are
run and their results compared:

  * column names are ignored; each gold column must match some predicted column by value,
    and extra predicted columns are allowed (a "top product" answer that also shows revenue
    is still right);
  * row counts must be equal;
  * numbers are compared after rounding to 2 decimals, with a relative tolerance, and a rate
    may be given as a fraction or a percentage;
  * timestamps at midnight compare equal to dates;
  * row order only matters when the question asks for a ranked list (order_matters).
"""

from __future__ import annotations

import math
from datetime import date, datetime
from decimal import Decimal

import pandas as pd

REL_TOL = 1e-3


def _norm(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if isinstance(v, pd.Timestamp):
        v = v.to_pydatetime()
    if isinstance(v, datetime):
        return v.date().isoformat() if v.time() == datetime.min.time() else v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, (int, float, Decimal)) and not isinstance(v, bool):
        return float(v)
    return str(v).strip().lower()


def _equal(a, b, scale: float = 1.0) -> bool:
    # A year can come back as 2024 (EXTRACT) or as '2024-01-01' (date_trunc('year', ...)).
    if isinstance(a, str) and isinstance(b, float) or isinstance(a, float) and isinstance(b, str):
        year, text = (a, b) if isinstance(a, float) else (b, a)
        return text[:10] == f"{int(year)}-01-01" if year.is_integer() else False
    if isinstance(a, float) and isinstance(b, float):
        a = a * scale
        return math.isclose(round(a, 2), round(b, 2), rel_tol=REL_TOL, abs_tol=0.011)
    return a == b


def _column(df: pd.DataFrame, i: int) -> list:
    return [_norm(v) for v in df.iloc[:, i].tolist()]


def _columns_match(gold: list, pred: list, ordered: bool, scale: float) -> bool:
    if ordered:
        return all(_equal(p, g, scale) for g, p in zip(gold, pred, strict=True))
    def key(v):
        if isinstance(v, str) and len(v) >= 10 and v[4] == "-" and v[5:10] == "01-01":
            return (False, "year", float(v[:4]))  # sorts with EXTRACT(YEAR ...) numbers
        if isinstance(v, float):
            return (False, "year", round(v, 2))
        return (v is None, str(type(v)), v)
    gs = sorted(gold, key=key)
    ps = sorted([p * scale if isinstance(p, float) else p for p in pred], key=key)
    return all(_equal(p, g) for g, p in zip(gs, ps, strict=True))


def results_match(gold: pd.DataFrame, pred: pd.DataFrame, *, order_matters: bool = False) -> bool:
    if _pivot_match(gold, pred):
        return True
    if len(gold) != len(pred) or pred.shape[1] < gold.shape[1]:
        return False
    if gold.empty:
        return True
    gold_cols = [_column(gold, i) for i in range(gold.shape[1])]
    pred_cols = [_column(pred, j) for j in range(pred.shape[1])]

    # Candidate predicted columns for each gold column, with the scale that makes them match.
    candidates: list[list[tuple[int, float]]] = []
    for g in gold_cols:
        options = []
        for j, p in enumerate(pred_cols):
            for scale in (1.0, 0.01, 100.0):
                if _columns_match(g, p, order_matters, scale):
                    options.append((j, scale))
                    break
        if not options:
            return False
        candidates.append(options)

    # Pick distinct predicted columns per gold column, then check rows line up as tuples
    # (two columns can each match on their own while pairing values differently).
    for choice in _assignments(candidates):
        if _rows_match(gold_cols, pred_cols, choice, order_matters):
            return True
    return False


def _pivot_match(gold: pd.DataFrame, pred: pd.DataFrame) -> bool:
    """Gold is a (label, value) list of n rows; prediction is the same numbers as one wide row.

    "Compare revenue in CA and TX" answered as columns ca_revenue, tx_revenue is correct.
    Only the values are checked: the labels live in the column names, which are free text.
    """
    if gold.shape[1] != 2 or len(gold) < 2 or len(pred) != 1:
        return False
    values = [_norm(v) for v in gold.iloc[:, 1]]
    row = [_norm(v) for v in pred.iloc[0]]
    numbers = [v for v in row if isinstance(v, float)]
    if not all(isinstance(v, float) for v in values) or len(numbers) != len(values):
        return False
    return all(_equal(p, g) for g, p in zip(sorted(values), sorted(numbers), strict=True))


def _assignments(candidates, limit: int = 200):
    count = 0

    def rec(i, used, acc):
        nonlocal count
        if count >= limit:
            return
        if i == len(candidates):
            count += 1
            yield list(acc)
            return
        for j, scale in candidates[i]:
            if j not in used:
                acc.append((j, scale))
                yield from rec(i + 1, used | {j}, acc)
                acc.pop()

    yield from rec(0, frozenset(), [])


def _rows_match(gold_cols, pred_cols, choice, ordered: bool) -> bool:
    n = len(gold_cols[0])
    gold_rows = [tuple(c[r] for c in gold_cols) for r in range(n)]
    pred_rows = [tuple((pred_cols[j][r] * s if isinstance(pred_cols[j][r], float)
                        else pred_cols[j][r]) for j, s in choice) for r in range(n)]
    if ordered:
        return all(_row_eq(g, p) for g, p in zip(gold_rows, pred_rows, strict=True))
    remaining = list(pred_rows)
    for g in gold_rows:
        for k, p in enumerate(remaining):
            if _row_eq(g, p):
                remaining.pop(k)
                break
        else:
            return False
    return True


def _row_eq(g: tuple, p: tuple) -> bool:
    return all(_equal(b, a) for a, b in zip(g, p, strict=True))
