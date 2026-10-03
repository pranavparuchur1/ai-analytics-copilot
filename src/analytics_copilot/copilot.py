"""The copilot pipeline: question in, answer with data, chart, explanation and trace out.

    question
      -> conversation memory (rewrite follow-ups into standalone questions)
      -> answer cache
      -> schema retrieval -> intent (refuse / diagnose / query)
      -> query:    SQL generation -> validation -> read-only execution (self-correcting)
         diagnose: spec extraction -> templated decomposition + benchmark + signals
      -> statistics -> data-quality checks -> chart selection
      -> natural-language explanation -> grounding check on its numbers
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field

import pandas as pd
import sqlglot

from analytics_copilot.analysis.charts import choose_chart
from analytics_copilot.analysis.diagnose import diagnose, extract_spec
from analytics_copilot.analysis.profile import profile
from analytics_copilot.analysis.quality import check_result
from analytics_copilot.analysis.stats import analyze
from analytics_copilot.cache import Cache
from analytics_copilot.config import get_settings
from analytics_copilot.explain import (
    explain_answer,
    explain_diagnosis,
    explain_query,
    ungrounded_numbers,
)
from analytics_copilot.intent import classify
from analytics_copilot.llm import LLM, GatewayLLM
from analytics_copilot.memory import Memory, Turn, rewrite
from analytics_copilot.safety.executor import QueryResult, execute
from analytics_copilot.safety.validator import default_policy
from analytics_copilot.schema.retrieval import retrieve
from analytics_copilot.sqlgen import generate

MAX_ROWS_RETURNED = 200


@dataclass
class Answer:
    id: str
    question: str
    standalone_question: str
    status: str                      # ok | refused | error
    intent: str
    answer: str = ""
    sql: str | None = None
    sql_steps: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    row_count: int = 0
    facts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    chart: dict | None = None
    chart_kind: str = "none"
    chart_reason: str = ""
    tables_used: list[str] = field(default_factory=list)
    attempts: list[dict] = field(default_factory=list)
    timings_ms: dict[str, float] = field(default_factory=dict)
    tokens: int = 0
    cached: bool = False
    model: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class Copilot:
    def __init__(self, llm: LLM | None = None, cache: Cache | None = None,
                 memory: Memory | None = None, *, max_corrections: int = 2):
        self.llm = llm or GatewayLLM()
        self.cache = cache if cache is not None else Cache()
        self.memory = memory or Memory()
        self.max_corrections = max_corrections
        self.settings = get_settings()

    def ask(self, question: str, *, session: str = "default", role: str = "analyst") -> Answer:
        t_start = time.perf_counter()
        timings: dict[str, float] = {}
        tokens = 0

        def lap(name: str, t0: float) -> float:
            now = time.perf_counter()
            timings[name] = round((now - t0) * 1000, 1)
            return now

        t = time.perf_counter()
        history = self.memory.history(session)
        standalone = rewrite(question, history, self.llm)
        t = lap("memory", t)

        key = self.cache.answer_key(standalone, role, self.llm.model,
                                    self.settings.as_of_date.isoformat())
        hit = self.cache.get_answer(key)
        if hit:
            ans = Answer(**hit)
            ans.id, ans.question, ans.cached = uuid.uuid4().hex[:12], question, True
            ans.timings_ms = {"cache": round((time.perf_counter() - t_start) * 1000, 1)}
            self._remember(session, ans)
            return ans
        t = lap("cache_lookup", t)

        retrieval = retrieve(standalone)
        intent = classify(standalone, retrieval)
        original = classify(question, retrieve(question))
        if original.kind.startswith("refuse"):  # never let a rewrite launder a refusal
            intent = original
        t = lap("intent", t)
        ans = Answer(uuid.uuid4().hex[:12], question, standalone, "ok", intent.kind,
                     model=self.llm.model)
        if intent.refused:
            ans.status, ans.answer = "refused", intent.reason
        elif intent.kind == "diagnose":
            tokens += self._diagnose(ans, standalone, role, timings)
        else:
            context = [{"question": history[-1].standalone, "sql": history[-1].sql}] \
                if history and history[-1].sql and standalone != question else None
            tokens += self._query(ans, standalone, role, context, retrieval, timings)

        ans.tokens = tokens
        ans.timings_ms = {**timings, "total": round((time.perf_counter() - t_start) * 1000, 1)}
        if ans.status == "ok":
            self.cache.put_answer(key, ans.to_dict())
        self._remember(session, ans)
        return ans

    # -- query path ---------------------------------------------------------------------------

    def _query(self, ans: Answer, question: str, role: str, context, retrieval, timings) -> int:
        t = time.perf_counter()
        out = generate(question, self.llm, role=role, max_corrections=self.max_corrections,
                       context=context, run=self._cached_execute)
        timings["sql_generation"] = round(out.llm_ms, 1)
        timings["sql_execution"] = round((time.perf_counter() - t) * 1000 - out.llm_ms, 1)
        ans.attempts = [{"sql": a.sql, "error": a.error, "stage": a.stage} for a in out.attempts]
        ans.tables_used = out.retrieval.tables
        tokens = out.prompt_tokens + out.completion_tokens
        if not out.ok:
            ans.status = "error"
            blocked = any(a.stage == "validate" for a in out.attempts)
            ans.answer = ("I could not write a safe query for that. " if blocked else
                          "I could not answer that from the data. ") + f"Last error: {out.error}"
            return tokens
        ans.sql = out.sql
        ans.sql_steps = explain_query(out.sql)
        tokens += self._analyse(ans, question, out.result, timings)
        return tokens

    def _cached_execute(self, sql: str, limit: int | None = None) -> QueryResult:
        df = self.cache.get_result(sql)
        if df is not None:
            return QueryResult(df=df, elapsed_ms=0.0,
                               truncated=limit is not None and limit >= default_policy().max_rows
                               and len(df) >= limit,
                               plan_cost=None)
        result = execute(sql, limit=limit)
        self.cache.put_result(sql, result.df)
        return result

    def _analyse(self, ans: Answer, question: str, result: QueryResult, timings) -> int:
        t = time.perf_counter()
        df = result.df
        prof = profile(df)
        facts = [f.text for f in analyze(df, prof, partial=_top_n(ans.sql, len(df)))]
        warnings = [w.message for w in check_result(df, prof, truncated=result.truncated,
                                                     today=self.settings.as_of_date)]
        chart = choose_chart(df, prof, title="")
        timings["analysis"] = round((time.perf_counter() - t) * 1000, 1)
        t = time.perf_counter()
        text, tokens = explain_answer(question, df, facts, warnings, self.llm)
        loose = ungrounded_numbers(text, df, facts)
        if loose:
            warnings.append("The explanation mentions numbers not found in the results: "
                            + ", ".join(loose) + ". Rely on the table.")
        timings["explanation"] = round((time.perf_counter() - t) * 1000, 1)
        self._fill(ans, df, facts, warnings, chart.spec, chart.kind, chart.reason, text)
        return tokens

    # -- diagnose path ------------------------------------------------------------------------

    def _diagnose(self, ans: Answer, question: str, role: str, timings) -> int:
        t = time.perf_counter()
        try:
            spec = extract_spec(question, self.llm)
        except (ValueError, KeyError) as e:
            ans.status, ans.answer = "error", f"I could not work out what to compare: {e}"
            return 0
        timings["spec_extraction"] = round((time.perf_counter() - t) * 1000, 1)
        t = time.perf_counter()
        diag = diagnose(spec, role=role)
        timings["diagnostic_queries"] = round((time.perf_counter() - t) * 1000, 1)
        facts = diag.facts()
        t = time.perf_counter()
        text, tokens = explain_diagnosis(question, facts, self.llm)
        timings["explanation"] = round((time.perf_counter() - t) * 1000, 1)
        best = diag.findings[0].dimension if diag.findings else None
        df = diag.tables[best] if best else pd.DataFrame()
        warnings = []
        loose = ungrounded_numbers(text, df, facts)
        if loose:
            warnings.append("The explanation mentions numbers not found in the evidence: "
                            + ", ".join(loose) + ".")
        warnings.append("Diagnostic findings show where the change is concentrated and what "
                        "coincided with it; they are not proof of cause.")
        ans.sql = ";\n\n".join(diag.queries)
        ans.sql_steps = [f"Ran {len(diag.queries)} templated queries: totals, a benchmark, "
                         f"one decomposition per dimension, and related-signal checks",
                         f"Spec: {spec.describe()}"]
        ans.tables_used = sorted({"orders", "order_items", "products", "stores", "customers",
                                  "stockouts", "marketing_spend"}
                                 | ({"returns"} if spec.metric == "return_count" else set()))
        self._fill(ans, df.rename(columns={"segment": best}) if best else df, facts, warnings,
                   diag.chart.spec if diag.chart else None,
                   diag.chart.kind if diag.chart else "none",
                   diag.chart.reason if diag.chart else "", text)
        return tokens

    # -- helpers ------------------------------------------------------------------------------

    @staticmethod
    def _fill(ans, df, facts, warnings, spec, kind, reason, text) -> None:
        ans.facts, ans.warnings, ans.answer = facts, warnings, text
        ans.chart, ans.chart_kind, ans.chart_reason = spec, kind, reason
        ans.row_count = len(df)
        ans.columns = [str(c) for c in df.columns]
        head = df.head(MAX_ROWS_RETURNED)
        ans.rows = [[_jsonable(v) for v in row] for row in head.itertuples(index=False)]

    def _remember(self, session: str, ans: Answer) -> None:
        summary = ans.answer if ans.status == "ok" else f"({ans.status}) {ans.answer}"
        self.memory.add(session, Turn(ans.question, ans.standalone_question,
                                      ans.sql if ans.intent == "query" else None, summary))


def _top_n(sql: str | None, n_rows: int) -> bool:
    """True when the query's own LIMIT (below the policy cap) cut the result to n rows."""
    if not sql:
        return False
    limit = sqlglot.parse_one(sql, read="postgres").args.get("limit")
    try:
        value = int(limit.expression.this) if limit is not None else None
    except (AttributeError, ValueError):
        return False
    return value is not None and value < default_policy().max_rows and n_rows == value


def _jsonable(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if hasattr(v, "item"):
        return v.item()
    return v
