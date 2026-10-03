"""Resolve relative date phrases to exact half-open ranges, in code rather than in the model.

Small models are unreliable at calendar arithmetic ("last quarter" on 2026-10-01 came back
as Q2 in early evaluation runs). The prompt therefore carries a table of concrete ranges
computed here, and the model only has to copy the right one.
"""

from __future__ import annotations

from datetime import date


def _month_start(d: date, offset: int = 0) -> date:
    m = d.month - 1 + offset
    return date(d.year + m // 12, m % 12 + 1, 1)


def _quarter_start(d: date, offset: int = 0) -> date:
    q = (d.month - 1) // 3 + offset
    return date(d.year + q // 4, (q % 4) * 3 + 1, 1)


def reference_ranges(today: date) -> list[tuple[str, date, date]]:
    """(phrase, start inclusive, end exclusive) for common relative periods."""
    this_month, this_quarter = _month_start(today), _quarter_start(today)
    last_q = _quarter_start(today, -1)
    q_label = f"Q{(last_q.month - 1) // 3 + 1} {last_q.year}"
    return [
        ("last month", _month_start(today, -1), this_month),
        ("the month before last", _month_start(today, -2), _month_start(today, -1)),
        (f"last quarter ({q_label})", last_q, this_quarter),
        ("the quarter before last", _quarter_start(today, -2), last_q),
        ("this year / year to date", date(today.year, 1, 1), today),
        ("last year", date(today.year - 1, 1, 1), date(today.year, 1, 1)),
        ("last 30 days", date.fromordinal(today.toordinal() - 30), today),
        ("last quarter, one year earlier", _quarter_start(today, -5), _quarter_start(today, -4)),
    ]


def describe(today: date) -> str:
    lines = [f"Today is {today.isoformat()}. Relative periods resolve to these exact ranges "
             "(start inclusive, end exclusive):"]
    for phrase, start, end in reference_ranges(today):
        lines.append(f"- {phrase}: >= '{start.isoformat()}' and < '{end.isoformat()}'")
    return "\n".join(lines)
