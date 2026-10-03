"""Run every gold query: it must pass the validator, execute, and return rows.

usage: uv run python scripts/check_golden.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from analytics_copilot.safety.executor import execute
from analytics_copilot.safety.validator import validate

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    cases = yaml.safe_load((ROOT / "evaluation" / "golden.yaml").read_text())
    bad = 0
    for c in cases:
        if c.get("kind", "sql") != "sql":
            continue
        v = validate(c["gold_sql"])
        if not v.ok:
            print(f"{c['id']}: INVALID {v.error_text}")
            bad += 1
            continue
        df = execute(v.sql).df
        flag = "EMPTY" if df.empty else ""
        bad += bool(flag)
        preview = df.head(3).to_dict("records")
        print(f"{c['id']} {len(df):>3} rows {flag} {preview}"[:180])
    print(f"{bad} problems")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
