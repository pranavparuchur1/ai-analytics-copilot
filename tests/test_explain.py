import pandas as pd

from analytics_copilot.explain import explain_query, ungrounded_numbers


def test_query_explanation_lists_the_steps():
    steps = explain_query(
        "SELECT p.category, SUM(oi.quantity) AS units FROM order_items oi "
        "JOIN products p ON p.product_id = oi.product_id WHERE p.category <> 'Snow' "
        "GROUP BY p.category ORDER BY units DESC LIMIT 5")
    text = " | ".join(steps)
    assert "Reads order_items, products" in text
    assert "Keeps rows where p.category <> 'Snow'" in text
    assert "Groups by p.category" in text
    assert "SUM" in text and "at most 5 rows" in text


def test_grounded_numbers_pass_with_rounding():
    df = pd.DataFrame({"state": ["CA", "TX"], "revenue": [1893904.10, 1051393.18]})
    text = "California brought in $1.89M, about 1.8x Texas at $1,051,393 (64.3% of the pair)."
    facts = ["Largest state: CA with 1,893,904 (64.3% of the total 2,945,297)"]
    # "1.8x" is derived, not in the sources; numbers under 10 without % or $ are not checked.
    assert ungrounded_numbers(text, df, facts) == []


def test_invented_number_is_flagged():
    df = pd.DataFrame({"revenue": [2378398.42]})
    assert ungrounded_numbers("Revenue was $3.1M last month.", df, []) == ["$3.1M"]


def test_years_and_small_counts_are_not_claims():
    df = pd.DataFrame({"revenue": [100.0]})
    assert ungrounded_numbers("In 2026 the top 5 products and 3 stores...", df, []) == []
