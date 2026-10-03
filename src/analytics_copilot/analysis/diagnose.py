"""Diagnostic analysis for "why did X change?" questions.

The model is used for one narrow job: turning the question into a structured spec
(metric, filters, current period, comparison period). Everything after that is
deterministic and every query is generated from templates, then passed through the same
validator and read-only executor as model-written SQL:

  1. totals       metric in both periods, absolute and % change
  2. benchmark    the same change outside the filter (other states) or a year earlier, to
                  separate "this segment fell" from "everything fell" (seasonality)
  3. decomposition  for each dimension (category, product, channel, store, ...), each
                  segment's share of the change; the finding is the segment whose share of
                  the change most exceeds its share of the baseline ("excess contribution")
  4. signals      related evidence from other tables: stockouts in scope, marketing spend
                  changes by channel, promotions that ended

The output is evidence, not a causal proof, and the explanation says so.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from analytics_copilot.analysis.charts import Chart, contribution_chart
from analytics_copilot.config import get_settings
from analytics_copilot.dates import describe as describe_dates
from analytics_copilot.llm import LLM
from analytics_copilot.safety.executor import execute
from analytics_copilot.safety.validator import validate
from analytics_copilot.schema.catalog import US_STATES, load_catalog

METRICS = {
    # name: (measure SQL, period column, source)
    "revenue": ("SUM(oi.quantity * oi.unit_price - oi.discount)", "o.ordered_at", "sales"),
    "units_sold": ("SUM(oi.quantity)", "o.ordered_at", "sales"),
    "order_count": ("COUNT(DISTINCT o.order_id)", "o.ordered_at", "sales"),
    "return_count": ("COUNT(*)", "r.returned_on", "returns"),
}
# filter / dimension key -> SQL column
FIELDS = {
    "ship_state": "o.ship_state", "channel": "o.channel", "category": "p.category",
    "subcategory": "p.subcategory", "product_name": "p.product_name", "brand": "p.brand",
    "store_name": "s.store_name", "segment": "c.segment", "reason": "r.reason",
    "promo_code": "o.promo_code",
}
DIMENSIONS = {
    "sales": ["category", "subcategory", "product_name", "channel", "store_name", "ship_state",
              "segment"],
    "returns": ["category", "product_name", "reason", "channel", "ship_state"],
}

SPEC_PROMPT = """Turn the analyst's question into a JSON spec for a change analysis.

{dates}

Fields:
- "metric": one of {metrics}
- "filters": object, keys from {fields}; values must use the exact spellings listed below.
  Only include filters the question states. A US state goes in ship_state as its 2-letter code.
- "current": the period the question is about, as a JSON array of two ISO dates
  ["start", "end"], start inclusive and end exclusive, e.g. ["2026-09-01", "2026-10-01"].
- "baseline": the period to compare against, in the same format. If the question names one, use
  it. Otherwise use the period of the same length immediately before "current".

Known values:
{values}

