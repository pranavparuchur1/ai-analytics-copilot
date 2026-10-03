"""Second line of defence: the database itself refuses what the validator should have caught.

These tests send SQL straight to the executor, bypassing the validator on purpose.
"""

import psycopg
import pytest

from analytics_copilot.config import get_settings
from analytics_copilot.safety.executor import QueryError, execute
from analytics_copilot.safety.validator import validate

pytestmark = pytest.mark.db


@pytest.fixture(scope="module", autouse=True)
def _needs_db():
    try:
        psycopg.connect(get_settings().dsn(), connect_timeout=2).close()
    except psycopg.Error:
        pytest.skip("warehouse database is not running (make db-up && make seed)")


@pytest.mark.parametrize("sql", [
    "INSERT INTO stores (store_id, store_name, channel, opened_on) "
    "VALUES (999, 'x', 'retail', now())",
    "DELETE FROM orders WHERE order_id = 100001",
    "UPDATE products SET list_price = 0",
])
def test_role_cannot_write(sql):
    with pytest.raises(QueryError) as e:
        execute(sql)
    assert e.value.kind in {"permission_denied", "read_only"}


@pytest.mark.parametrize("sql", [
    "DELETE FROM retail.orders WHERE order_id = 100001",
    "DROP TABLE retail.returns",
    "CREATE TABLE retail.evil (x int)",
    "CREATE TABLE public.evil (x int)",
    "TRUNCATE retail.order_items",
])
def test_role_cannot_write_even_outside_read_only_transaction(sql):
    # A raw connection with the read-only default switched off: the session guard rail is
    # gone, and the statement still fails because the role has no write or DDL grants.
    with psycopg.connect(get_settings().dsn(), autocommit=True) as conn:
        conn.execute("SET default_transaction_read_only = off")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(sql)


@pytest.mark.parametrize("sql", [
    "SELECT email FROM customers LIMIT 1",
    "SELECT * FROM customers LIMIT 1",
    "SELECT salary FROM hr.employees LIMIT 1",
])
def test_role_cannot_read_sensitive_data(sql):
    with pytest.raises(QueryError) as e:
        execute(sql)
    assert e.value.kind == "permission_denied"


def test_statement_timeout():
    with pytest.raises(QueryError) as e:
        execute("SELECT pg_sleep(3)", timeout_ms=500)
    assert e.value.kind == "timeout"


def test_plan_cost_guard_blocks_cross_join():
    sql = validate("SELECT count(*) FROM order_items a CROSS JOIN order_items b").sql
    with pytest.raises(QueryError) as e:
        execute(sql)
    assert e.value.kind == "too_expensive"


def test_validated_query_runs():
    v = validate("SELECT category, count(*) AS n FROM products GROUP BY category ORDER BY n DESC")
    result = execute(v.sql, limit=v.limit_applied)
    assert len(result.df) == 7 and list(result.df.columns) == ["category", "n"]
    assert not result.truncated


def test_numeric_columns_become_floats():
    v = validate("SELECT sum(unit_price) AS total FROM order_items")
    df = execute(v.sql).df
    assert df["total"].dtype.kind == "f"
