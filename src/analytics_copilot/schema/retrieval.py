"""Schema retrieval: pick the tables a question needs, so the prompt only carries those.

Lexical scoring over each table's name, synonyms, column names, descriptions and known
values, plus metric triggers ("revenue" needs orders + order_items). Then the join graph
is used to add any table needed to connect the selected ones (asking about revenue by
category pulls in products, and order_items to reach it).

Eight tables would fit in any prompt; retrieval exists because real warehouses have
hundreds. The evaluation measures what it costs or saves here: table recall against the
gold SQL, prompt tokens, and accuracy with and without it.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field

from analytics_copilot.schema.catalog import US_STATES, Catalog, load_catalog

METRIC_WORDS = {
    "revenue": ["revenue", "sales", "sold", "earn", "made", "income", "top product", "best sell"],
    "units_sold": ["units", "quantity"],
    "order_count": ["orders", "order count", "how many orders", "number of orders"],
    "average_order_value": ["average order", "aov", "basket"],
    "gross_margin": ["margin", "profit"],
    "return_rate": ["return rate", "returns", "returned", "refund"],
}
DEFAULT_TABLES = ["orders", "order_items"]


@dataclass
class Retrieval:
    tables: list[str]
    scores: dict[str, float]
    metrics: list[str]
    value_hints: list[str] = field(default_factory=list)  # e.g. "California -> 'CA'"


def _words(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words}


def retrieve(question: str, catalog: Catalog | None = None, threshold: float = 2.0) -> Retrieval:
    catalog = catalog or load_catalog()
    q = question.lower()
    qwords = _words(question)
    scores: dict[str, float] = {}
    hints: list[str] = []

    for table in catalog.tables.values():
        s = 0.0
        if _words(table.name) & qwords:
            s += 3
        for syn in table.synonyms:
            if (" " in syn and syn in q) or _words(syn) <= qwords:
                s += 3
        for col in table.columns.values():
            if _words(col.name.replace("_", " ")) <= qwords:
                s += 1.5
            for v in col.values:
                if len(v) > 2 and re.search(rf"\b{re.escape(v.lower())}\b", q):
                    s += 3
                    hints.append(f"'{v}' is a value of {table.name}.{col.name}")
        scores[table.name] = s

    for name, code in US_STATES.items():
        if re.search(rf"\b{name}\b", q):
            hints.append(f"{name.title()} = '{code}' (orders.ship_state)")
            scores["orders"] = scores.get("orders", 0) + 3

    metrics = [m for m, words in METRIC_WORDS.items() if any(w in q for w in words)]
    selected = {t for t, s in scores.items() if s >= threshold}
    for m in metrics:
        selected.update(catalog.metrics[m].requires)
    if not selected:
        selected = set(DEFAULT_TABLES)
    selected = _connect(selected, catalog)
    ordered = sorted(selected, key=lambda t: (-scores.get(t, 0), t))
    return Retrieval(ordered, scores, metrics, sorted(set(hints)))


def _connect(tables: set[str], catalog: Catalog) -> set[str]:
    """Add the tables on shortest join paths between the selected tables."""
    graph: dict[str, set[str]] = {}
    for a, b, _ in catalog.joins:
        graph.setdefault(a, set()).add(b)
        graph.setdefault(b, set()).add(a)
    tables = set(tables)
    anchor = next(iter(sorted(tables)))
    for target in sorted(tables - {anchor}):
        path = _shortest_path(graph, anchor, target)
        tables.update(path)
    return tables


def _shortest_path(graph: dict[str, set[str]], start: str, goal: str) -> list[str]:
    prev = {start: None}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        if node == goal:
            break
        for nxt in sorted(graph.get(node, ())):
            if nxt not in prev:
                prev[nxt] = node
                queue.append(nxt)
    if goal not in prev:
        return [goal]
    path, node = [], goal
    while node is not None:
        path.append(node)
        node = prev[node]
    return path
