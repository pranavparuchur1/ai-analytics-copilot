# ADR 0006: Synthetic data with planted effects

Status: accepted

## Context
Public sample databases are either tiny or have no known "story", so there is no way to check
whether a "why did X change?" answer is right. Real company data is not shareable.

## Decision
A seeded generator (`data/generate.py`) builds 330k orders for a fictional retailer with
realistic structure (category seasonality, growth, weekday and holiday effects, store openings,
a price increase) and five planted effects: a California camping stockout, a defective jacket
batch, a promotion, a paused social-media budget in Texas, and data-quality defects.

## Consequences
- Diagnostic and data-quality features can be scored against known truth.
- The data is only as realistic as the generator. Real data has messier failure modes
  (late-arriving rows, schema drift, conflicting definitions) that this does not exercise.
- The generator also produced a defect nobody planted (open stockouts on discontinued
  products), which the warehouse audit found.
