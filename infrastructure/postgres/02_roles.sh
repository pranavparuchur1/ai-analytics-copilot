#!/usr/bin/env bash
# Creates the least-privilege role the copilot connects as. Runs once at container init,
# after 01_schema.sql. The password comes from the container environment (.env).
#
# Defence in depth: even if a harmful statement got past the SQL validator in
# src/analytics_copilot/safety/, this role can only SELECT from the retail tables, cannot
# see PII columns or the hr schema, and every statement is cut off after a timeout.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
     -v ro_password="$COPILOT_RO_PASSWORD" -v db="$POSTGRES_DB" <<'SQL'
CREATE ROLE copilot_ro LOGIN PASSWORD :'ro_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION CONNECTION LIMIT 20;

-- Session defaults. These are guard rails, not the security boundary: a session may
-- change its own settings. The boundary is the privilege list below.
ALTER ROLE copilot_ro SET default_transaction_read_only = on;
ALTER ROLE copilot_ro SET statement_timeout = '5s';
ALTER ROLE copilot_ro SET idle_in_transaction_session_timeout = '10s';
ALTER ROLE copilot_ro SET search_path = retail;

REVOKE ALL ON DATABASE :"db" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"db" TO copilot_ro;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON SCHEMA hr FROM PUBLIC;

GRANT USAGE ON SCHEMA retail TO copilot_ro;
GRANT SELECT ON retail.stores, retail.products, retail.orders, retail.order_items,
                retail.returns, retail.marketing_spend, retail.stockouts TO copilot_ro;
-- Column-level grant: names, email and phone stay invisible.
GRANT SELECT (customer_id, city, state, signup_date, segment, loyalty_tier)
    ON retail.customers TO copilot_ro;
SQL
