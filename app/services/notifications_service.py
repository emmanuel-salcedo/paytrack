from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import func, select
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
) -> NotificationLog | None:
    row = NotificationLog(
        type=type,
        channel=channel,
        bucket_date=bucket_date,
        occurrence_id=occurrence_id,
        dedup_key=dedup_key,
        status=status,
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
) -> NotificationLog | None:
    row = session.get(NotificationLog, log_id)
    if row is None:
        return None
    row.status = status
    row.error_message = (error_message or "").strip() or None
    row.delivered_at = datetime.now() if status == "sent" else None
    session.commit()
    session.refresh(row)
    return row


def list_notifications(session: Session, *, limit: int = 200) -> list[NotificationRowView]:
    rows = session.scalars(
        select(Notification).order_by(Notification.created_at.desc(), Notification.id.desc()).limit(limit)
    ).all()
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
