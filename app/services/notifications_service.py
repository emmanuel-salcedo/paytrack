from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Notification, NotificationLog


class NotificationsValidationError(ValueError):
    pass


@dataclass(frozen=True)
class NotificationRowView:
    id: int
    type: str
    title: str
    body: str
    is_read: bool
    created_at: datetime
    read_at: datetime | None


@dataclass(frozen=True)
class NotificationLogRowView:
    id: int
    type: str
    channel: str
    bucket_date: date
    dedup_key: str
    status: str
    telegram_message_id: str | None
    error_message: str | None
    delivered_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class NotificationLogFilters:
    type: str | None = None
    channel: str | None = None
    status: str | None = None
    start_date: date | None = None
    end_date: date | None = None


@dataclass(frozen=True)
class NotificationFilters:
    type: str | None = None
    read_state: str | None = None
    start_date: date | None = None
    end_date: date | None = None


def create_in_app_notification(
    session: Session,
    *,
    type: str,
    title: str,
    body: str,
    occurrence_id: int | None = None,
) -> Notification:
    row = Notification(
        type=type,
        title=title,
        body=body,
        occurrence_id=occurrence_id,
        is_read=False,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def try_log_notification_delivery(
    session: Session,
    *,
    type: str,
    channel: str,
    bucket_date: date,
    dedup_key: str,
    occurrence_id: int | None = None,
) -> bool:
    row = NotificationLog(
        type=type,
        channel=channel,
        bucket_date=bucket_date,
        occurrence_id=occurrence_id,
        dedup_key=dedup_key,
        status="sent",
        delivered_at=datetime.now(),
    )
    session.add(row)
    try:
        session.commit()
        return True
    except IntegrityError:
        session.rollback()
        return False


def create_notification_log_entry(
    session: Session,
    *,
    type: str,
    channel: str,
    bucket_date: date,
    dedup_key: str,
    occurrence_id: int | None = None,
    status: str = "pending",
    telegram_message_id: str | None = None,
) -> NotificationLog | None:
    row = NotificationLog(
        type=type,
        channel=channel,
        bucket_date=bucket_date,
        occurrence_id=occurrence_id,
        dedup_key=dedup_key,
        status=status,
        telegram_message_id=(telegram_message_id or "").strip() or None,
        delivered_at=datetime.now() if status == "sent" else None,
    )
    session.add(row)
    try:
        session.commit()
        session.refresh(row)
        return row
    except IntegrityError:
        session.rollback()
        return None


def finalize_notification_log_entry(
    session: Session,
    *,
    log_id: int,
    status: str,
    error_message: str | None = None,
    telegram_message_id: str | None = None,
) -> NotificationLog | None:
    row = session.get(NotificationLog, log_id)
    if row is None:
        return None
    row.status = status
    row.error_message = (error_message or "").strip() or None
    row.telegram_message_id = (telegram_message_id or "").strip() or None
    row.delivered_at = datetime.now() if status == "sent" else None
    session.commit()
    session.refresh(row)
    return row


def _apply_notification_filters(stmt: Select, filters: NotificationFilters | None) -> Select:
    if filters is None:
        return stmt
    if filters.type:
        stmt = stmt.where(Notification.type == filters.type)
    if filters.read_state == "read":
        stmt = stmt.where(Notification.is_read.is_(True))
    elif filters.read_state == "unread":
        stmt = stmt.where(Notification.is_read.is_(False))
    if filters.start_date:
        stmt = stmt.where(func.date(Notification.created_at) >= filters.start_date)
    if filters.end_date:
        stmt = stmt.where(func.date(Notification.created_at) <= filters.end_date)
    return stmt


def count_notifications(
    session: Session,
    *,
    filters: NotificationFilters | None = None,
) -> int:
    stmt = _apply_notification_filters(select(func.count()).select_from(Notification), filters)
    return int(session.scalar(stmt) or 0)


def list_notifications(
    session: Session,
    *,
    limit: int = 200,
    offset: int = 0,
    sort: str = "newest",
    filters: NotificationFilters | None = None,
) -> list[NotificationRowView]:
    stmt = _apply_notification_filters(select(Notification), filters)
    if sort == "oldest":
        stmt = stmt.order_by(Notification.created_at.asc(), Notification.id.asc())
    elif sort == "unread_first":
        stmt = stmt.order_by(Notification.is_read.asc(), Notification.created_at.desc(), Notification.id.desc())
    else:
        stmt = stmt.order_by(Notification.created_at.desc(), Notification.id.desc())
    rows = session.scalars(stmt.offset(max(offset, 0)).limit(limit)).all()
    return [
        NotificationRowView(
            id=row.id,
            type=row.type,
            title=row.title,
            body=row.body,
            is_read=row.is_read,
            created_at=row.created_at,
            read_at=row.read_at,
        )
        for row in rows
    ]


def count_notification_logs(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(NotificationLog)) or 0)


def _apply_notification_log_filters(stmt: Select, filters: NotificationLogFilters | None) -> Select:
    if filters is None:
        return stmt
    if filters.type:
        stmt = stmt.where(NotificationLog.type == filters.type)
    if filters.channel:
        stmt = stmt.where(NotificationLog.channel == filters.channel)
    if filters.status:
        stmt = stmt.where(NotificationLog.status == filters.status)
    if filters.start_date:
        stmt = stmt.where(NotificationLog.bucket_date >= filters.start_date)
    if filters.end_date:
        stmt = stmt.where(NotificationLog.bucket_date <= filters.end_date)
    return stmt


def count_notification_logs_filtered(
    session: Session,
    *,
    filters: NotificationLogFilters | None = None,
) -> int:
    stmt = _apply_notification_log_filters(select(func.count()).select_from(NotificationLog), filters)
    return int(session.scalar(stmt) or 0)


def list_notification_logs(
    session: Session,
    *,
    limit: int = 50,
    offset: int = 0,
    filters: NotificationLogFilters | None = None,
    sort: str = "newest",
) -> list[NotificationLogRowView]:
    stmt = _apply_notification_log_filters(select(NotificationLog), filters)
    if sort == "oldest":
        ordered = stmt.order_by(NotificationLog.created_at.asc(), NotificationLog.id.asc())
    else:
        ordered = stmt.order_by(NotificationLog.created_at.desc(), NotificationLog.id.desc())
    rows = session.scalars(
        ordered
        .offset(max(offset, 0))
        .limit(limit)
    ).all()
    return [
        NotificationLogRowView(
            id=row.id,
            type=row.type,
            channel=row.channel,
            bucket_date=row.bucket_date,
            dedup_key=row.dedup_key,
            status=row.status,
            telegram_message_id=row.telegram_message_id,
            error_message=row.error_message,
            delivered_at=row.delivered_at,
            created_at=row.created_at,
        )
        for row in rows
    ]


def get_unread_notifications_count(session: Session) -> int:
    count = session.scalar(
        select(func.count()).select_from(Notification).where(Notification.is_read.is_(False))
    )
    return int(count or 0)


def mark_notification_read(session: Session, *, notification_id: int, now: datetime) -> Notification:
    row = session.get(Notification, notification_id)
    if row is None:
        raise NotificationsValidationError(f"Notification {notification_id} not found")
    row.is_read = True
    row.read_at = now
    session.commit()
    session.refresh(row)
    return row


def mark_all_notifications_read(session: Session, *, now: datetime) -> int:
    rows = session.scalars(select(Notification).where(Notification.is_read.is_(False))).all()
    for row in rows:
        row.is_read = True
        row.read_at = now
    session.commit()
    return len(rows)
