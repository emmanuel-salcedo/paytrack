from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

from app.models import AppSettings, Notification, Occurrence, PaySchedule, Payment
from app.services.notifications_service import create_in_app_notification, try_log_notification_delivery
from app.services.occurrence_generation import try_mark_daily_job_run
from app.services.settings_service import get_or_create_settings_rows
from app.services.telegram_service import TelegramDeliveryError, send_telegram_message


NOTIFICATION_JOBS_JOB_NAME = "run_notification_jobs"


@dataclass(frozen=True)
class NotificationJobsRunResult:
    ran: bool
    job_name: str
    run_date: date
    daily_summary_created: int
    due_soon_created: int
    overdue_created: int
    telegram_sent: int
    telegram_errors: int


def _format_money(amount: Decimal | int | float) -> str:
    return f"${Decimal(str(amount)):.2f}"


def _maybe_send_telegram(
    session: Session,
    *,
    row_type: str,
    dedup_key: str,
    bucket_date: date,
    text: str,
    app_settings: AppSettings,
) -> tuple[bool, bool]:
    if not app_settings.telegram_enabled:
        return False, False
    if not app_settings.telegram_bot_token or not app_settings.telegram_chat_id:
        return False, False

    if not try_log_notification_delivery(
        session,
        type=row_type,
        channel="telegram",
        bucket_date=bucket_date,
        dedup_key=dedup_key,
    ):
        return False, False

    try:
        send_telegram_message(
            bot_token=app_settings.telegram_bot_token,
            chat_id=app_settings.telegram_chat_id,
            text=text,
        )
        return True, False
    except TelegramDeliveryError:
        # Prevent duplicate spam after connectivity/auth failures; an in-app notification is still written.
        return False, True


def _create_in_app_if_new(
    session: Session,
    *,
    row_type: str,
    dedup_key: str,
    bucket_date: date,
    title: str,
    body: str,
    occurrence_id: int | None = None,
) -> bool:
    if not try_log_notification_delivery(
        session,
        type=row_type,
        channel="in_app",
        bucket_date=bucket_date,
        dedup_key=dedup_key,
        occurrence_id=occurrence_id,
    ):
        return False
    create_in_app_notification(
        session,
        type=row_type,
        title=title,
        body=body,
        occurrence_id=occurrence_id,
    )
    return True


