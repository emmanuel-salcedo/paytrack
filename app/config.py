from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    timezone: str
    due_soon_days: int
    daily_summary_time: str
    app_host: str
    app_port: int
    sqlite_busy_timeout_ms: int


def get_settings() -> Settings:
    return Settings(
        database_url=os.getenv("DATABASE_URL", "sqlite:///./paytrack.db"),
        timezone=os.getenv("TZ", "America/Los_Angeles"),
        due_soon_days=int(os.getenv("DUE_SOON_DAYS", "5")),
        daily_summary_time=os.getenv("DAILY_SUMMARY_TIME", "07:00"),
        app_host=os.getenv("APP_HOST", "0.0.0.0"),
        app_port=int(os.getenv("APP_PORT", "8000")),
        sqlite_busy_timeout_ms=int(os.getenv("SQLITE_BUSY_TIMEOUT_MS", "5000")),
    )

