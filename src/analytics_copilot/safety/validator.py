"""Static validation of generated SQL, before it is sent to the database.

The validator parses the SQL into a syntax tree with sqlglot and checks the tree, rather
than searching the text for bad words. That matters in both directions:

  * `SELECT 'DROP TABLE' AS label` is harmless; a keyword regex would block it.
  * `WITH d AS (DELETE FROM orders RETURNING *) SELECT * FROM d` starts with WITH and
    ends with SELECT, but it deletes rows. The tree contains a Delete node, so it is caught.

Rules, in order: one statement only; it must be a query (SELECT / set operation); no
write, DDL, locking or SELECT INTO anywhere in the tree; only allow-listed tables (per
user role) and columns; no denied functions; no `*` that would expose restricted
columns; finally a LIMIT is added or capped.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import sqlglot
import yaml
from sqlglot import exp
from sqlglot.errors import ParseError

from analytics_copilot.config import REPO_ROOT

# sqlglot logs a warning whenever it falls back to an opaque Command node; we reject those.
logging.getLogger("sqlglot").setLevel(logging.ERROR)

# Node types that must never appear anywhere in a query tree.
FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter,
    exp.TruncateTable, exp.Copy, exp.Grant, exp.Command, exp.Into, exp.Lock, exp.Set,
    exp.Transaction, exp.Commit, exp.Rollback, exp.Use, exp.Pragma, exp.Kill, exp.Execute,
    exp.Analyze, exp.LoadData, exp.Declare,
)


@dataclass(frozen=True)
class Policy:
    default_schema: str
    max_rows: int
    max_sql_chars: int
    max_plan_cost: float
    tables: dict[str, frozenset[str]]
    restricted_columns: frozenset[str]
    roles: dict[str, frozenset[str]]
    table_functions: frozenset[str]
    denied_functions: frozenset[str]

    @classmethod
    def from_yaml(cls, path: Path) -> Policy:
        raw = yaml.safe_load(path.read_text())
        tables = {t: frozenset(cols) for t, cols in raw["tables"].items()}
        roles = {r: frozenset(tables) if v == "*" else frozenset(v) for r, v in raw["roles"].items()}
        return cls(
            default_schema=raw["default_schema"], max_rows=raw["max_rows"],
            max_sql_chars=raw["max_sql_chars"], max_plan_cost=float(raw["max_plan_cost"]),
            tables=tables, restricted_columns=frozenset(raw["restricted_columns"]), roles=roles,
            table_functions=frozenset(raw["table_functions"]),
            denied_functions=frozenset(raw["denied_functions"]),
        )


@lru_cache
def default_policy() -> Policy:
    return Policy.from_yaml(REPO_ROOT / "configs" / "policy.yaml")


@dataclass
class Violation:
    code: str
    message: str

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


@dataclass
class ValidationResult:
    ok: bool
    sql: str | None  # normalised SQL with the row limit applied (None when rejected)
    violations: list[Violation] = field(default_factory=list)
    tables: set[str] = field(default_factory=set)
    limit_applied: int | None = None

    @property
    def error_text(self) -> str:
        return "; ".join(str(v) for v in self.violations)


def validate(sql: str, *, role: str = "analyst", policy: Policy | None = None) -> ValidationResult:
    policy = policy or default_policy()
    sql = sql.strip().rstrip(";").strip()
    if not sql:
        return _reject("empty", "no SQL was produced")
    if len(sql) > policy.max_sql_chars:
        return _reject("too_long", f"SQL is longer than {policy.max_sql_chars} characters")
    if role not in policy.roles:
        return _reject("unknown_role", f"role {role!r} is not defined in the policy")

    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except ParseError as e:
        return _reject("parse_error", _first_line(str(e)))
    if len(statements) != 1:
        return _reject("multiple_statements",
                       f"expected exactly one statement, found {len(statements)}")
    tree = statements[0]
    if not isinstance(tree, exp.Query):
        return _reject("not_a_query", f"only SELECT queries are allowed, got {tree.key.upper()}")

    violations: list[Violation] = []
    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            violations.append(Violation("forbidden_operation",
                                        f"{node.key.upper()} is not allowed in a read-only query"))
    if violations:
        return ValidationResult(ok=False, sql=None, violations=_dedupe(violations))

    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    allowed_tables = policy.roles[role]
    tables: set[str] = set()
    for table in tree.find_all(exp.Table):
        if isinstance(table.this, exp.Func):  # FROM generate_series(...)
            fname = _func_name(table.this)
            if fname not in policy.table_functions:
                violations.append(Violation("denied_function",
                                            f"table function {fname}() is not allowed"))
            continue
        name = table.name.lower()
        schema = (table.db or "").lower()
        if not schema and name in cte_names:
            continue
        if table.catalog or (schema and schema != policy.default_schema):
            violations.append(Violation("unknown_table",
                                        f"{table.sql(dialect='postgres')} is outside the "
                                        f"{policy.default_schema} schema"))
        elif name not in policy.tables:
            violations.append(Violation("unknown_table", f"table {name!r} does not exist or "
                                        "is not available"))
        elif name not in allowed_tables:
            violations.append(Violation("permission_denied",
                                        f"role {role!r} may not query table {name!r}"))
        else:
            tables.add(name)

    for column in tree.find_all(exp.Column):
        if column.name.lower() in policy.restricted_columns:
            violations.append(Violation("restricted_column",
                                        f"column {column.name!r} is restricted (personal or "
                                        "sensitive data)"))

    if "customers" in tables:
        for star in tree.find_all(exp.Star):
            if not isinstance(star.parent, exp.Count):
                violations.append(Violation("star_on_restricted_table",
                                            "SELECT * is not allowed when customers is queried; "
                                            "list the columns you need"))
                break

    for func in tree.find_all(exp.Func):
        fname = _func_name(func)
        if fname.startswith("pg_") or fname in policy.denied_functions:
            violations.append(Violation("denied_function", f"function {fname}() is not allowed"))

    if violations:
        return ValidationResult(ok=False, sql=None, violations=_dedupe(violations), tables=tables)

    limit = _apply_limit(tree, policy.max_rows)
    return ValidationResult(ok=True, sql=tree.sql(dialect="postgres", pretty=True),
                            tables=tables, limit_applied=limit)


def _apply_limit(tree: exp.Query, max_rows: int) -> int:
    current = tree.args.get("limit")
    value = None
    if current is not None:
        literal = current.expression if isinstance(current, exp.Limit) else current
        if isinstance(literal, exp.Literal) and literal.is_int:
            value = int(literal.this)
    if value is None or value > max_rows:
        tree.limit(max_rows, copy=False)
        return max_rows
    return value


def _func_name(func: exp.Func) -> str:
    if isinstance(func, exp.Anonymous):
        return str(func.this).lower()
    # sqlglot calls Postgres' set-returning generate_series "EXPLODING_GENERATE_SERIES".
    return func.sql_name().lower().removeprefix("exploding_")


def _reject(code: str, message: str) -> ValidationResult:
    return ValidationResult(ok=False, sql=None, violations=[Violation(code, message)])


def _dedupe(violations: list[Violation]) -> list[Violation]:
    seen, out = set(), []
    for v in violations:
        if (v.code, v.message) not in seen:
            seen.add((v.code, v.message))
            out.append(v)
    return out


def _first_line(text: str) -> str:
    return text.strip().splitlines()[0] if text.strip() else "could not parse SQL"
