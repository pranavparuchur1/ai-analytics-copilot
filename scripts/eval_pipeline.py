"""End-to-end evaluation of the copilot (beyond SQL accuracy).

  1. diagnostic "why" questions: is the planted driver among the findings / signals,
     and does the written explanation name it?
  2. explanations: how often the grounding check finds a number that is not in the data;
     per-stage latency over the golden SQL questions
  3. caching: cold vs warm latency for repeated questions (and trivially re-worded ones)
  4. conversation memory: follow-up questions scored by execution accuracy

usage: uv run python scripts/eval_pipeline.py [--skip-explanations]
writes docs/results/pipeline.md and evaluation/runs/pipeline.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import tempfile
import time
from pathlib import Path

import yaml

from analytics_copilot.analysis.diagnose import diagnose, driver_matches, extract_spec
from analytics_copilot.cache import Cache
from analytics_copilot.copilot import Copilot
from analytics_copilot.eval.matching import results_match
from analytics_copilot.llm import GatewayLLM
from analytics_copilot.safety.executor import execute
from analytics_copilot.safety.validator import validate

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = yaml.safe_load((ROOT / "evaluation" / "golden.yaml").read_text())


def fresh_copilot(llm, cache_enabled=False) -> Copilot:
    tmp = Path(tempfile.mkdtemp()) / "cache.db"
    return Copilot(llm, cache=Cache(tmp, enabled=cache_enabled))


def eval_why(llm) -> list[dict]:
    out = []
    for c in [c for c in GOLDEN if c.get("kind") == "why"]:
        spec = extract_spec(c["question"], llm)
        diag = diagnose(spec)
        found, where = driver_matches(diag, c["expect_driver"])
        ans = fresh_copilot(llm).ask(c["question"])
        named = str(c["expect_driver"]["value"]).lower() in ans.answer.lower()
        out.append({"id": c["id"], "question": c["question"], "spec": spec.describe(),
                    "driver_found": found, "where": where, "explanation_names_driver": named,
                    "top_finding": diag.findings[0].text(spec.metric) if diag.findings else None,
                    "signals": diag.signals, "answer": ans.answer,
                    "ungrounded_warning": any("not found" in w for w in ans.warnings)})
        print(f"why {c['id']}: found={found} ({where}) named={named}", flush=True)
    return out


def eval_explanations(llm) -> dict:
    copilot = fresh_copilot(llm)
    stages: dict[str, list[float]] = {}
    totals, ungrounded, statuses = [], [], []
    for c in [c for c in GOLDEN if c.get("kind", "sql") == "sql"]:
        ans = copilot.ask(c["question"], session=c["id"])
        statuses.append(ans.status)
        if ans.status != "ok":
            continue
        for k, v in ans.timings_ms.items():
            stages.setdefault(k, []).append(v)
        totals.append(ans.timings_ms["total"])
        bad = [w for w in ans.warnings if "not found in the results" in w]
        ungrounded.append({"id": c["id"], "warning": bad[0]} if bad else None)
        print(f"explain {c['id']}: {ans.timings_ms['total']:.0f} ms "
              f"{'UNGROUNDED' if bad else ''}", flush=True)
    flagged = [u for u in ungrounded if u]
    return {"answered": len(totals), "asked": len(statuses),
            "flagged_ungrounded": len(flagged), "flagged": flagged,
            "stage_p50_ms": {k: round(statistics.median(v)) for k, v in stages.items()},
            "total_p50_ms": round(statistics.median(totals)),
            "total_p95_ms": round(sorted(totals)[int(len(totals) * 0.95) - 1])}


def eval_cache(llm) -> dict:
    copilot = fresh_copilot(llm, cache_enabled=True)
    qs = [c["question"] for c in GOLDEN if c.get("kind", "sql") == "sql"][:10]
    def timed(question, session):
        t = time.perf_counter()
        a = copilot.ask(question, session=session)
        return time.perf_counter() - t, a.cached

    cold = [timed(q, "cold")[0] for q in qs]
    warm = [timed(q, "warm") for q in qs]
    reworded = [timed("  " + q.upper().rstrip("?.") + " ", "rw") for q in qs]
    return {"n": len(qs), "cold_p50_ms": round(statistics.median(cold) * 1000),
            "warm_p50_ms": round(statistics.median(w for w, _ in warm) * 1000, 2),
            "warm_hit_rate": sum(h for _, h in warm) / len(warm),
            "case_and_punctuation_variant_hit_rate": sum(h for _, h in reworded) / len(reworded)}


def eval_memory(llm) -> list[dict]:
    convs = yaml.safe_load((ROOT / "evaluation" / "conversations.yaml").read_text())
    out = []
    for conv in convs:
        copilot = fresh_copilot(llm)
        for turn in conv["turns"]:
            ans = copilot.ask(turn, session=conv["id"])
        gold = execute(validate(conv["gold_sql"]).sql).df
        ok = False
        if ans.status == "ok" and ans.sql:
            pred = execute(ans.sql).df
            ok = results_match(gold, pred, order_matters=bool(conv.get("order_matters")))
        out.append({"id": conv["id"], "turns": conv["turns"], "rewritten": ans.standalone_question,
                    "correct": ok})
        print(f"memory {conv['id']}: {ok} <- {ans.standalone_question}", flush=True)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-explanations", action="store_true")
    args = ap.parse_args()
    llm = GatewayLLM()
    results = {"model": llm.model, "why": eval_why(llm), "memory": eval_memory(llm),
               "cache": eval_cache(llm)}
    if not args.skip_explanations:
        results["explanations"] = eval_explanations(llm)
    (ROOT / "evaluation" / "runs").mkdir(parents=True, exist_ok=True)
    (ROOT / "evaluation" / "runs" / "pipeline.json").write_text(json.dumps(results, indent=2))
    write_report(results)


def write_report(r: dict) -> None:
    why, mem, cache = r["why"], r["memory"], r["cache"]
    lines = ["# Pipeline evaluation", "", f"Model: `{r['model']}` via the inference-gateway. "
             "Generated by `scripts/eval_pipeline.py`.", "",
             "## Diagnostic (\"why\") questions", "",
             f"Planted driver found: **{sum(w['driver_found'] for w in why)}/{len(why)}**; "
             f"named in the written explanation: {sum(w['explanation_names_driver'] for w in why)}"
             f"/{len(why)}.", "",
             "| Question | Spec the model extracted | Driver found | Top finding |",
             "|---|---|---|---|"]
    for w in why:
        lines.append(f"| {w['question']} | {w['spec']} | {'yes' if w['driver_found'] else 'no'} "
                     f"({w['where']}) | {w['top_finding']} |")
    lines += ["", "## Conversation memory (follow-ups)", "",
              f"Follow-up answered correctly: **{sum(m['correct'] for m in mem)}/{len(mem)}**", "",
              "| Turns | Rewritten as | Correct |", "|---|---|---|"]
    for m in mem:
        lines.append(f"| {' → '.join(m['turns'])} | {m['rewritten']} | "
                     f"{'yes' if m['correct'] else 'no'} |")
    lines += ["", "## Caching", "",
              f"{cache['n']} questions asked cold, then again: median {cache['cold_p50_ms']:,} ms "
              f"cold vs {cache['warm_p50_ms']} ms from the answer cache (hit rate "
              f"{cache['warm_hit_rate']:.0%}). Upper-cased, re-spaced variants without the "
              f"question mark hit {cache['case_and_punctuation_variant_hit_rate']:.0%} "
              "of the time. "
              "Genuinely re-worded questions do not hit the answer cache, but share the result "
              "cache when they produce the same SQL."]
    if "explanations" in r:
        e = r["explanations"]
        lines += ["", "## Explanations and latency", "",
                  f"{e['answered']} of {e['asked']} golden SQL questions answered end to end. "
                  f"The grounding check flagged a number not present in the data in "
                  f"**{e['flagged_ungrounded']}** answer(s).", "",
                  f"End-to-end latency: median {e['total_p50_ms']:,} ms, "
                  f"p95 {e['total_p95_ms']:,} ms.",
                  "", "| Stage | Median ms |", "|---|---|"]
        for k, v in e["stage_p50_ms"].items():
            lines.append(f"| {k} | {v:,} |")
        if e["flagged"]:
            lines += ["", "Flagged answers:", ""]
            lines += [f"- `{f['id']}`: {f['warning']}" for f in e["flagged"]]
    out = ROOT / "docs" / "results" / "pipeline.md"
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
