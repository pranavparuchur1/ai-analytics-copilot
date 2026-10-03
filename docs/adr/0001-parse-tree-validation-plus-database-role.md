# ADR 0001: Validate generated SQL on its parse tree, and run it as a role that can only read

Status: accepted

## Context
The model writes SQL that runs against a real warehouse. A wrong query is an accuracy problem;
a harmful one (writes, DDL, reading personal data, sleeping forever) is a security problem.
The obvious first defence, a keyword blocklist, both misses attacks and blocks harmless queries
(measured in `docs/results/safety.md`: 18/47 harmful statements caught, 2/14 harmless blocked).

## Decision
Two independent layers:
1. **Validator** (`safety/validator.py`): parse with sqlglot, require exactly one query
   statement, walk the whole tree for forbidden node types (so `WITH d AS (DELETE ...)
   SELECT ...` is caught), allow-list tables per role and columns, deny dangerous functions,
   require a real table, and inject a row LIMIT. An EXPLAIN cost ceiling runs before execution.
2. **Database role** `copilot_ro`: SELECT grants only, column-level grants that leave out PII,
   no access to `hr`, read-only transactions and a statement timeout.

## Alternatives considered
- Keyword regex only: rejected, see the numbers above.
- Database role only: it is the real boundary, but its errors ("permission denied for table
  employees") are poor feedback for the self-correction loop, and it does not stop expensive
  queries or table-less fabricated answers.
- Running queries on a read replica: right for production; out of scope on one machine.

## Consequences
- 47/47 harmful statements in the suite blocked, 0/14 harmless ones. The suite was written
  alongside the validator, so this shows the listed attacks are covered, not that all are.
- Validator messages are written to be fed back to the model ("list the columns you need").
- `default_transaction_read_only` is a session default a client can change; the tests prove
  the grants alone still refuse writes.
