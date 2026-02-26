from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Notification


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
