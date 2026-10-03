# ADR 0005: A small semantic layer and lexical schema retrieval

Status: accepted

## Context
The model needs tables, columns, join keys, metric definitions ("revenue" counts only
completed orders) and exact value spellings ('CA', 'in_store'). Real warehouses have far more
tables than fit in a prompt, so the copilot selects which tables to show.

## Decision
`configs/semantic_layer.yaml` holds descriptions, synonyms, joins, metrics and rules; column
types are parsed from the DDL; sample values are read from the database. Retrieval scores
tables lexically (names, synonyms, columns, values, metric words) and adds the tables on the
join path between the ones it selected.

## Alternatives considered
- Embedding-based retrieval: needs an embedding model and an index for 8 tables. Lexical
  scoring with synonyms already finds every needed table in all 56 questions.
- Always sending the whole schema: measured as equally accurate here (49/56 either way) at
  1.8x the prompt tokens (2,893 vs 1,594). It would not scale to a real warehouse.

## Consequences
- Business definitions written only in a table description can be ignored by the model:
  "physical stores" was still read as channel = 'retail' in two questions.
- Changing the semantic layer changes the cache key, so stale answers are never served.
