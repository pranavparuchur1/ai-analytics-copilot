"""Settings, read from the environment (and a .env file in the repo root if present)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# The dataset ends on 2026-09-30, and "today" for every question is pinned to the day
# after, so relative phrases like "last month" resolve the same way on every run.
AS_OF_DATE = date(2026, 10, 1)


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


@dataclass(frozen=True)
class Settings:
    db_host: str
    db_port: int
    db_name: str
    admin_user: str
    admin_password: str
    ro_user: str
    ro_password: str
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    as_of_date: date

    def dsn(self, *, admin: bool = False) -> str:
        user, password = (
            (self.admin_user, self.admin_password) if admin else (self.ro_user, self.ro_password)
        )
        return (
            f"host={self.db_host} port={self.db_port} dbname={self.db_name} "
            f"user={user} password={password}"
        )


def get_settings() -> Settings:
    _load_dotenv(REPO_ROOT / ".env")
    env = os.environ.get
    return Settings(
        db_host=env("WAREHOUSE_HOST", "localhost"),
        db_port=int(env("WAREHOUSE_PORT", "5433")),
        db_name=env("WAREHOUSE_DB", "warehouse"),
        admin_user=env("WAREHOUSE_ADMIN_USER", "warehouse_admin"),
        admin_password=env("WAREHOUSE_ADMIN_PASSWORD", "admin_dev_only"),
        ro_user="copilot_ro",
        ro_password=env("COPILOT_RO_PASSWORD", "copilot_ro_dev_only"),
        llm_base_url=env("LLM_BASE_URL", "http://localhost:8000/v1"),
        llm_api_key=env("LLM_API_KEY", ""),
        llm_model=env("LLM_MODEL", "coder"),
        as_of_date=date.fromisoformat(env("AS_OF_DATE", AS_OF_DATE.isoformat())),
    )
