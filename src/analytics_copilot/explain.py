"""Explanations: what the query does (from its syntax tree) and what the result means (LLM).

Query explanation is deterministic: it is read off the sqlglot tree (tables, joins,
filters, grouping, ordering, limit), so it can never describe a query that was not run.

Answer explanation is written by the model from the computed facts, then checked: every
number in the text must be traceable to the result table or the facts (within rounding).
Untraceable numbers are listed as warnings instead of being silently trusted.
"""

from __future__ import annotations

import re

import pandas as pd
import sqlglot
from sqlglot import exp

from analytics_copilot.llm import LLM

ANSWER_PROMPT = """You are a data analyst at an outdoor-gear retailer. Answer the question in
2-4 sentences of plain English for a business reader, using ONLY the numbers given below.
Lead with the direct answer. Round sensibly ($1.23M, 23.4%). Do not invent causes or numbers.
Write plain text: no markdown, no bold, no headings. A short list is fine as plain lines.
If there are data-quality warnings that affect the answer, mention them in one short sentence.

Question: {question}

Result ({n_rows} rows, first {shown} shown):
{table}

Computed facts:
{facts}

Data-quality warnings:
{warnings}"""

WHY_PROMPT = """You are a data analyst at an outdoor-gear retailer. Explain the change the
question asks about in 3-5 sentences for a business reader, using ONLY the evidence below.
State the size of the change, compare it to the benchmark, name the segment that drove most
of it, and mention related evidence (stockouts, marketing, promotions) if it is listed.
This is correlational evidence: say "is associated with" or "coincides with", not "caused".
Write plain text without markdown.

Question: {question}

Evidence:
{facts}"""


def explain_query(sql: str) -> list[str]:
    tree = sqlglot.parse_one(sql, read="postgres")
    steps: list[str] = []
    ctes = [c.alias_or_name for c in tree.find_all(exp.CTE)]
    if ctes:
        steps.append(f"Builds intermediate results: {', '.join(ctes)}")
    tables = sorted({t.name for t in tree.find_all(exp.Table) if t.name and t.name not in ctes})
    if tables:
        steps.append(f"Reads {', '.join(tables)}")
    joins = list(tree.find_all(exp.Join))
    if joins:
        steps.append(f"Joins {len(joins)} time(s) to combine them")
    for where in tree.find_all(exp.Where):
        conds = _split_and(where.this)
        shown = [c.sql(dialect="postgres") for c in conds[:6]]
        steps.append("Keeps rows where " + "; ".join(shown)
                     + (f" (+{len(conds) - 6} more)" if len(conds) > 6 else ""))
        break
    group = tree.args.get("group")
    if group:
        steps.append("Groups by " + ", ".join(e.sql(dialect="postgres") for e in group.expressions))
    aggs = sorted({a.key.upper() for a in tree.find_all(exp.AggFunc)})
    if aggs:
        steps.append("Calculates " + ", ".join(aggs))
    if any(isinstance(n, exp.Window) for n in tree.walk()):
        steps.append("Uses window functions to compare rows (e.g. with the previous period)")
    order = tree.args.get("order")
    if order:
        steps.append("Sorts by " + ", ".join(e.sql(dialect="postgres") for e in order.expressions))
    limit = tree.args.get("limit")
    if limit is not None:
        steps.append(f"Returns at most {limit.expression.sql()} rows")
    return steps


def _split_and(node: exp.Expression) -> list[exp.Expression]:
    if isinstance(node, exp.And):
        return _split_and(node.left) + _split_and(node.right)
    return [node]


def explain_answer(question: str, df: pd.DataFrame, facts: list[str], warnings: list[str],
                   llm: LLM, max_rows: int = 15) -> tuple[str, int]:
    table = df.head(max_rows).to_string(index=False, max_colwidth=40) if not df.empty else "(none)"
    prompt = ANSWER_PROMPT.format(question=question, n_rows=len(df), shown=min(len(df), max_rows),
                                  table=table, facts="\n".join(f"- {f}" for f in facts) or "-",
                                  warnings="\n".join(f"- {w}" for w in warnings) or "none")
    resp = llm.complete([{"role": "user", "content": prompt}], max_tokens=250, temperature=0.0)
    return _plain(resp.text), resp.prompt_tokens + resp.completion_tokens


def explain_diagnosis(question: str, facts: list[str], llm: LLM) -> tuple[str, int]:
    prompt = WHY_PROMPT.format(question=question, facts="\n".join(facts))
    resp = llm.complete([{"role": "user", "content": prompt}], max_tokens=350)
    return _plain(resp.text), resp.prompt_tokens + resp.completion_tokens


def _plain(text: str) -> str:
    """Strip markdown emphasis and headings the model adds despite being asked not to."""
    text = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), text)
    return re.sub(r"^#+\s*", "", text.strip(), flags=re.M)


# --- grounding check ------------------------------------------------------------------------

NUMBER = re.compile(r"(?<![\w.])[-+]?\$?\d(?:[\d,]*\d)?(?:\.\d+)?"
                    r"(?:\s*%|\s*(?:thousand|million|billion)\b|\s?[kKmMbB](?![a-zA-Z]))?")
SCALE_WORDS = {"thousand": 1e3, "million": 1e6, "billion": 1e9}


def _parse(token: str) -> float | None:
    t = token.strip().replace("$", "").replace(",", "").replace(" ", "")
    mult = 1.0
    for word, scale in SCALE_WORDS.items():
        if t.lower().endswith(word):
            t, mult = t[: -len(word)], scale
    if mult == 1.0 and t and t[-1] in "kKmMbB":
        mult = {"k": 1e3, "m": 1e6, "b": 1e9}[t[-1].lower()]
        t = t[:-1]
    t = t.rstrip("%")
    try:
        return float(t) * mult
    except ValueError:
        return None


def _source_numbers(df: pd.DataFrame, facts: list[str]) -> list[float]:
    nums: list[float] = []
    for col in df.columns:
        s = pd.to_numeric(df[col], errors="coerce").dropna()
        nums += [float(v) for v in s.head(200)]
    for f in facts:
        nums += [v for tok in NUMBER.findall(f) if (v := _parse(tok)) is not None]
    return nums


def ungrounded_numbers(text: str, df: pd.DataFrame, facts: list[str]) -> list[str]:
    """Numbers in `text` that match nothing in the result or facts (allowing for rounding)."""
    sources = _source_numbers(df, facts)
    out = []
    for tok in NUMBER.findall(text):
        v = _parse(tok)
        if v is None or abs(v) < 10 and "%" not in tok and "$" not in tok:
            continue  # small counts ("top 5", "3 stores") and years in prose are not claims
        if 1990 <= v <= 2100 and "." not in tok and "," not in tok:
            continue  # a year
        # Signs are dropped in prose ("fell by 23.4%" for -23.4%), so compare magnitudes.
        if not any(_close(abs(v), abs(s)) for s in sources):
            out.append(tok.strip())
    return out


def _close(v: float, s: float) -> bool:
    if s == 0:
        return abs(v) < 0.5
    for scale in (1, 100, 0.01):
        sv = s * scale
        # Writers round: $1.23M for 1,234,567 is 0.4% off; allow 1.5% or half a unit.
        if abs(v - sv) <= max(abs(sv) * 0.015, 0.5 if abs(sv) < 100 else 0):
            return True
    return False
