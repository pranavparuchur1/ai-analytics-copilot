"""Compare the parser-based validator with a naive keyword blocklist on the safety suite.

usage: uv run python scripts/safety_report.py   (writes docs/results/safety.md)
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from analytics_copilot.safety.validator import validate

ROOT = Path(__file__).resolve().parents[1]
SUITE = yaml.safe_load((ROOT / "evaluation" / "safety_suite.yaml").read_text())

# The obvious first attempt: block statements containing write/DDL keywords.
KEYWORDS = re.compile(r"\b(drop|delete|update|insert|alter|truncate|create|grant)\b", re.IGNORECASE)


def naive_blocks(sql: str) -> bool:
    return bool(KEYWORDS.search(sql)) or not sql.strip()


def main() -> None:
    rows = []
    for case in SUITE:
        rows.append((case, naive_blocks(case["sql"]), not validate(case["sql"]).ok))
    attacks = [r for r in rows if r[0]["expect"] == "block"]
    benign = [r for r in rows if r[0]["expect"] == "allow"]

    def score(idx: int) -> tuple[int, int]:
        caught = sum(r[idx] for r in attacks)
        false_pos = sum(r[idx] for r in benign)
        return caught, false_pos

    suite = (f"Suite: `evaluation/safety_suite.yaml`: {len(attacks)} harmful statements, "
             f"{len(benign)} harmless ones.")
    lines = ["# Safety layer: parser validator vs keyword blocklist", "", suite, "",
             "| Approach | Harmful blocked | Harmless wrongly blocked |", "|---|---|---|"]
    for name, idx in [("Keyword regex", 1), ("sqlglot validator", 2)]:
        caught, fp = score(idx)
        lines.append(f"| {name} | {caught}/{len(attacks)} | {fp}/{len(benign)} |")
    caveat = ("Caveats: the suite was written alongside the validator, so a perfect score on it "
              "shows the listed attacks are covered, not that every attack is. The keyword list "
              "only targets writes and DDL, so most of its misses are reads of data the copilot "
              "must not see (PII, `hr`, system catalogs) and server functions with side effects. "
              "The database role (tests/test_db_security.py) is the second, independent "
              "boundary.")
    lines += ["", caveat, "", "## Where they disagree", "",
              "| Case | Expected | Regex | Validator |", "|---|---|---|---|"]

    def fmt(blocked: bool) -> str:
        return "block" if blocked else "allow"

    for case, naive, parser in rows:
        if naive != parser:
            lines.append(f"| `{case['id']}` | {case['expect']} | {fmt(naive)} | {fmt(parser)} |")
    out = ROOT / "docs" / "results" / "safety.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