def _run_notification_jobs(session: Session, *, today: date) -> NotificationJobsRunResult:
    pay_schedule, app_settings = get_or_create_settings_rows(session)

    telegram_sent = 0
    telegram_errors = 0
    daily_summary_created = 0
    due_soon_created = 0
    overdue_created = 0

    due_soon_end = today + timedelta(days=max(app_settings.due_soon_days, 0))
    scheduled_rows = session.execute(
        select(Occurrence, Payment)
        .join(Payment, Payment.id == Occurrence.payment_id)
        .where(Occurrence.status == "scheduled")
        .order_by(Occurrence.due_date.asc(), Payment.name.asc(), Occurrence.id.asc())
    ).all()

    due_soon_rows = [(occ, pay) for occ, pay in scheduled_rows if today <= occ.due_date <= due_soon_end]
    overdue_rows = [(occ, pay) for occ, pay in scheduled_rows if occ.due_date < today]

    if due_soon_rows:
        due_soon_total = sum((Decimal(str(occ.expected_amount)) for occ, _ in due_soon_rows), start=Decimal("0"))
        title = f"Due Soon ({len(due_soon_rows)} items)"
        body = (
            f"{len(due_soon_rows)} scheduled payments due by {due_soon_end.isoformat()} "
            f"totaling {_format_money(due_soon_total)}."
        )
        if _create_in_app_if_new(
            session,
            row_type="due_soon",
            dedup_key="digest",
            bucket_date=today,
            title=title,
            body=body,
        ):
            due_soon_created = 1
        sent, errored = _maybe_send_telegram(
            session,
            row_type="due_soon",
            dedup_key="digest",
            bucket_date=today,
            text=f"{title}\n{body}",
            app_settings=app_settings,
        )
        telegram_sent += int(sent)
        telegram_errors += int(errored)

    if overdue_rows:
        overdue_total = sum((Decimal(str(occ.expected_amount)) for occ, _ in overdue_rows), start=Decimal("0"))
        title = f"Overdue ({len(overdue_rows)} items)"
        body = f"{len(overdue_rows)} scheduled payments are overdue totaling {_format_money(overdue_total)}."
        if _create_in_app_if_new(
            session,
            row_type="overdue",
            dedup_key="digest",
            bucket_date=today,
            title=title,
            body=body,
        ):
            overdue_created = 1
        sent, errored = _maybe_send_telegram(
            session,
            row_type="overdue",
            dedup_key="digest",
            bucket_date=today,
            text=f"{title}\n{body}",
            app_settings=app_settings,
        )
        telegram_sent += int(sent)
        telegram_errors += int(errored)

    # Daily summary includes unread count + scheduled pressure snapshot for today.
    unread_count = int(
        session.scalar(
            select(func.count()).select_from(Notification).where(Notification.is_read.is_(False))
        )
        or 0
    )
    due_today_rows = [(occ, pay) for occ, pay in scheduled_rows if occ.due_date == today]
    due_today_total = sum((Decimal(str(occ.expected_amount)) for occ, _ in due_today_rows), start=Decimal("0"))
    summary_title = "Daily Summary"
    summary_body = (
        f"{len(due_today_rows)} payments due today totaling {_format_money(due_today_total)}. "
        f"Unread notifications: {unread_count}. Timezone: {pay_schedule.timezone}."
    )
    if _create_in_app_if_new(
        session,
        row_type="daily_summary",
        dedup_key="daily",
        bucket_date=today,
        title=summary_title,
        body=summary_body,
    ):
        daily_summary_created = 1
    sent, errored = _maybe_send_telegram(
        session,
        row_type="daily_summary",
        dedup_key="daily",
        bucket_date=today,
        text=f"{summary_title}\n{summary_body}",
        app_settings=app_settings,
    )
    telegram_sent += int(sent)
    telegram_errors += int(errored)

    return NotificationJobsRunResult(
        ran=True,
        job_name=NOTIFICATION_JOBS_JOB_NAME,
        run_date=today,
        daily_summary_created=daily_summary_created,
        due_soon_created=due_soon_created,
        overdue_created=overdue_created,
        telegram_sent=telegram_sent,
        telegram_errors=telegram_errors,
    )


def run_notification_jobs_once_per_day(session: Session, *, today: date) -> NotificationJobsRunResult:
    if not try_mark_daily_job_run(session, job_name=NOTIFICATION_JOBS_JOB_NAME, run_date=today):
        return NotificationJobsRunResult(
            ran=False,
            job_name=NOTIFICATION_JOBS_JOB_NAME,
            run_date=today,
            daily_summary_created=0,
            due_soon_created=0,
            overdue_created=0,
            telegram_sent=0,
            telegram_errors=0,
        )
    return _run_notification_jobs(session, today=today)


def run_notification_jobs_once_per_day_in_session_if_ready(
    session: Session,
    *,
    today: date,
) -> NotificationJobsRunResult | None:
    inspector = inspect(session.bind)
    tables = set(inspector.get_table_names())
    required = {"job_runs", "notifications", "notification_log", "occurrences", "payments", "pay_schedule", "app_settings"}
    if not required.issubset(tables):
        return None
    return run_notification_jobs_once_per_day(session, today=today)


def run_notification_jobs_now_if_ready(
    session: Session,
    *,
    today: date,
) -> NotificationJobsRunResult | None:
    inspector = inspect(session.bind)
    tables = set(inspector.get_table_names())
    required = {"notifications", "notification_log", "occurrences", "payments", "pay_schedule", "app_settings"}
    if not required.issubset(tables):
        return None
    return _run_notification_jobs(session, today=today)
