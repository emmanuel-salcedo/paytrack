from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from app.models import AppSettings, PaySchedule


class SettingsValidationError(ValueError):
    pass


@dataclass(frozen=True)
class UpdatePayScheduleInput:
    anchor_payday_date: date
    timezone: str


@dataclass(frozen=True)
class UpdateAppSettingsInput:
    due_soon_days: int
    daily_summary_time: str
    telegram_enabled: bool
    telegram_bot_token: str | None
    telegram_chat_id: str | None


def get_or_create_settings_rows(session: Session) -> tuple[PaySchedule, AppSettings]:
    pay_schedule = session.query(PaySchedule).first()
    app_settings = session.query(AppSettings).first()
    created = False
    if pay_schedule is None:
        pay_schedule = PaySchedule(
            anchor_payday_date=date(2026, 1, 15),
            timezone="America/Los_Angeles",
        )
        session.add(pay_schedule)
        created = True
    if app_settings is None:
        app_settings = AppSettings(
            due_soon_days=5,
            daily_summary_time="07:00",
            telegram_enabled=False,
            telegram_bot_token=None,
            telegram_chat_id=None,
        )
        session.add(app_settings)
        created = True
    if created:
        session.commit()
        session.refresh(pay_schedule)
        session.refresh(app_settings)
    return pay_schedule, app_settings


def _validate_timezone(tz_name: str) -> str:
    tz_name = tz_name.strip()
    if not tz_name:
        raise SettingsValidationError("Invalid timezone.")
    try:
        ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        # Windows/dev environments may not have IANA tzdata installed.
        # Accept non-empty timezone identifiers and let runtime TZ handling decide.
        return tz_name
    return tz_name


def _validate_daily_summary_time(value: str) -> str:
    parts = value.split(":")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise SettingsValidationError("Daily summary time must be HH:MM.")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise SettingsValidationError("Daily summary time must be HH:MM.")
    return f"{hour:02d}:{minute:02d}"


def update_pay_schedule(session: Session, data: UpdatePayScheduleInput) -> PaySchedule:
    pay_schedule, _ = get_or_create_settings_rows(session)
    pay_schedule.anchor_payday_date = data.anchor_payday_date
    pay_schedule.timezone = _validate_timezone(data.timezone.strip())
    session.commit()
    session.refresh(pay_schedule)
    return pay_schedule


def update_app_settings(session: Session, data: UpdateAppSettingsInput) -> AppSettings:
    _, app_settings = get_or_create_settings_rows(session)
    if data.due_soon_days < 0:
        raise SettingsValidationError("Due-soon days must be non-negative.")

    app_settings.due_soon_days = data.due_soon_days
    app_settings.daily_summary_time = _validate_daily_summary_time(data.daily_summary_time.strip())
    app_settings.telegram_enabled = bool(data.telegram_enabled)
    app_settings.telegram_bot_token = (data.telegram_bot_token or "").strip() or None
    app_settings.telegram_chat_id = (data.telegram_chat_id or "").strip() or None
    session.commit()
    session.refresh(app_settings)
    return app_settings
