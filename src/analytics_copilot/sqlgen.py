"""Natural language -> SQL, with validation, execution and a self-correction loop.

    question -> retrieve schema -> prompt -> LLM -> extract SQL -> validate -> execute
                                              ^                      |          |
                                              +---- error feedback --+----------+

A rejected or failing query is sent back to the model together with the exact validator
or database error, up to `max_corrections` times. An empty result gets one retry too,
because it usually means a filter value was misspelled ('California' vs 'CA').
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from analytics_copilot.config import get_settings
from analytics_copilot.dates import describe as describe_dates
from analytics_copilot.llm import LLM
from analytics_copilot.safety.executor import QueryError, QueryResult, execute
from analytics_copilot.safety.validator import validate
from analytics_copilot.schema.catalog import Catalog, load_catalog
from analytics_copilot.schema.retrieval import Retrieval, retrieve

SYSTEM = """You are a careful analytics engineer at {company}
You write PostgreSQL for a read-only warehouse.

{dates}

Answer the user's question with ONE SELECT query. Reply with only the SQL inside a ```sql block.
Use only the tables and columns listed below. Give result columns short, readable aliases.

{schema}

Join conditions:
{joins}

Metric definitions:
{metrics}

Rules:
{rules}
{hints}"""

EXAMPLE_Q = "How many completed online orders were placed in Texas in June 2026?"
EXAMPLE_SQL = """```sql
SELECT COUNT(*) AS online_orders
FROM orders
WHERE status = 'completed'
  AND channel = 'online'
  AND ship_state = 'TX'
  AND ordered_at >= '2026-06-01' AND ordered_at < '2026-07-01';
```"""


@dataclass
class Attempt:
    sql: str | None
    error: str | None
    stage: str  # "validate" | "execute" | "empty" | "ok" | "no_sql"


@dataclass
class SQLOutcome:
    question: str
    sql: str | None
    result: QueryResult | None
    attempts: list[Attempt]
    retrieval: Retrieval
    prompt_tokens: int = 0
    completion_tokens: int = 0
    llm_ms: float = 0.0
    error: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.result is not None

    @property
    def corrections(self) -> int:
        return max(0, len(self.attempts) - 1)


def build_messages(question: str, retrieval: Retrieval, *, catalog: Catalog | None = None,
                   today: date | None = None, context: list[dict] | None = None) -> list[dict]:
    catalog = catalog or load_catalog()
    today = today or get_settings().as_of_date
    tables = retrieval.tables
    schema = "\n\n".join(_table_block(catalog, t) for t in tables)
    joins = "\n".join(f"- {cond}" for a, b, cond in catalog.joins if a in tables and b in tables)
    metrics = "\n".join(f"- {m.name} = {m.sql} ({m.rule})" for m in catalog.metrics.values()
                        if set(m.requires) <= set(tables))
    rules = "\n".join(f"- {r}" for r in catalog.rules)
    hints = ""
    if retrieval.value_hints:
        hints = "\nValues mentioned in the question:\n" + "\n".join(
            f"- {h}" for h in retrieval.value_hints)
    system = SYSTEM.format(company=catalog.company, dates=describe_dates(today), schema=schema,
                           joins=joins or "- (single table)", metrics=metrics or "- (none)",
                           rules=rules, hints=hints)
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": EXAMPLE_Q},
                {"role": "assistant", "content": EXAMPLE_SQL}]
    for turn in context or []:
        messages.append({"role": "user", "content": turn["question"]})
        messages.append({"role": "assistant", "content": f"```sql\n{turn['sql']}\n```"})
    messages.append({"role": "user", "content": question})
    return messages


def _table_block(catalog: Catalog, name: str) -> str:
    table = catalog.tables[name]
    lines = [f"TABLE {name}  -- {table.description}"]
    for col in table.columns.values():
        note = col.description
        if col.values and len(col.values) <= 30:
            note += " Values: " + ", ".join(f"'{v}'" for v in col.values)
        elif col.values:
            note += " e.g. " + ", ".join(f"'{v}'" for v in col.values[:3])
        lines.append(f"  {col.name} {col.type}  -- {note.strip()}")
    return "\n".join(lines)


def extract_sql(text: str) -> str | None:
    blocks = re.findall(r"```(?:sql|postgresql|postgres)?\s*\n?(.*?)```", text, re.S | re.I)
    if blocks:
        return max(blocks, key=len).strip()
    m = re.search(r"\b(WITH|SELECT)\b.*", text, re.S | re.I)
    return m.group(0).strip() if m else None


def generate(question: str, llm: LLM, *, role: str = "analyst", max_corrections: int = 2,
             use_retrieval: bool = True, context: list[dict] | None = None,
             run=execute) -> SQLOutcome:
    catalog = load_catalog()
    if use_retrieval:
        retrieval = retrieve(question, catalog)
    else:
        retrieval = Retrieval(sorted(catalog.tables), {}, [], retrieve(question, catalog).value_hints)
    messages = build_messages(question, retrieval, catalog=catalog, context=context)
    out = SQLOutcome(question=question, sql=None, result=None, attempts=[], retrieval=retrieval)
    retried_empty = False

    for _ in range(max_corrections + 1):
        resp = llm.complete(messages, max_tokens=700)
        out.prompt_tokens += resp.prompt_tokens
        out.completion_tokens += resp.completion_tokens
        out.llm_ms += resp.latency_ms
        sql = extract_sql(resp.text)
        messages.append({"role": "assistant", "content": resp.text})

        if not sql:
            out.attempts.append(Attempt(None, "no SQL in reply", "no_sql"))
            messages.append({"role": "user", "content":
                             "Reply with a single SELECT query inside a ```sql block."})
            continue
        check = validate(sql, role=role)
        if not check.ok:
            out.attempts.append(Attempt(sql, check.error_text, "validate"))
            messages.append({"role": "user", "content":
                             f"That query was rejected: {check.error_text}. "
                             "Write a corrected query using only the listed tables and columns."})
            continue
        try:
            result = run(check.sql, limit=check.limit_applied)
        except QueryError as e:
            out.attempts.append(Attempt(check.sql, f"{e.kind}: {e}", "execute"))
            messages.append({"role": "user", "content":
                             f"PostgreSQL returned an error: {e}. Fix the query."})
            continue
        if result.df.empty and not retried_empty:
            retried_empty = True
            out.attempts.append(Attempt(check.sql, "query returned no rows", "empty"))
            out.sql, out.result = check.sql, result  # keep it in case the retry is no better
            messages.append({"role": "user", "content":
                             "That query returned no rows. Check filter values against the "
                             "listed column values and the date range, then try again. If no "
                             "rows is genuinely correct, return the same query."})
            continue
        out.attempts.append(Attempt(check.sql, None, "ok"))
        out.sql, out.result = check.sql, result
        return out

    if out.result is None:
        out.error = out.attempts[-1].error if out.attempts else "no attempts"
    return out
