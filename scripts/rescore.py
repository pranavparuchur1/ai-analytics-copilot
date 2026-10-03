"""Re-score saved evaluation runs with the current result matcher, without calling the LLM.

Each run stores the SQL the model produced, so a change to the scorer (not the model or
prompt) can be applied to every past run by re-executing that SQL and the gold SQL.

usage: uv run python scripts/rescore.py <label> [<label> ...]
"""

from __future__ import annotations

import json
import sys

from analytics_copilot.eval.matching import results_match
from analytics_copilot.eval.runner import RUNS, Record, load_golden, summarize
from analytics_copilot.safety.executor import QueryError, execute
from analytics_copilot.safety.validator import validate


def main(labels: list[str]) -> None:
    gold_cases = {c["id"]: c for c in load_golden("all")}
    gold_cache = {}
    for label in labels:
        summary = json.loads((RUNS / f"{label}.summary.json").read_text())
        lines = (RUNS / f"{label}.jsonl").read_text().splitlines()
        records = [Record(**json.loads(x)) for x in lines]
        changed = []
        for r in records:
            if r.kind != "sql" or not r.sql:
                continue
            case = gold_cases[r.id]
            if r.id not in gold_cache:
                gold_cache[r.id] = execute(validate(case["gold_sql"]).sql).df
            try:
                pred = execute(r.sql).df
            except QueryError:
                continue
            ordered = bool(case.get("order_matters"))
            ok = results_match(gold_cache[r.id], pred, order_matters=ordered)
            if ok != r.correct:
                changed.append(f"{r.id}: {r.correct} -> {ok}")
                r.correct = ok
            if r.attempts == 1:  # the first attempt was the final query
                r.first_attempt_correct = ok
        new = summarize(records)
        for k in ("label", "model", "use_retrieval", "max_corrections", "split"):
            new[k] = summary.get(k)
        new["rescored"] = True
        (RUNS / f"{label}.summary.json").write_text(json.dumps(new, indent=2))
        with (RUNS / f"{label}.jsonl").open("w") as f:
            for r in records:
                f.write(json.dumps(r.__dict__) + "\n")
        print(f"{label}: {summary['execution_accuracy']} -> {new['execution_accuracy']} "
              f"{changed}")


if __name__ == "__main__":
    main(sys.argv[1:])
