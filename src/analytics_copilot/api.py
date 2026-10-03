"""HTTP API and web UI: `uv run acp serve` then open http://localhost:8765."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import psycopg
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from analytics_copilot.config import get_settings
from analytics_copilot.copilot import Copilot
from analytics_copilot.safety.validator import default_policy
from analytics_copilot.schema.catalog import load_catalog

WEB = Path(__file__).parent / "web"
EXAMPLES = [
    "What were our top five products by revenue last quarter?",
    "Why did revenue decrease in California last month?",
    "Show monthly revenue for 2025.",
    "Compare revenue in California and Texas last quarter.",
    "What is the return rate by product category in 2025?",
    "Why were online orders in Texas lower in Q3 2026 than in Q3 2025?",
]


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    session_id: str = Field(default="default", max_length=64)
    role: str = Field(default="analyst", max_length=32)


@lru_cache
def get_copilot() -> Copilot:
    return Copilot()


def create_app() -> FastAPI:
    app = FastAPI(title="Analytics Copilot", version="0.1.0")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(WEB / "index.html")

    @app.post("/api/ask")
    def ask(req: AskRequest) -> dict:
        if req.role not in default_policy().roles:
            raise HTTPException(400, f"unknown role {req.role!r}")
        return get_copilot().ask(req.question, session=req.session_id, role=req.role).to_dict()

    @app.delete("/api/sessions/{session_id}")
    def clear_session(session_id: str) -> dict:
        get_copilot().memory.clear(session_id)
        return {"cleared": session_id}

    @app.get("/api/health")
    def health() -> dict:
        s = get_settings()
        try:
            with psycopg.connect(s.dsn(), connect_timeout=2) as conn:
                conn.execute("SELECT 1")
            db = "ok"
        except psycopg.Error:
            db = "unreachable"
        copilot = get_copilot()
        return {"database": db, "model": copilot.llm.model, "as_of": s.as_of_date.isoformat(),
                "cache": copilot.cache.stats()}

    @app.get("/api/schema")
    def schema() -> dict:
        c = load_catalog()
        return {"roles": sorted(default_policy().roles), "examples": EXAMPLES,
                "tables": [{"name": t.name, "description": t.description,
                            "columns": list(t.columns)} for t in c.tables.values()]}

    return app


app = create_app()
