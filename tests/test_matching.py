from datetime import date, datetime
from decimal import Decimal

import pandas as pd

from analytics_copilot.eval.matching import results_match


def df(rows, cols):
    return pd.DataFrame(rows, columns=cols)


def test_column_names_ignored_and_extra_columns_allowed():
    gold = df([["Tent", 10.0]], ["product_name", "revenue"])
    pred = df([[1, "Tent", 10.004]], ["rank", "name", "rev"])
    assert results_match(gold, pred)


def test_row_count_must_match():
    assert not results_match(df([[1], [2]], ["x"]), df([[1]], ["x"]))


def test_unordered_by_default_but_ordered_when_asked():
    gold = df([["a", 2], ["b", 1]], ["k", "v"])
    pred = df([["b", 1], ["a", 2]], ["k", "v"])
    assert results_match(gold, pred)
    assert not results_match(gold, pred, order_matters=True)


def test_rows_must_pair_up():
    # Each column matches on its own, but the pairs are swapped.
    gold = df([["a", 1], ["b", 2]], ["k", "v"])
    pred = df([["a", 2], ["b", 1]], ["k", "v"])
    assert not results_match(gold, pred)


def test_fraction_vs_percentage_and_decimal():
    assert results_match(df([[Decimal("0.2512")]], ["share"]), df([[25.12]], ["pct"]))


def test_midnight_timestamp_equals_date():
    gold = df([[datetime(2026, 9, 1), 5]], ["month", "n"])
    pred = df([[date(2026, 9, 1), 5]], ["m", "n"])
    assert results_match(gold, pred)


def test_strings_case_insensitive():
    assert results_match(df([["CA"]], ["s"]), df([["ca"]], ["state"]))


def test_wrong_value_fails():
    assert not results_match(df([[100.0]], ["x"]), df([[101.0]], ["x"]))


def test_wide_answer_to_comparison_question():
    gold = df([["CA", 1893904.1], ["TX", 1051393.18]], ["ship_state", "revenue"])
    pred = df([[1893904.1, 1051393.18]], ["california_revenue", "texas_revenue"])
    assert results_match(gold, pred)
    assert not results_match(gold, df([[1893904.1, 1.0]], ["a", "b"]))


def test_year_as_number_or_truncated_date():
    gold = df([[2024.0, 1.0], [2025.0, 2.0]], ["year", "v"])
    pred = df([["2025-01-01T00:00:00", 2.0], ["2024-01-01T00:00:00", 1.0]], ["year", "v"])
    assert results_match(gold, pred)
    assert not results_match(gold, df([["2024-02-01", 1.0], ["2025-01-01", 2.0]], ["y", "v"]))
