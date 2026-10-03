"""Deterministic statistics on a query result.

The explanation model is given these computed facts instead of being asked to do
arithmetic on a table: small models misread and mis-add numbers, pandas does not.
Every number the explanation is allowed to cite comes from here or from the result.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from analytics_copilot.analysis.profile import Profile, as_time


@dataclass
class Fact:
    kind: str
    text: str


def _fmt(v: float) -> str:
    if abs(v) >= 100:
        return f"{v:,.0f}"
    if abs(v) >= 1:
        return f"{v:,.2f}"
    return f"{v:.4g}"


def _pct(new: float, old: float) -> str | None:
    if old == 0 or pd.isna(old) or pd.isna(new):
        return None
    return f"{(new - old) / abs(old) * 100:+.1f}%"


def analyze(df: pd.DataFrame, prof: Profile, *, partial: bool = False) -> list[Fact]:
    """`partial`: the query kept only the top N rows (its own LIMIT), so shares of the
    returned rows' total would be misread as shares of everything; they are omitted."""
    if df.empty:
        return [Fact("empty", "The query returned no rows.")]
    shape = prof.shape
    if shape == "single_value":
        col = prof.measures[0]
        return [Fact("value", f"{col} = {_fmt(float(df[col].iloc[0]))}")] if len(df) == 1 else \
            _distribution(df[col], col)
    if shape == "time_series":
        return _time_series(df, prof)
    if shape == "breakdown":
        return _breakdown(df, prof, partial=partial)
    if shape == "relationship":
        a, b = prof.measures[:2]
        if len(df) >= 3:
            r = df[[a, b]].astype(float).corr().iloc[0, 1]
            return [Fact("correlation", f"Correlation between {a} and {b}: r = {r:.2f} "
                         f"over {len(df)} rows")]
    return [Fact("rows", f"{len(df)} rows returned")]


def _distribution(s: pd.Series, col: str) -> list[Fact]:
    s = s.astype(float)
    return [Fact("distribution", f"{col}: n = {len(s)}, mean {_fmt(s.mean())}, median "
                 f"{_fmt(s.median())}, min {_fmt(s.min())}, max {_fmt(s.max())}")]


def _time_series(df: pd.DataFrame, prof: Profile) -> list[Fact]:
    facts: list[Fact] = []
    t = as_time(df[prof.time])
    measure = prof.measures[0]
    data = df.assign(_t=t)
    if prof.dimensions:  # several series: summarise each one's change
        dim = prof.dimensions[0]
        for key, g in data.groupby(dim, sort=False):
            g = g.sort_values("_t")
            if len(g) >= 2:
                first, last = float(g[measure].iloc[0]), float(g[measure].iloc[-1])
                change = _pct(last, first)
                facts.append(Fact("series_change",
                                  f"{dim} = {key}: {measure} went from {_fmt(first)} to "
                                  f"{_fmt(last)}" + (f" ({change})" if change else "")))
        return facts[:8]

    g = data.sort_values("_t")
    y = g[measure].astype(float).to_numpy()
    labels = [_label(v) for v in g["_t"]]
    facts.append(Fact("range", f"{measure} from {labels[0]} to {labels[-1]}: "
                      f"{_fmt(y[0])} -> {_fmt(y[-1])}"
                      + (f" ({_pct(y[-1], y[0])})" if _pct(y[-1], y[0]) else "")))
    if len(y) >= 2:
        change = _pct(y[-1], y[-2])
        if change:
            facts.append(Fact("last_change", f"Latest period ({labels[-1]}) vs previous "
                              f"({labels[-2]}): {change}"))
    facts.append(Fact("total", f"Sum of {measure} over the {len(y)} periods: {_fmt(y.sum())}"))
    hi, lo = int(np.argmax(y)), int(np.argmin(y))
    facts.append(Fact("extremes", f"Highest {measure}: {labels[hi]} ({_fmt(y[hi])}); lowest: "
                      f"{labels[lo]} ({_fmt(y[lo])})"))
    if len(y) >= 4:
        x = np.arange(len(y))
        slope, intercept = np.polyfit(x, y, 1)
        mean = y.mean()
        if mean:
            facts.append(Fact("trend", f"Linear trend: {slope / abs(mean) * 100:+.1f}% of the "
                              "average per period"))
        resid = y - (slope * x + intercept)
        sd = resid.std()
        if sd > 0:
            for i in np.where(np.abs(resid / sd) > 2.0)[0][:3]:
                facts.append(Fact("anomaly", f"{labels[i]} is unusual: {_fmt(y[i])} vs "
                                  f"{_fmt(slope * i + intercept)} expected from the trend"))
    return facts


def _breakdown(df: pd.DataFrame, prof: Profile, *, partial: bool = False) -> list[Fact]:
    dim, measure = prof.dimensions[0], prof.measures[0]
    g = df.groupby(dim, sort=False)[measure].sum().astype(float).sort_values(ascending=False)
    total = g.sum()
    facts = [Fact("top", f"Largest {dim}: {g.index[0]} with {_fmt(g.iloc[0])}"
                  + (f" ({g.iloc[0] / total * 100:.1f}% of the {_fmt(total)} total across "
                     f"the {len(g)} rows shown)"
                     if total > 0 and len(g) > 1 and not partial else ""))]
    if len(g) == 2:
        a, b = g.iloc[0], g.iloc[1]
        rel = f" ({(a - b) / b * 100:+.1f}% relative to {g.index[1]})" if b else ""
        facts.append(Fact("difference", f"{g.index[0]} minus {g.index[1]}: {_fmt(a - b)}{rel}"))
    if len(g) >= 2:
        facts.append(Fact("runner_up", f"Second: {g.index[1]} with {_fmt(g.iloc[1])}"
                          + (f"; the leader is {g.iloc[0] / g.iloc[1]:.2f}x the second"
                             if g.iloc[1] > 0 else "")))
    if partial:
        facts.append(Fact("partial", f"Only the top {len(g)} rows were requested, so totals "
                          "and shares of the overall total are not known from this result"))
    elif len(g) >= 5 and total > 0:
        facts.append(Fact("concentration", f"Top 3 of {len(g)} {dim} values hold "
                          f"{g.iloc[:3].sum() / total * 100:.1f}% of {measure}"))
    if len(g) >= 3:
        facts.append(Fact("smallest", f"Smallest {dim}: {g.index[-1]} with {_fmt(g.iloc[-1])}"))
    if len(prof.measures) >= 2 and len(df) >= 3:
        a, b = prof.measures[:2]
        r = df[[a, b]].astype(float).corr().iloc[0, 1]
        if not np.isnan(r):
            facts.append(Fact("correlation", f"Across {dim}, {a} and {b} correlate at r = {r:.2f}"))
    return facts


def _label(v) -> str:
    if isinstance(v, pd.Timestamp):
        if v.day == 1 and v.hour == 0:
            return v.strftime("%b %Y")
        return v.strftime("%Y-%m-%d")
    return str(int(v)) if isinstance(v, float) and v.is_integer() else str(v)