Reply with only the JSON object."""


@dataclass
class Spec:
    metric: str
    filters: dict[str, str]
    current: tuple[date, date]
    baseline: tuple[date, date]

    def describe(self) -> str:
        f = ", ".join(f"{k} = {v}" for k, v in self.filters.items()) or "all data"
        return (f"{self.metric} for {f}: {self.current[0]} to {self.current[1]} (exclusive) vs "
                f"{self.baseline[0]} to {self.baseline[1]}")


@dataclass
class Finding:
    dimension: str
    segment: str
    baseline: float
    current: float
    change: float
    share_of_change: float     # this segment's change / total change
    share_of_baseline: float   # this segment's baseline / total baseline
    excess: float              # share_of_change - share_of_baseline

    def text(self, metric: str) -> str:
        return (f"{self.dimension} = {self.segment}: {metric} {self.baseline:,.0f} -> "
                f"{self.current:,.0f} ({self.change:+,.0f}); {self.share_of_change:.0%} of the "
                f"total change vs {self.share_of_baseline:.0%} of the baseline")


@dataclass
class Diagnosis:
    spec: Spec
    total_baseline: float
    total_current: float
    benchmark: str | None
    benchmark_change_pct: float | None
    findings: list[Finding]
    signals: list[str]
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    queries: list[str] = field(default_factory=list)
    chart: Chart | None = None

    @property
    def change(self) -> float:
        return self.total_current - self.total_baseline

    @property
    def change_pct(self) -> float | None:
        return self.change / self.total_baseline * 100 if self.total_baseline else None

    def facts(self) -> list[str]:
        m = self.spec.metric
        lines = [f"Scope: {self.spec.describe()}",
                 f"Total {m}: {self.total_baseline:,.0f} -> {self.total_current:,.0f} "
                 f"({self.change:+,.0f}"
                 + (f", {self.change_pct:+.1f}%)" if self.change_pct is not None else ")")]
        if self.benchmark and self.benchmark_change_pct is not None:
            lines.append(f"Benchmark ({self.benchmark}): {self.benchmark_change_pct:+.1f}%")
        lines += ["Largest drivers by excess contribution:"]
        lines += [f"  {i + 1}. {f.text(m)}" for i, f in enumerate(self.findings[:5])]
        if self.signals:
            lines += ["Related evidence:"] + [f"  - {s}" for s in self.signals]
        return lines


# --- spec extraction ------------------------------------------------------------------------

def ground_filters(spec: Spec, question: str) -> list[str]:
    """Drop filters whose value the question never mentions; returns what was dropped.

    Seen in evaluation: "Why did returns go up in spring 2026?" came back with
    reason = 'arrived late', which would have analysed the wrong slice of data.
    """
    q = question.lower()
    names = {code.lower(): name for name, code in US_STATES.items()}
    dropped = []
    for key, value in list(spec.filters.items()):
        v = value.lower()
        mentioned = v in q or (key == "ship_state" and names.get(v, "#") in q)
        if key == "channel" and v == "in_store":
            mentioned = mentioned or "in-store" in q or "in store" in q
        if not mentioned:
            dropped.append(f"{key} = {value}")
            del spec.filters[key]
    return dropped


def extract_spec(question: str, llm: LLM, today: date | None = None) -> Spec:
    today = today or get_settings().as_of_date
    catalog = load_catalog()
    values = []
    for key in ("category", "channel", "reason", "segment", "promo_code"):
        table = "returns" if key == "reason" else {"category": "products", "segment": "customers"
                                                  }.get(key, "orders")
        col = catalog.tables[table].columns.get(key)
        if col and col.values:
            values.append(f"- {key}: " + ", ".join(col.values))
    values.append("- ship_state: " + ", ".join(f"{n.title()}={c}" for n, c in US_STATES.items()))
    prompt = SPEC_PROMPT.format(dates=describe_dates(today), metrics=list(METRICS),
                                fields=list(FIELDS), values="\n".join(values))
    resp = llm.complete([{"role": "system", "content": prompt},
                         {"role": "user", "content": question}], max_tokens=300)
    spec = parse_spec(resp.text)
    ground_filters(spec, question)
    return spec


def parse_spec(text: str) -> Spec:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON object in the model reply")
    # Small models sometimes copy interval notation: ["2026-09-01", "2026-10-01")
    raw = json.loads(re.sub(r'"\s*\)', '"]', m.group(0)))
    metric = raw.get("metric")
    if metric not in METRICS:
        raise ValueError(f"unknown metric {metric!r}")
    filters = {k: str(v) for k, v in (raw.get("filters") or {}).items()
               if k in FIELDS and v not in (None, "", [])}
    if "ship_state" in filters:
        s = filters["ship_state"]
        filters["ship_state"] = US_STATES.get(s.lower(), s.upper())
    current = tuple(date.fromisoformat(d) for d in raw["current"])
    baseline = tuple(date.fromisoformat(d) for d in raw["baseline"])
    if current[0] >= current[1] or baseline[0] >= baseline[1]:
        raise ValueError("empty period")
    return Spec(metric, filters, current, baseline)


# --- SQL templates --------------------------------------------------------------------------

def _from(source: str) -> str:
    if source == "returns":
        return ("FROM returns r JOIN order_items oi ON oi.order_item_id = r.order_item_id "
                "JOIN orders o ON o.order_id = oi.order_id "
                "JOIN products p ON p.product_id = oi.product_id "
                "JOIN stores s ON s.store_id = o.store_id "
                "JOIN customers c ON c.customer_id = o.customer_id")
    return ("FROM orders o JOIN order_items oi ON oi.order_id = o.order_id "
            "JOIN products p ON p.product_id = oi.product_id "
            "JOIN stores s ON s.store_id = o.store_id "
            "JOIN customers c ON c.customer_id = o.customer_id")


def _lit(v: str) -> str:
    return "'" + str(v).replace("'", "''") + "'"


def _where(spec: Spec, *, filters: dict[str, str] | None = None,
           exclude: dict[str, str] | None = None, periods=None) -> str:
    measure, period_col, source = METRICS[spec.metric]
    parts = []
    if source == "sales":
        parts.append("o.status = 'completed'")
    for k, v in (spec.filters if filters is None else filters).items():
        parts.append(f"{FIELDS[k]} = {_lit(v)}")
    for k, v in (exclude or {}).items():
        parts.append(f"{FIELDS[k]} <> {_lit(v)}")
    cur, base = periods or (spec.current, spec.baseline)
    parts.append(f"(({period_col} >= {_lit(cur[0])} AND {period_col} < {_lit(cur[1])}) OR "
                 f"({period_col} >= {_lit(base[0])} AND {period_col} < {_lit(base[1])}))")
    return "WHERE " + " AND ".join(parts)


def _period_case(spec: Spec, periods=None) -> str:
    _, period_col, _ = METRICS[spec.metric]
    cur, _ = periods or (spec.current, spec.baseline)
    return (f"CASE WHEN {period_col} >= {_lit(cur[0])} AND {period_col} < {_lit(cur[1])} "
            "THEN 'current' ELSE 'baseline' END")


class _Runner:
    def __init__(self, role: str):
        self.role = role
        self.queries: list[str] = []

    def __call__(self, sql: str) -> pd.DataFrame:
        check = validate(sql, role=self.role)
        if not check.ok:
            raise ValueError(f"template query rejected: {check.error_text}")
        self.queries.append(check.sql)
        return execute(check.sql, limit=check.limit_applied, timeout_ms=15000).df


def _totals(run: _Runner, spec: Spec, **kw) -> tuple[float, float]:
    measure, _, source = METRICS[spec.metric]
    periods = kw.get("periods")
    df = run(f"SELECT {_period_case(spec, periods)} AS period, {measure} AS value "
             f"{_from(source)} {_where(spec, **kw)} GROUP BY 1")
    vals = dict(zip(df.period, df.value.astype(float), strict=True))
    return vals.get("baseline", 0.0), vals.get("current", 0.0)


def diagnose(spec: Spec, *, role: str = "analyst") -> Diagnosis:
    run = _Runner(role)
    measure, _, source = METRICS[spec.metric]
    base_total, cur_total = _totals(run, spec)
    delta = cur_total - base_total

    # Benchmark: outside the state filter if there is one, otherwise the same periods a year
    # earlier (tells you whether the change is just the usual seasonal pattern).
    bench_name, bench_pct = None, None
    if "ship_state" in spec.filters:
        others = {k: v for k, v in spec.filters.items() if k != "ship_state"}
        b0, b1 = _totals(run, spec, filters=others,
                         exclude={"ship_state": spec.filters["ship_state"]})
        bench_name = "same change in all other states"
    else:
        shift = lambda d: d.replace(year=d.year - 1)  # noqa: E731
        periods = ((shift(spec.current[0]), shift(spec.current[1])),
                   (shift(spec.baseline[0]), shift(spec.baseline[1])))
        b0, b1 = _totals(run, spec, periods=periods)
        bench_name = "same periods one year earlier"
    if b0:
        bench_pct = (b1 - b0) / b0 * 100

    findings: list[Finding] = []
    tables: dict[str, pd.DataFrame] = {}
    for dim in DIMENSIONS[source]:
        if dim in spec.filters:
            continue
        df = run(f"SELECT {FIELDS[dim]} AS segment, {_period_case(spec)} AS period, "
                 f"{measure} AS value {_from(source)} {_where(spec)} GROUP BY 1, 2")
        if df.empty:
            continue
        wide = df.pivot_table(index="segment", columns="period", values="value",
                              aggfunc="sum").fillna(0.0)
        for col in ("baseline", "current"):
            if col not in wide:
                wide[col] = 0.0
        wide["change"] = wide["current"] - wide["baseline"]
        wide = wide.sort_values("change")
        tables[dim] = wide.reset_index()
        if delta == 0 or base_total == 0:
            continue
        share_change = wide["change"] / delta
        share_base = wide["baseline"] / base_total
        excess = share_change - share_base
        seg = excess.idxmax()
        findings.append(Finding(dim, str(seg), float(wide.loc[seg, "baseline"]),
                                float(wide.loc[seg, "current"]), float(wide.loc[seg, "change"]),
                                float(share_change[seg]), float(share_base[seg]),
                                float(excess[seg])))
    findings.sort(key=lambda f: f.excess, reverse=True)

    signals = _signals(run, spec)
    chart = None
    if findings:
        best = tables[findings[0].dimension].copy()
        best = best.reindex(best["change"].abs().sort_values(ascending=False).index).head(12)
        chart = contribution_chart(best.rename(columns={"segment": findings[0].dimension}),
                                   findings[0].dimension, "change",
                                   f"Change in {spec.metric} by {findings[0].dimension}")
    return Diagnosis(spec, base_total, cur_total, bench_name, bench_pct, findings, signals,
                     tables, run.queries, chart)


def _signals(run: _Runner, spec: Spec) -> list[str]:
    out: list[str] = []
    cur0, cur1 = (_lit(d) for d in spec.current)
    state = spec.filters.get("ship_state")

    # Stockouts that were open during the current period, in scope.
    conds = [f"so.started_on < {cur1}", f"(so.ended_on IS NULL OR so.ended_on > {cur0})",
             f"NOT (so.started_on < {_lit(spec.baseline[1])} AND (so.ended_on IS NULL OR "
             f"so.ended_on > {_lit(spec.baseline[0])}))"]
    if state:
        conds.append(f"s.state = {_lit(state)}")
    for k in ("category", "subcategory", "product_name"):
        if k in spec.filters:
            conds.append(f"{FIELDS[k]} = {_lit(spec.filters[k])}")
    df = run("SELECT p.category, count(DISTINCT so.product_id) AS products, "
             "count(DISTINCT so.store_id) AS stores FROM stockouts so "
             "JOIN products p ON p.product_id = so.product_id "
             "JOIN stores s ON s.store_id = so.store_id "
             f"WHERE {' AND '.join(conds)} GROUP BY 1 ORDER BY 2 DESC")
    for r in df.itertuples():
        if r.products >= 2:
            out.append(f"stockout: {r.products} {r.category} products were out of stock at "
                       f"{r.stores} store(s){' in ' + state if state else ''} during the "
                       "current period (and not during the baseline)")

    # Marketing spend by marketing channel, per day, current vs baseline.
    days_c = (spec.current[1] - spec.current[0]).days
    days_b = (spec.baseline[1] - spec.baseline[0]).days
    where_state = f"AND state = {_lit(state)}" if state else ""
    df = run("SELECT channel, "
             f"SUM(amount) FILTER (WHERE spend_date >= {cur0} AND spend_date < {cur1}) AS cur, "
             f"SUM(amount) FILTER (WHERE spend_date >= {_lit(spec.baseline[0])} AND "
             f"spend_date < {_lit(spec.baseline[1])}) AS base "
             f"FROM marketing_spend WHERE true {where_state} GROUP BY 1")
    for r in df.itertuples():
        if r.base and r.base > 0 and r.cur is not None:
            pct = (r.cur / days_c - r.base / days_b) / (r.base / days_b) * 100
            if abs(pct) >= 30:
                out.append(f"marketing: {r.channel} spend per day {pct:+.0f}% vs the baseline"
                           f"{' in ' + state if state else ''}")

    # Promotions active in the baseline but not in the current period.
    if METRICS[spec.metric][2] == "sales":
        df = run("SELECT promo_code, "
                 f"count(*) FILTER (WHERE ordered_at >= {cur0} AND ordered_at < {cur1}) AS cur, "
                 f"count(*) FILTER (WHERE ordered_at >= {_lit(spec.baseline[0])} AND "
                 f"ordered_at < {_lit(spec.baseline[1])}) AS base FROM orders "
                 "WHERE promo_code IS NOT NULL "
                 f"{'AND ship_state = ' + _lit(state) if state else ''} GROUP BY 1")
        for r in df.itertuples():
            if r.base >= 50 and r.cur < r.base * 0.2:
                out.append(f"promotion: {r.promo_code} was used on {r.base} orders in the "
                           f"baseline and {r.cur} in the current period")
    return out


def driver_matches(diag: Diagnosis, expected: dict, top_k: int = 3) -> tuple[bool, str]:
    """Evaluation helper: is the expected driver among the top findings or the signals?"""
    dim, value = expected["dimension"], str(expected["value"]).lower()
    for i, f in enumerate(diag.findings[:top_k]):
        if f.dimension == dim and f.segment.lower() == value:
            return True, f"finding #{i + 1}"
    if dim == "marketing_channel":
        for s in diag.signals:
            if s.startswith("marketing:") and f" {value} " in f" {s} ":
                return True, "signal"
    return False, "not found"
