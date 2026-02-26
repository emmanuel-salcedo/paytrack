from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from app.models import Occurrence, Payment
from app.models.payments import OCCURRENCE_STATUSES


@dataclass(frozen=True)
class HistoryFilters:
    status: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    q: str | None = None


@dataclass(frozen=True)
class HistoryRow:
    occurrence_id: int
    payment_id: int
    payment_name: str
    due_date: date
    status: str
    expected_amount: Decimal
    amount_paid: Decimal | None
    paid_date: date | None


def _apply_history_filters(stmt: Select, filters: HistoryFilters) -> Select:
    if filters.status:
        if filters.status not in OCCURRENCE_STATUSES:
            raise ValueError(f"Unsupported status filter: {filters.status}")
        stmt = stmt.where(Occurrence.status == filters.status)

    if filters.start_date:
        stmt = stmt.where(
            or_(
                Occurrence.due_date >= filters.start_date,
                Occurrence.paid_date >= filters.start_date,
            )
        )

    if filters.end_date:
        stmt = stmt.where(
            or_(
                Occurrence.due_date <= filters.end_date,
                Occurrence.paid_date <= filters.end_date,
            )
        )

    if filters.q:
        like = f"%{filters.q.strip()}%"
        if like != "%%":
            stmt = stmt.where(Payment.name.ilike(like))

    return stmt


def list_occurrence_history(
    session: Session,
    *,
    filters: HistoryFilters,
    limit: int = 250,
) -> list[HistoryRow]:
    stmt = (
        select(Occurrence, Payment)
        .join(Payment, Payment.id == Occurrence.payment_id)
        .order_by(
            Occurrence.due_date.desc(),
            Occurrence.created_at.desc(),
            Occurrence.id.desc(),
        )
        .limit(limit)
    )
    stmt = _apply_history_filters(stmt, filters)

    rows = session.execute(stmt).all()
    return [
        HistoryRow(
            occurrence_id=occ.id,
            payment_id=payment.id,
            payment_name=payment.name,
            due_date=occ.due_date,
            status=occ.status,
            expected_amount=Decimal(str(occ.expected_amount)),
            amount_paid=None if occ.amount_paid is None else Decimal(str(occ.amount_paid)),
            paid_date=occ.paid_date,
        )
        for occ, payment in rows
    ]

