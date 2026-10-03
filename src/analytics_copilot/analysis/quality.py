"""Data-quality checks, at two levels.

Result checks run on every answer and turn into warnings shown next to it (a result that
hit the row limit, contains future dates, nulls, negative money or duplicate rows).

Warehouse checks (`acp dq`) audit the tables themselves for problems that would quietly
distort answers: future-dated orders, zero-quantity lines, duplicate returns, and so on.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from analytics_copilot.analysis.profile import Profile, as_time

MONEY_WORDS = ("revenue", "amount", "price", "spend", "refund", "sales", "cost", "aov")


@dataclass
class Warning_:
    check: str
    message: str


def check_result(df: pd.DataFrame, prof: Profile, *, truncated: bool, today: date,
                 sql: str | None = None) -> list[Warning_]:
    out: list[Warning_] = []
    if df.empty:
        out.append(Warning_("empty", "No rows matched. Check the filters and date range."))
        return out
    if truncated:
        out.append(Warning_("truncated", f"Only the first {len(df)} rows are shown (row limit); "
                            "totals computed from them would be incomplete."))
    if prof.time and not pd.api.types.is_numeric_dtype(df[prof.time]):
        t = as_time(df[prof.time])
        future = int((t >= pd.Timestamp(today)).sum())
        if future:
            out.append(Warning_("future_dates", f"{future} row(s) are dated on or after "
                                f"{today.isoformat()}. The warehouse has known future-dated "
                                "orders (a data-entry problem); consider excluding them."))
    for col in df.columns:
        nulls = int(df[col].isna().sum())
        if nulls and len(df) > 1:
            out.append(Warning_("nulls", f"{col} is empty in {nulls} of {len(df)} rows."))
    for col in prof.measures:
        if any(w in col.lower() for w in MONEY_WORDS) and (df[col] < 0).any():
            out.append(Warning_("negative_money", f"{col} has negative values."))
    if len(df) > 1 and df.duplicated().any():
        out.append(Warning_("duplicates", f"{int(df.duplicated().sum())} duplicate row(s)."))
    if prof.shape == "breakdown" and len(df) >= 2:
        small = df[prof.measures[0]].astype(float)
        if (small.abs() < 1e-9).all():
            out.append(Warning_("all_zero", "Every value is zero."))
    return out


# --- warehouse audit -----------------------------------------------------------------------

WAREHOUSE_CHECKS: list[tuple[str, str, str]] = [
    ("future_dated_orders", "Orders dated after the end of the data (2026-09-30)",
     "SELECT count(*) FROM orders WHERE ordered_at >= '2026-10-01'"),
    ("zero_quantity_lines", "Order lines with quantity 0",
     "SELECT count(*) FROM order_items WHERE quantity <= 0"),
    ("duplicate_returns", "Order lines returned more than once",
     "SELECT count(*) FROM (SELECT order_item_id FROM returns GROUP BY order_item_id "
     "HAVING count(*) > 1) d"),
    ("customers_missing_state", "Customers with no state",
     "SELECT count(*) FROM customers WHERE state IS NULL"),
    ("cost_above_price", "Products whose unit cost exceeds the list price",
     "SELECT count(*) FROM products WHERE unit_cost > list_price"),
    ("orders_before_signup", "Orders placed before the customer signed up",
     "SELECT count(*) FROM orders o JOIN customers c ON c.customer_id = o.customer_id "
     "WHERE o.ordered_at < c.signup_date"),
    ("returns_before_order", "Returns dated before their order",
     "SELECT count(*) FROM returns r JOIN order_items oi ON oi.order_item_id = r.order_item_id "
     "JOIN orders o ON o.order_id = oi.order_id WHERE r.returned_on < o.ordered_at::date"),
    ("refund_above_line_value", "Refunds larger than the line's value",
     "SELECT count(*) FROM returns r JOIN order_items oi ON oi.order_item_id = r.order_item_id "
     "WHERE r.refund_amount > oi.quantity * oi.unit_price - oi.discount + 0.01"),
    ("orphan_stockouts", "Open stockouts for discontinued products",
     "SELECT count(*) FROM stockouts s JOIN products p ON p.product_id = s.product_id "
     "WHERE s.ended_on IS NULL AND p.discontinued_on IS NOT NULL"),
]


def audit_warehouse(run) -> list[dict]:
    """Run every warehouse check through `run(sql) -> DataFrame`; returns one row per check."""
    results = []
    for name, description, sql in WAREHOUSE_CHECKS:
        count = int(run(sql).iloc[0, 0])
        results.append({"check": name, "description": description, "failing_rows": count,
                        "status": "ok" if count == 0 else "issue"})
    return results
