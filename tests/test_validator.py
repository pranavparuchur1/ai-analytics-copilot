from pathlib import Path

import pytest
import yaml

from analytics_copilot.safety.validator import validate

SUITE = yaml.safe_load(
    (Path(__file__).parents[1] / "evaluation" / "safety_suite.yaml").read_text())


@pytest.mark.parametrize("case", SUITE, ids=[c["id"] for c in SUITE])
def test_safety_suite(case):
    result = validate(case["sql"])
    if case["expect"] == "block":
        assert not result.ok, f"should have been blocked: {case['sql']}"
        assert result.violations
    else:
        assert result.ok, result.error_text


def test_limit_is_added_when_missing():
    result = validate("SELECT order_id FROM orders")
    assert result.ok and result.limit_applied == 1000
    assert "LIMIT 1000" in result.sql


def test_limit_is_capped():
    result = validate("SELECT order_id FROM orders LIMIT 50000")
    assert result.limit_applied == 1000 and "LIMIT 1000" in result.sql


def test_smaller_limit_is_kept():
    result = validate("SELECT order_id FROM orders ORDER BY order_id LIMIT 5")
    assert result.limit_applied == 5 and "LIMIT 5" in result.sql


def test_role_permissions():
    assert validate("SELECT count(*) FROM marketing_spend", role="marketing").ok
    denied = validate("SELECT count(*) FROM returns", role="marketing")
    assert not denied.ok and denied.violations[0].code == "permission_denied"


def test_unknown_role():
    assert validate("SELECT 1", role="intern").violations[0].code == "unknown_role"


def test_violation_messages_are_actionable():
    # The correction loop feeds these back to the model, so they must say what to fix.
    result = validate("SELECT * FROM customers")
    assert "list the columns" in result.error_text


def test_tables_reported():
    result = validate("SELECT 1 FROM orders o JOIN order_items oi USING (order_id)")
    assert result.tables == {"orders", "order_items"}
