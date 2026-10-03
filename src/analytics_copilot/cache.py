"""Query caching in SQLite, at two levels.

  answers  full answers keyed by (standalone question, role, model, as-of date, catalogue
           version). A repeated question skips the LLM entirely.
  results  query results keyed by the validated SQL. Different wordings that produce the
           same SQL share one database round trip.

The catalogue version is a hash of the semantic layer, policy and sample values, so
changing the schema description or permissions invalidates everything automatically.
Entries expire after a TTL because the warehouse is not static in real life.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import sqlite3
import threading
import time
from pathlib import Path

import pandas as pd

from analytics_copilot.config import REPO_ROOT


def catalog_version() -> str:
    h = hashlib.sha256()
    for p in ("configs/semantic_layer.yaml", "configs/policy.yaml", "configs/catalog_values.json"):
        f = REPO_ROOT / p
        if f.exists():
            h.update(f.read_bytes())
    return h.hexdigest()[:12]


def normalize_question(q: str) -> str:
    q = re.sub(r"\s+", " ", q.strip().lower())
    return q.rstrip(" ?.!")


class Cache:
    def __init__(self, path: Path | None = None, answer_ttl_s: float = 3600,
                 result_ttl_s: float = 600, enabled: bool = True):
        self.enabled = enabled
        self.answer_ttl_s, self.result_ttl_s = answer_ttl_s, result_ttl_s
        path = path or REPO_ROOT / "data" / "cache.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._db.execute("CREATE TABLE IF NOT EXISTS answers "
                             "(key TEXT PRIMARY KEY, payload TEXT, created REAL)")
            self._db.execute("CREATE TABLE IF NOT EXISTS results "
                             "(key TEXT PRIMARY KEY, payload TEXT, created REAL)")
            self._db.commit()
        self.version = catalog_version()
        self.hits = {"answers": 0, "results": 0}
        self.misses = {"answers": 0, "results": 0}

    @staticmethod
    def _hash(*parts: str) -> str:
        return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()

    def answer_key(self, question: str, role: str, model: str, as_of: str) -> str:
        return self._hash(normalize_question(question), role, model, as_of, self.version)

    def _get(self, table: str, key: str, ttl: float) -> str | None:
        if not self.enabled:
            return None
        with self._lock:
            row = self._db.execute(f"SELECT payload, created FROM {table} WHERE key = ?",
                                   (key,)).fetchone()
        if row and time.time() - row[1] < ttl:
            self.hits[table] += 1
            return row[0]
        self.misses[table] += 1
        return None

    def _put(self, table: str, key: str, payload: str) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._db.execute(f"INSERT OR REPLACE INTO {table} VALUES (?, ?, ?)",
                             (key, payload, time.time()))
            self._db.commit()

    def get_answer(self, key: str) -> dict | None:
        raw = self._get("answers", key, self.answer_ttl_s)
        return json.loads(raw) if raw else None

    def put_answer(self, key: str, answer: dict) -> None:
        self._put("answers", key, json.dumps(answer, default=str))

    def get_result(self, sql: str) -> pd.DataFrame | None:
        raw = self._get("results", self._hash(sql, self.version), self.result_ttl_s)
        return pd.read_json(io.StringIO(raw), orient="split") if raw else None

    def put_result(self, sql: str, df: pd.DataFrame) -> None:
        self._put("results", self._hash(sql, self.version), df.to_json(orient="split",
                                                                       date_format="iso"))

    def clear(self) -> None:
        with self._lock:
            self._db.execute("DELETE FROM answers")
            self._db.execute("DELETE FROM results")
            self._db.commit()

    def stats(self) -> dict:
        return {"hits": dict(self.hits), "misses": dict(self.misses), "version": self.version}
