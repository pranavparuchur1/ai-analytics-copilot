from datetime import date

from analytics_copilot.dates import reference_ranges


def ranges(today):
    return {phrase.split(" (")[0]: (s, e) for phrase, s, e in reference_ranges(today)}


def test_as_of_date():
    r = ranges(date(2026, 10, 1))
    assert r["last month"] == (date(2026, 9, 1), date(2026, 10, 1))
    assert r["last quarter"] == (date(2026, 7, 1), date(2026, 10, 1))
    assert r["last year"] == (date(2025, 1, 1), date(2026, 1, 1))
    assert r["last quarter, one year earlier"] == (date(2025, 7, 1), date(2025, 10, 1))


def test_year_boundary():
    r = ranges(date(2026, 1, 15))
    assert r["last month"] == (date(2025, 12, 1), date(2026, 1, 1))
    assert r["last quarter"] == (date(2025, 10, 1), date(2026, 1, 1))
    assert r["the quarter before last"] == (date(2025, 7, 1), date(2025, 10, 1))
