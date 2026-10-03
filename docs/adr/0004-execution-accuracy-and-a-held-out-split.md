# ADR 0004: Measure execution accuracy, and keep a held-out split

Status: accepted

## Context
Comparing generated SQL to reference SQL as text says nothing: many different queries are
correct. Prompt work also overfits quickly when there is only one question set.

## Decision
- Score by running both queries and comparing results (`eval/matching.py`): column names
  ignored, extra columns allowed, numeric tolerance, fractions vs percentages, dates vs
  midnight timestamps, years as numbers or dates, and wide vs long layouts for comparisons.
  Row order only matters for ranked questions.
- 56 SQL questions split dev (19) / test (37), plus 8 refusal and 4 "why" questions. Prompt
  changes were made on dev only; test was run once and that number is reported as the
  held-out result. Changes made after looking at test failures are reported separately.
- Saved runs keep the generated SQL, so a scorer change can be re-applied without the model
  (`scripts/rescore.py`).

## Consequences
- Held-out: 30/37 = 81% (95% interval 66-91%). Full set after fixes: 49/56 = 87.5% (76-94%).
- With this few questions most ablation differences are within noise, and the reports say so.
- The matcher's tolerance rules are judgement calls; each has a unit test and a reason.
