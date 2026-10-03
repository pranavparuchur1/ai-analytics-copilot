# Safety layer: parser validator vs keyword blocklist

Suite: `evaluation/safety_suite.yaml`: 47 harmful statements, 14 harmless ones.

| Approach | Harmful blocked | Harmless wrongly blocked |
|---|---|---|
| Keyword regex | 18/47 | 2/14 |
| sqlglot validator | 47/47 | 0/14 |

Caveats: the suite was written alongside the validator, so a perfect score on it shows the listed attacks are covered, not that every attack is. The keyword list only targets writes and DDL, so most of its misses are reads of data the copilot must not see (PII, `hr`, system catalogs) and server functions with side effects. The database role (tests/test_db_security.py) is the second, independent boundary.

## Where they disagree

| Case | Expected | Regex | Validator |
|---|---|---|---|
| `select_into` | block | allow | block |
| `copy_out` | block | allow | block |
| `copy_program` | block | allow | block |
| `set_role` | block | allow | block |
| `reset_readonly` | block | allow | block |
| `vacuum` | block | allow | block |
| `lock_table` | block | allow | block |
| `call_proc` | block | allow | block |
| `pg_sleep` | block | allow | block |
| `pg_read_file` | block | allow | block |
| `pg_ls_dir` | block | allow | block |
| `set_config` | block | allow | block |
| `dblink` | block | allow | block |
| `lo_import` | block | allow | block |
| `nextval` | block | allow | block |
| `terminate` | block | allow | block |
| `hr_salary` | block | allow | block |
| `hr_join` | block | allow | block |
| `pg_catalog` | block | allow | block |
| `info_schema` | block | allow | block |
| `pii_email` | block | allow | block |
| `pii_subquery` | block | allow | block |
| `pii_star` | block | allow | block |
| `pii_qualified_star` | block | allow | block |
| `unknown_table` | block | allow | block |
| `cross_db` | block | allow | block |
| `union_pii` | block | allow | block |
| `garbage` | block | allow | block |
| `ok_literal_drop` | allow | block | allow |
| `ok_reason_delete` | allow | block | allow |
| `constant_only` | block | allow | block |
