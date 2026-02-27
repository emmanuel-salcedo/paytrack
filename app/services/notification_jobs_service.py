from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
import time as time_module
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

from app.models import AppSettings, Notification, Occurrence, PaySchedule, Payment
from app.services.notifications_service import (
    create_in_app_notification,
    create_notification_log_entry,
    finalize_notification_log_entry,
    try_log_notification_delivery,
)
from app.services.occurrence_generation import try_mark_daily_job_run
from app.services.settings_service import get_or_create_settings_rows
from app.services.telegram_service import TelegramDeliveryError, send_telegram_message


NOTIFICATION_JOBS_JOB_NAME = "run_notification_jobs"
TELEGRAM_SEND_MAX_ATTEMPTS = 3
TELEGRAM_RETRY_SLEEP_SECONDS = 0.25


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
    daily_summary_deferred_before_time: bool
    daily_summary_ready_time: str | None


def _format_money(amount: Decimal | int | float) -> str:
    return f"${Decimal(str(amount)):.2f}"


def _escape_md_v2(value: str) -> str:
    escaped = value
    for ch in ("\\", "_", "*", "[", "]", "(", ")", "~", "`", ">", "#", "+", "-", "=", "|", "{", "}", ".", "!"):
        escaped = escaped.replace(ch, f"\\{ch}")
    return escaped


def _format_occurrence_group_lines(rows: list[tuple[Occurrence, Payment]]) -> list[str]:
    grouped: dict[date, list[tuple[Occurrence, Payment]]] = {}
    for occ, pay in rows:
        grouped.setdefault(occ.due_date, []).append((occ, pay))

    lines: list[str] = []
    for due in sorted(grouped):
        lines.append(f"*{_escape_md_v2(due.isoformat())}*")
        for occ, pay in grouped[due]:
            lines.append(
                f"- {_escape_md_v2(pay.name)} : {_escape_md_v2(_format_money(Decimal(str(occ.expected_amount))))}"
            )
    return lines


def _build_due_soon_telegram_text(*, rows: list[tuple[Occurrence, Payment]], due_soon_end: date) -> str:
    total = sum((Decimal(str(occ.expected_amount)) for occ, _ in rows), start=Decimal("0"))
    header = (
        f"*Due Soon* \\({len(rows)} items\\)\n"
        f"Due by *{_escape_md_v2(due_soon_end.isoformat())}* | Total {_escape_md_v2(_format_money(total))}"
    )
    return "\n".join([header, "", *_format_occurrence_group_lines(rows)])


def _build_overdue_telegram_text(*, rows: list[tuple[Occurrence, Payment]]) -> str:
    total = sum((Decimal(str(occ.expected_amount)) for occ, _ in rows), start=Decimal("0"))
    header = f"*Overdue* \\({len(rows)} items\\)\nTotal {_escape_md_v2(_format_money(total))}"
    return "\n".join([header, "", *_format_occurrence_group_lines(rows)])


def _build_daily_summary_telegram_text(
    *,
    today: date,
    due_today_rows: list[tuple[Occurrence, Payment]],
    due_soon_rows: list[tuple[Occurrence, Payment]],
    overdue_rows: list[tuple[Occurrence, Payment]],
    unread_count: int,
    timezone_name: str,
) -> str:
    due_today_total = sum((Decimal(str(occ.expected_amount)) for occ, _ in due_today_rows), start=Decimal("0"))
    due_soon_total = sum((Decimal(str(occ.expected_amount)) for occ, _ in due_soon_rows), start=Decimal("0"))
    overdue_total = sum((Decimal(str(occ.expected_amount)) for occ, _ in overdue_rows), start=Decimal("0"))
    lines = [
        f"*Daily Summary* | {_escape_md_v2(today.isoformat())}",
        f"Timezone: `{_escape_md_v2(timezone_name)}`",
        "",
        f"- Due today: *{len(due_today_rows)}* \\({_escape_md_v2(_format_money(due_today_total))}\\)",
        f"- Due soon: *{len(due_soon_rows)}* \\({_escape_md_v2(_format_money(due_soon_total))}\\)",
        f"- Overdue: *{len(overdue_rows)}* \\({_escape_md_v2(_format_money(overdue_total))}\\)",
        f"- Unread notifications: *{unread_count}*",
    ]
    if due_today_rows:
        lines.extend(["", "*Due Today Items*", *_format_occurrence_group_lines(due_today_rows)])
    return "\n".join(lines)


