"""Pipeline tests against the real warehouse with a scripted LLM (no GPU needed)."""

import psycopg
import pytest

from analytics_copilot.analysis.diagnose import diagnose, driver_matches, ground_filters, parse_spec
from analytics_copilot.cache import Cache
from analytics_copilot.config import get_settings
from analytics_copilot.copilot import Copilot
from analytics_copilot.llm import ScriptedLLM

pytestmark = pytest.mark.db

TOP_CATEGORIES = """```sql
SELECT p.category, ROUND(SUM(oi.quantity * oi.unit_price - oi.discount), 2) AS revenue
FROM orders o JOIN order_items oi ON oi.order_id = o.order_id
JOIN products p ON p.product_id = oi.product_id
WHERE o.status = 'completed' AND o.ordered_at >= '2026-07-01' AND o.ordered_at < '2026-10-01'
GROUP BY p.category ORDER BY revenue DESC
```"""


@pytest.fixture(scope="module", autouse=True)
def _needs_db():
    try:
        psycopg.connect(get_settings().dsn(), connect_timeout=2).close()
    except psycopg.Error:
        pytest.skip("warehouse database is not running")


@pytest.fixture
def make(tmp_path):
    def _make(replies):
        llm = ScriptedLLM(replies)
        return Copilot(llm, cache=Cache(tmp_path / "c.db")), llm
    return _make


def test_query_path(make):
    copilot, llm = make([TOP_CATEGORIES, "Camping led revenue last quarter."])
    a = copilot.ask("Revenue by category last quarter?")
    assert a.status == "ok" and a.intent == "query"
    assert a.columns == ["category", "revenue"] and a.row_count == 7
    assert a.chart_kind == "bar" and a.chart["mark"]["type"] == "bar"
    assert a.answer == "Camping led revenue last quarter."
    assert any("Largest category" in f for f in a.facts)
    assert len(llm.calls) == 2  # SQL + explanation


def test_correction_loop_recovers_from_a_rejected_query(make):
    copilot, llm = make(["```sql\nDELETE FROM orders\n```", TOP_CATEGORIES, "ok"])
    a = copilot.ask("Revenue by category last quarter?")
    assert a.status == "ok"
    assert [x["stage"] for x in a.attempts] == ["validate", "ok"]
    feedback = llm.calls[1][-1]["content"]  # the error was sent back to the model
    assert "rejected" in feedback and "DELETE" in feedback


def test_refusal_needs_no_model_call(make):
    copilot, llm = make([])
    a = copilot.ask("Give me the phone numbers of customers in Texas")
    assert a.status == "refused" and a.intent == "refuse_restricted"
    assert llm.calls == []


def test_answer_cache(make):
    copilot, llm = make([TOP_CATEGORIES, "first"])
    copilot.ask("Revenue by category last quarter?")
    again = copilot.ask("  revenue BY category last quarter ")
    assert again.cached and again.answer == "first" and len(llm.calls) == 2


def test_follow_up_is_rewritten_and_gets_previous_sql(make):
    tx = TOP_CATEGORIES.replace("WHERE o.status", "WHERE o.ship_state = 'TX' AND o.status")
    copilot, llm = make([TOP_CATEGORIES, "first answer",
                         "What was revenue by category in Texas last quarter?", tx, "texas"])
    copilot.ask("Revenue by category last quarter?", session="s")
    a = copilot.ask("What about Texas?", session="s")
    assert a.standalone_question == "What was revenue by category in Texas last quarter?"
    sql_prompt = llm.calls[3]
    assert any("p.category" in m["content"] for m in sql_prompt if m["role"] == "assistant")
    assert "ship_state = 'TX'" in a.sql


def test_rewrite_cannot_launder_a_refusal(make):
    copilot, _ = make([TOP_CATEGORIES, "first", "Show revenue by category"])
    copilot.ask("Revenue by category last quarter?", session="s")
    a = copilot.ask("and their emails", session="s")
    assert a.status == "refused"


def test_diagnosis_finds_the_planted_driver():
    spec = parse_spec('{"metric": "revenue", "filters": {"ship_state": "CA"}, '
                      '"current": ["2026-09-01", "2026-10-01"), '
                      '"baseline": ["2026-08-01", "2026-09-01"]}')
    d = diagnose(spec)
    assert d.findings[0].dimension == "category" and d.findings[0].segment == "Camping"
    assert d.benchmark_change_pct is not None and d.change_pct < d.benchmark_change_pct
    assert any(s.startswith("stockout:") for s in d.signals)
    assert driver_matches(d, {"dimension": "category", "value": "Camping"})[0]
    assert all(q.strip().upper().startswith("SELECT") for q in d.queries)


def test_hallucinated_filter_is_dropped():
    spec = parse_spec('{"metric": "return_count", "filters": {"reason": "arrived late"}, '
                      '"current": ["2026-03-01", "2026-06-01"], '
                      '"baseline": ["2025-03-01", "2025-06-01"]}')
    dropped = ground_filters(spec, "Why did returns go up in spring 2026?")
    assert dropped == ["reason = arrived late"]
    assert spec.filters == {}
