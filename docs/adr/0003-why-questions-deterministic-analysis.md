# ADR 0003: "Why" questions use a model-extracted spec and deterministic analysis

Status: accepted

## Context
"Why did revenue decrease in California last month?" cannot be answered by one SQL query.
Asking a 7B model to plan and run a multi-step investigation would be slow, hard to test and
easy to get subtly wrong.

## Decision
The model does one structured step: turn the question into a spec (metric, filters, current
and baseline periods). Code then runs templated queries through the same validator and
executor: totals; a benchmark (other states, or the same periods a year earlier); a
decomposition per dimension ranked by *excess contribution* (share of the change minus share
of the baseline); and related signals (stockouts that started, marketing spend changes,
promotions that ended). The model then writes the narrative from those facts, phrased as
association rather than cause.

## Alternatives considered
- An agent loop with free-form SQL per step: more general, much less predictable, and every
  step multiplies the chance of a wrong query.
- Ranking segments by raw share of the change: always picks the biggest segment. Excess
  contribution picks the one that changed out of proportion to its size.

## Consequences
- 3 of 4 planted drivers found (`docs/results/pipeline.md`). The miss was an ambiguous period:
  "spring 2026" became March vs February instead of a year-over-year comparison.
- A filter the model invented (`reason = 'arrived late'` for "why did returns go up?") is now
  dropped unless the question mentions its value.
- Only additive metrics (revenue, units, orders, returns) are supported; ratios such as
  average order value would need a different decomposition.
