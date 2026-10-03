"""Evaluation harness for NL -> SQL.

For each golden question: generate SQL with the configured model/settings, run it, run the
gold query, and compare results (execution accuracy). Also records whether the first
attempt was already right (so the value of the correction loop is visible), whether
schema retrieval selected every table the gold query uses, prompt tokens and latency.

Refusal questions go through the intent classifier, then (if it lets them through) the
generator; they count as refused when no rows reach the user.
"""

from __future__ import annotations

import json
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

from analytics_copilot.config import REPO_ROOT
from analytics_copilot.eval.matching import results_match
from analytics_copilot.intent import classify
from analytics_copilot.llm import LLM
from analytics_copilot.safety.executor import QueryError, execute
from analytics_copilot.safety.validator import validate
from analytics_copilot.schema.retrieval import retrieve
from analytics_copilot.sqlgen import generate

GOLDEN = REPO_ROOT / "evaluation" / "golden.yaml"
RUNS = REPO_ROOT / "evaluation" / "runs"


@dataclass
class Record:
    id: str
    split: str
    category: str
    question: str
    kind: str
    correct: bool
    first_attempt_correct: bool | None = None
    executed: bool | None = None
    attempts: int = 0
    stopped_by: str | None = None  # refusals: intent | validator | database | none
    retrieval_recall: float | None = None
    tables_selected: int | None = None
    prompt_tokens: int = 0
    latency_ms: float = 0.0
    sql: str | None = None
    error: str | None = None


def load_golden(split: str | None = None, kinds=("sql", "refuse")) -> list[dict]:
    cases = yaml.safe_load(GOLDEN.read_text())
    return [c for c in cases if c.get("kind", "sql") in kinds
            and (split in (None, "all") or c["split"] == split)]


def run_case(case: dict, llm: LLM, *, use_retrieval: bool, max_corrections: int,
             gold_cache: dict) -> Record:
    kind = case.get("kind", "sql")
    rec = Record(case["id"], case["split"], case["category"], case["question"], kind, False)
    t0 = time.perf_counter()

    if kind == "refuse":
        intent = classify(case["question"], retrieve(case["question"]))
        if intent.refused:
            rec.correct, rec.stopped_by = True, "intent"
        else:
            out = generate(case["question"], llm, max_corrections=max_corrections,
                           use_retrieval=use_retrieval)
            rec.sql, rec.attempts = out.sql, len(out.attempts)
            stages = {a.stage for a in out.attempts}
            if out.ok and not out.result.df.empty:
                rec.stopped_by = "none"  # data was returned: refusal failed
            else:
                rec.correct = True
                rec.stopped_by = "validator" if "validate" in stages else "database"
        rec.latency_ms = (time.perf_counter() - t0) * 1000
        return rec

    gold_check = validate(case["gold_sql"])
    if case["id"] not in gold_cache:
        gold_cache[case["id"]] = execute(gold_check.sql).df
    gold = gold_cache[case["id"]]

    out = generate(case["question"], llm, max_corrections=max_corrections,
                   use_retrieval=use_retrieval)
    rec.latency_ms = (time.perf_counter() - t0) * 1000
    rec.attempts = len(out.attempts)
    rec.prompt_tokens = out.prompt_tokens
    rec.sql = out.sql
    rec.executed = out.ok
    rec.error = out.error
    selected = set(out.retrieval.tables)
    rec.tables_selected = len(selected)
    rec.retrieval_recall = len(gold_check.tables & selected) / len(gold_check.tables)
    order = bool(case.get("order_matters"))
    if out.ok:
        rec.correct = results_match(gold, out.result.df, order_matters=order)
    first = out.attempts[0] if out.attempts else None
    if first is not None and first.stage in ("ok", "empty") and first.sql:
        if first.sql == out.sql:
            rec.first_attempt_correct = rec.correct
        else:
            try:
                rec.first_attempt_correct = results_match(gold, execute(first.sql).df,
                                                          order_matters=order)
            except QueryError:
                rec.first_attempt_correct = False
    else:
        rec.first_attempt_correct = False
    return rec


def run(llm: LLM, *, label: str, split: str = "all", use_retrieval: bool = True,
        max_corrections: int = 2, ids: list[str] | None = None, verbose: bool = True) -> dict:
    cases = load_golden(split)
    if ids:
        cases = [c for c in cases if c["id"] in ids]
    gold_cache: dict = {}
    records = []
    for case in cases:
        rec = run_case(case, llm, use_retrieval=use_retrieval, max_corrections=max_corrections,
                       gold_cache=gold_cache)
        records.append(rec)
        if verbose:
            mark = "ok " if rec.correct else "BAD"
            print(f"{mark} {rec.id} [{rec.category}] {rec.latency_ms:6.0f} ms "
                  f"att={rec.attempts} {rec.error or ''}"[:160], flush=True)
    summary = summarize(records)
    summary.update(label=label, model=llm.model, use_retrieval=use_retrieval,
                   max_corrections=max_corrections, split=split)
    RUNS.mkdir(parents=True, exist_ok=True)
    with (RUNS / f"{label}.jsonl").open("w") as f:
        for r in records:
            f.write(json.dumps(asdict(r)) + "\n")
    (RUNS / f"{label}.summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def summarize(records: list[Record]) -> dict:
    sql = [r for r in records if r.kind == "sql"]
    ref = [r for r in records if r.kind == "refuse"]

    def rate(rs, attr="correct"):
        vals = [getattr(r, attr) for r in rs if getattr(r, attr) is not None]
        return round(sum(vals) / len(vals), 4) if vals else None

    by_cat = {}
    for cat in sorted({r.category for r in sql}):
        rs = [r for r in sql if r.category == cat]
        by_cat[cat] = {"n": len(rs), "ex": rate(rs)}
    by_split = {s: {"n": len(rs), "ex": rate(rs)} for s in ("dev", "test")
                if (rs := [r for r in sql if r.split == s])}
    lat = sorted(r.latency_ms for r in sql)
    return {
        "n_sql": len(sql),
        "execution_accuracy": rate(sql),
        "first_attempt_accuracy": rate(sql, "first_attempt_correct"),
        "executed_rate": rate(sql, "executed"),
        "mean_attempts": round(statistics.mean(r.attempts for r in sql), 2) if sql else None,
        "retrieval_recall_mean": rate([r for r in sql if r.retrieval_recall is not None],
                                      "retrieval_recall"),
        "retrieval_all_tables_found": round(
            sum(r.retrieval_recall == 1 for r in sql) / len(sql), 4) if sql else None,
        "mean_tables_selected": round(statistics.mean(r.tables_selected for r in sql), 2)
        if sql else None,
        "mean_prompt_tokens": round(statistics.mean(r.prompt_tokens for r in sql)) if sql else None,
        "latency_p50_ms": round(lat[len(lat) // 2]) if lat else None,
        "latency_p95_ms": round(lat[min(len(lat) - 1, int(len(lat) * 0.95))]) if lat else None,
        "by_split": by_split,
        "by_category": by_cat,
        "refusal_accuracy": rate(ref),
        "refusals_stopped_by": {k: sum(r.stopped_by == k for r in ref)
                                for k in ("intent", "validator", "database", "none")},
    }


def load_run(label: str) -> tuple[dict, list[dict]]:
    summary = json.loads((RUNS / f"{label}.summary.json").read_text())
    records = [json.loads(line) for line in (RUNS / f"{label}.jsonl").read_text().splitlines()]
    return summary, records


def runs_dir() -> Path:
    return RUNS
