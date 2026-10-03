import pytest

from analytics_copilot.intent import classify
from analytics_copilot.schema.retrieval import retrieve


def test_metric_pulls_in_required_tables():
    r = retrieve("What was revenue by product category last quarter?")
    assert {"orders", "order_items", "products"} <= set(r.tables)
    assert "revenue" in r.metrics


def test_join_path_tables_are_added():
    # Returns by region needs returns -> order_items -> orders -> stores.
    r = retrieve("How many returns did each region have?")
    assert {"returns", "order_items", "orders", "stores"} <= set(r.tables)


def test_unrelated_tables_left_out():
    r = retrieve("How much did we spend on social ads in 2025?")
    assert "marketing_spend" in r.tables
    assert "returns" not in r.tables and "customers" not in r.tables


def test_state_name_becomes_a_value_hint():
    r = retrieve("Revenue in California last month")
    assert any("'CA'" in h for h in r.value_hints)


@pytest.mark.parametrize("question,kind", [
    ("Delete all cancelled orders", "refuse_write"),
    ("Update the price of tents", "refuse_write"),
    ("List the email addresses of gold customers", "refuse_restricted"),
    ("What is the salary of each store manager?", "refuse_restricted"),
    ("What's the capital of France?", "out_of_scope"),
    ("Why did revenue drop in September?", "diagnose"),
    ("What drove the spike in returns?", "diagnose"),
    ("Show monthly revenue for 2025", "query"),
])
def test_intent(question, kind):
    assert classify(question, retrieve(question)).kind == kind