def _resolve_local_now(*, now: datetime | None, timezone_name: str) -> datetime:
    try:
        tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        tz = None

    if now is None:
        return datetime.now(tz) if tz is not None else datetime.now()
    if tz is None:
        return now
    if now.tzinfo is None:
        return now
    return now.astimezone(tz)


def _daily_summary_gate(
    *,
    pay_schedule: PaySchedule,
    app_settings: AppSettings,
    now: datetime | None,
) -> tuple[bool, str | None]:
    try:
        hour_text, minute_text = app_settings.daily_summary_time.split(":")
        ready_time = time(int(hour_text), int(minute_text))
    except (ValueError, AttributeError):
        return True, None
    local_now = _resolve_local_now(now=now, timezone_name=pay_schedule.timezone)
    return local_now.time() >= ready_time, f"{app_settings.daily_summary_time} {pay_schedule.timezone}"


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

    log_row = create_notification_log_entry(
        session,
        type=row_type,
        channel="telegram",
        bucket_date=bucket_date,
        dedup_key=dedup_key,
        status="pending",
    )
    if log_row is None:
        return False, False

    last_error: TelegramDeliveryError | None = None
    for attempt in range(1, TELEGRAM_SEND_MAX_ATTEMPTS + 1):
        try:
            result = send_telegram_message(
                bot_token=app_settings.telegram_bot_token,
                chat_id=app_settings.telegram_chat_id,
                text=text,
                parse_mode="MarkdownV2",
            )
            finalize_notification_log_entry(
                session,
                log_id=log_row.id,
                status="sent",
                telegram_message_id=None if result.message_id is None else str(result.message_id),
            )
            return True, False
        except TelegramDeliveryError as exc:
            last_error = exc
            if not exc.retryable or attempt >= TELEGRAM_SEND_MAX_ATTEMPTS:
                break
            time_module.sleep(TELEGRAM_RETRY_SLEEP_SECONDS)
    # Prevent duplicate spam after connectivity/auth failures; an in-app notification is still written.
    finalize_notification_log_entry(
        session,
        log_id=log_row.id,
        status="error",
        error_message=str(last_error) if last_error is not None else "Telegram send failed.",
    )
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


def _run_notification_jobs(
    session: Session,
    *,
    today: date,
    now: datetime | None = None,
    force_daily_summary: bool = False,
) -> NotificationJobsRunResult:
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
            text=_build_due_soon_telegram_text(rows=due_soon_rows, due_soon_end=due_soon_end),
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
            text=_build_overdue_telegram_text(rows=overdue_rows),
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
    daily_summary_allowed, daily_summary_ready_time = _daily_summary_gate(
        pay_schedule=pay_schedule,
        app_settings=app_settings,
        now=now,
    )
    daily_summary_deferred_before_time = not daily_summary_allowed and not force_daily_summary
    if daily_summary_allowed or force_daily_summary:
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
            text=_build_daily_summary_telegram_text(
                today=today,
                due_today_rows=due_today_rows,
                due_soon_rows=due_soon_rows,
                overdue_rows=overdue_rows,
                unread_count=unread_count,
                timezone_name=pay_schedule.timezone,
            ),
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
        daily_summary_deferred_before_time=daily_summary_deferred_before_time,
        daily_summary_ready_time=daily_summary_ready_time,
    )


def run_notification_jobs_once_per_day(
    session: Session,
    *,
    today: date,
    now: datetime | None = None,
) -> NotificationJobsRunResult:
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
            daily_summary_deferred_before_time=False,
            daily_summary_ready_time=None,
        )
    return _run_notification_jobs(session, today=today, now=now)


def run_notification_jobs_once_per_day_in_session_if_ready(
    session: Session,
    *,
    today: date,
    now: datetime | None = None,
) -> NotificationJobsRunResult | None:
    inspector = inspect(session.bind)
    tables = set(inspector.get_table_names())
    required = {"job_runs", "notifications", "notification_log", "occurrences", "payments", "pay_schedule", "app_settings"}
    if not required.issubset(tables):
        return None
    return run_notification_jobs_once_per_day(session, today=today, now=now)


def run_notification_jobs_now_if_ready(
    session: Session,
    *,
    today: date,
    now: datetime | None = None,
    force_daily_summary: bool = False,
) -> NotificationJobsRunResult | None:
    inspector = inspect(session.bind)
    tables = set(inspector.get_table_names())
    required = {"notifications", "notification_log", "occurrences", "payments", "pay_schedule", "app_settings"}
    if not required.issubset(tables):
        return None
    return _run_notification_jobs(session, today=today, now=now, force_daily_summary=force_daily_summary)
