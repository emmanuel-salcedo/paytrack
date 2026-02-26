from __future__ import annotations

from datetime import date

from sqlalchemy import Boolean, Date, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class PaySchedule(TimestampMixin, Base):
    __tablename__ = "pay_schedule"

    id: Mapped[int] = mapped_column(primary_key=True)
    anchor_payday_date: Mapped[date] = mapped_column(Date, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="America/Los_Angeles")


class AppSettings(TimestampMixin, Base):
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    due_soon_days: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    daily_summary_time: Mapped[str] = mapped_column(String(5), nullable=False, default="07:00")
    telegram_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    telegram_bot_token: Mapped[str | None] = mapped_column(String(255), nullable=True)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
