"""Bulk-load generated tables into PostgreSQL with COPY (as the admin role)."""

from __future__ import annotations

import io

import pandas as pd
import psycopg


def load(dsn: str, tables: dict[str, pd.DataFrame]) -> dict[str, int]:
    """Replace the contents of every table in `tables`. Returns row counts per table."""
    counts = {}
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        # One TRUNCATE for all tables, so foreign keys between them are not an issue.
        cur.execute(f"TRUNCATE {', '.join(tables)} CASCADE")
        for name, df in tables.items():
            buf = io.StringIO()
            df.to_csv(buf, index=False, header=False, na_rep="\\N")
            cols = ", ".join(df.columns)
            with cur.copy(f"COPY {name} ({cols}) FROM STDIN WITH (FORMAT csv, NULL '\\N')") as cp:
                cp.write(buf.getvalue())
            counts[name] = len(df)
        conn.commit()
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("ANALYZE")
    return counts
