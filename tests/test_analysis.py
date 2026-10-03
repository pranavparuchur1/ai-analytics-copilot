from datetime import date

import pandas as pd

from analytics_copilot.analysis.charts import choose_chart
from analytics_copilot.analysis.profile import profile
from analytics_copilot.analysis.quality import check_result
from analytics_copilot.analysis.stats import analyze


def test_profile_roles():
    df = pd.DataFrame({"month": pd.to_datetime(["2026-01-01", "2026-02-01"]),
                       "product_id": [1, 2], "category": ["a", "b"], "revenue": [1.0, 2.0]})
    p = profile(df)
    assert p.time == "month" and p.ids == ["product_id"]
    assert p.dimensions == ["category"] and p.measures == ["revenue"]


def test_year_number_is_time():
    p = profile(pd.DataFrame({"year": [2024.0, 2025.0], "revenue": [1.0, 2.0]}))
    assert p.time == "year" and p.shape == "time_series"


def test_chart_choice():
    ts = pd.DataFrame({"month": pd.date_range("2026-01-01", periods=6, freq="MS"),
                       "revenue": range(6)})
    assert choose_chart(ts, profile(ts)).kind == "line"
    bars = pd.DataFrame({"category": list("abc"), "revenue": [3.0, 2.0, 1.0]})
    assert choose_chart(bars, profile(bars)).kind == "bar"
    one = pd.DataFrame({"total": [5.0]})
    assert choose_chart(one, profile(one)).kind == "none"
    scatter = pd.DataFrame({"spend": [1.0, 2.0, 3.0], "revenue": [2.0, 4.0, 7.0]})
    assert choose_chart(scatter, profile(scatter)).kind == "scatter"


def test_time_series_facts_find_the_anomaly():
    y = [100.0] * 11 + [400.0]
    df = pd.DataFrame({"month": pd.date_range("2025-01-01", periods=12, freq="MS"), "v": y})
    facts = analyze(df, profile(df))
    kinds = {f.kind for f in facts}
    assert {"range", "last_change", "extremes", "trend", "anomaly"} <= kinds


def test_top_n_results_do_not_claim_shares_of_the_total():
    df = pd.DataFrame({"product": list("abcde"), "revenue": [5.0, 4, 3, 2, 1]})
    text = " ".join(f.text for f in analyze(df, profile(df), partial=True))
    assert "% of" not in text and "not known" in text


def test_result_quality_warnings():
    df = pd.DataFrame({"year": pd.to_datetime(["2026-01-01", "2027-01-01"]),
                       "revenue": [10.0, None]})
    w = {x.check for x in check_result(df, profile(df), truncated=True, today=date(2026, 10, 1))}
    assert {"truncated", "future_dates", "nulls"} <= w
