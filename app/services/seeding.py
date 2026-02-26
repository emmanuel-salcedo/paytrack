from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.db import SessionLocal
from app.models import AppSettings, PaySchedule

logger = logging.getLogger(__name__)


def seed_defaults_if_ready() -> None:
    settings = get_settings()

    with SessionLocal() as session:
        try:
            inspector = inspect(session.bind)
            tables = set(inspector.get_table_names())
            required = {"pay_schedule", "app_settings"}
            if not required.issubset(tables):
                logger.info("Skipping default seed; schema not ready yet")
                return

            if session.query(PaySchedule).first() is None:
                session.add(
                    PaySchedule(
                        anchor_payday_date=date(2026, 1, 15),
                        timezone=settings.timezone,
                    )
                )

            if session.query(AppSettings).first() is None:
                session.add(
                    AppSettings(
                        due_soon_days=settings.due_soon_days,
                        daily_summary_time=settings.daily_summary_time,
                        telegram_enabled=False,
                    )
                )

            session.commit()
            logger.info("Default seed check completed")
        except SQLAlchemyError:
            session.rollback()
            logger.exception("Default seeding failed")

