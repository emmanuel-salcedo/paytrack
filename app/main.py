from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.logging_config import configure_logging
from app.routes.api import api_router
from app.routes.web import web_router
from app.services.notification_jobs_service import run_notification_jobs_once_per_day_in_session_if_ready
from app.services.occurrence_generation import run_generate_occurrences_once_per_day_if_ready
from app.db import SessionLocal
from app.services.seeding import seed_defaults_if_ready

configure_logging()
logger = logging.getLogger(__name__)


def _app_mode() -> str:
    mode = os.getenv("APP_MODE", "all").strip().lower()
    if mode not in {"all", "api", "web"}:
        return "all"
    return mode


def _startup_jobs_enabled() -> bool:
    raw = os.getenv("RUN_STARTUP_JOBS", "1").strip().lower()
    return raw in {"1", "true", "yes", "on"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting PayTrack application")
    seed_defaults_if_ready()
    if _startup_jobs_enabled():
        guarded_run = run_generate_occurrences_once_per_day_if_ready(today=date.today())
        if guarded_run is not None:
            if guarded_run.ran and guarded_run.generation_result is not None:
                logger.info(
                    "Occurrence generation startup daily run completed",
                    extra={
                        "generated_count": guarded_run.generation_result.generated_count,
                        "skipped_existing_count": guarded_run.generation_result.skipped_existing_count,
                    },
                )
            else:
                logger.info("Occurrence generation startup daily run skipped (already ran today)")
        with SessionLocal() as session:
            notification_run = run_notification_jobs_once_per_day_in_session_if_ready(
                session,
                today=date.today(),
                now=datetime.now(),
            )
            if notification_run is not None:
                if notification_run.ran:
                    logger.info(
                        "Notification jobs startup daily run completed",
                        extra={
                            "daily_summary_created": notification_run.daily_summary_created,
                            "due_soon_created": notification_run.due_soon_created,
                            "overdue_created": notification_run.overdue_created,
                            "telegram_sent": notification_run.telegram_sent,
                            "telegram_errors": notification_run.telegram_errors,
                            "daily_summary_deferred_before_time": notification_run.daily_summary_deferred_before_time,
                        },
                    )
                else:
                    logger.info("Notification jobs startup daily run skipped (already ran today)")
    else:
        logger.info("Startup jobs disabled for this container role")
    yield
    logger.info("Shutting down PayTrack application")


def create_app() -> FastAPI:
    app = FastAPI(title="PayTrack", version="0.1.0", lifespan=lifespan)

    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    mode = _app_mode()
    if mode in {"all", "web"}:
        app.include_router(web_router)
    if mode in {"all", "api"}:
        app.include_router(api_router, prefix="/api")
    return app


app = create_app()
