"""The warehouse catalogue: tables, column types, descriptions, sample values, joins, metrics.

Column types come from parsing the DDL (infrastructure/postgres/01_schema.sql) with sqlglot,
so the catalogue cannot drift from the real schema. Descriptions, synonyms and metrics come
from configs/semantic_layer.yaml. Low-cardinality sample values ('CA', 'Camping', ...) are
read from the database once by `acp catalog build` and committed as
configs/catalog_values.json, because the model writes much better filters when it knows
the exact spelling of the values.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache

import sqlglot
import yaml
from sqlglot import exp

from analytics_copilot.config import REPO_ROOT
from analytics_copilot.safety.validator import default_policy

SCHEMA_SQL = REPO_ROOT / "infrastructure" / "postgres" / "01_schema.sql"
SEMANTIC_YAML = REPO_ROOT / "configs" / "semantic_layer.yaml"
VALUES_JSON = REPO_ROOT / "configs" / "catalog_values.json"

US_STATES = {
    "california": "CA", "texas": "TX", "new york": "NY", "florida": "FL", "washington": "WA",
    "colorado": "CO", "illinois": "IL", "oregon": "OR", "arizona": "AZ",
    "massachusetts": "MA",
}


@dataclass
class Column:
    name: str
    type: str
    description: str = ""
    values: list[str] = field(default_factory=list)


@dataclass
class Table:
    name: str
    description: str
    synonyms: list[str]
    columns: dict[str, Column]


@dataclass
class Metric:
    name: str
    sql: str
    requires: list[str]
    rule: str


@dataclass
class Catalog:
    company: str
    tables: dict[str, Table]
    joins: list[tuple[str, str, str]]
    metrics: dict[str, Metric]
    rules: list[str]

    def join_condition(self, a: str, b: str) -> str | None:
        for x, y, cond in self.joins:
            if {x, y} == {a, b}:
                return cond
        return None


def _ddl_columns() -> dict[str, dict[str, str]]:
    """{table: {column: type}} for the retail schema, parsed from the DDL file."""
    out: dict[str, dict[str, str]] = {}
    for stmt in sqlglot.parse(SCHEMA_SQL.read_text(), read="postgres"):
        if not isinstance(stmt, exp.Create) or stmt.kind != "TABLE":
            continue
        table = stmt.this.this  # Schema -> Table
        if table.db != "retail":
            continue
        cols = {}
        for col in stmt.this.expressions:
            if isinstance(col, exp.ColumnDef):
                cols[col.name] = col.args["kind"].sql(dialect="postgres").lower()
        out[table.name] = cols
    return out


@lru_cache
def load_catalog() -> Catalog:
    semantic = yaml.safe_load(SEMANTIC_YAML.read_text())
    values = json.loads(VALUES_JSON.read_text()) if VALUES_JSON.exists() else {}
    policy = default_policy()
    ddl = _ddl_columns()
    tables = {}
    for name, spec in semantic["tables"].items():
        allowed = policy.tables[name]
        columns = {
            col: Column(col, typ, spec["columns"].get(col, ""), values.get(f"{name}.{col}", []))
            for col, typ in ddl[name].items() if col in allowed  # restricted columns never shown
        }
        tables[name] = Table(name, spec["description"], spec.get("synonyms", []), columns)
    metrics = {k: Metric(k, v["sql"], v["requires"], v["rule"])
               for k, v in semantic["metrics"].items()}
    joins = [tuple(j) for j in semantic["joins"]]
    return Catalog(semantic["company"], tables, joins, metrics, semantic["rules"])


def build_values(dsn: str, max_distinct: int = 200) -> dict[str, list[str]]:
    """Read distinct values of low-cardinality text columns from the database."""
    import psycopg

    catalog = load_catalog()
    out: dict[str, list[str]] = {}
    with psycopg.connect(dsn) as conn:
        for table in catalog.tables.values():
            for col in table.columns.values():
                if not (col.type.startswith("text") or col.type.startswith("char")):
                    continue
                rows = conn.execute(
                    f"SELECT DISTINCT {col.name} FROM retail.{table.name} "
                    f"WHERE {col.name} IS NOT NULL ORDER BY 1 LIMIT {max_distinct + 1}").fetchall()
                if len(rows) <= max_distinct:
                    out[f"{table.name}.{col.name}"] = [r[0] for r in rows]
    VALUES_JSON.write_text(json.dumps(out, indent=1) + "\n")
    load_catalog.cache_clear()
    return out
