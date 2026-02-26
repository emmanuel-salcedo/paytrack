from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.logging_config import configure_logging
from app.routes.api import api_router
from app.routes.web import web_router
from app.services.occurrence_generation import run_generate_occurrences_once_per_day_if_ready
from app.services.seeding import seed_defaults_if_ready

configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting PayTrack application")
    seed_defaults_if_ready()
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
    yield
    logger.info("Shutting down PayTrack application")


def create_app() -> FastAPI:
    app = FastAPI(title="PayTrack", version="0.1.0", lifespan=lifespan)

    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    app.include_router(web_router)
    app.include_router(api_router, prefix="/api")
    return app


app = create_app()
