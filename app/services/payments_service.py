from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.payments import Payment, RECURRENCE_TYPES


@dataclass(frozen=True)
class CreatePaymentInput:
    name: str
    expected_amount: Decimal
    initial_due_date: date
    recurrence_type: str
    priority: int | None = None


def list_payments(session: Session, *, include_archived: bool = True) -> list[Payment]:
    stmt = select(Payment)
    if not include_archived:
        stmt = stmt.where(Payment.is_active.is_(True))
    stmt = stmt.order_by(Payment.is_active.desc(), Payment.name.asc())
    return session.scalars(stmt).all()


def create_payment(session: Session, data: CreatePaymentInput) -> Payment:
    if data.recurrence_type not in RECURRENCE_TYPES:
        raise ValueError(f"Unsupported recurrence_type: {data.recurrence_type}")
    if data.expected_amount < 0:
        raise ValueError("expected_amount must be non-negative")

    payment = Payment(
        name=data.name.strip(),
        expected_amount=data.expected_amount,
        initial_due_date=data.initial_due_date,
        recurrence_type=data.recurrence_type,
        priority=data.priority,
        is_active=True,
    )
    session.add(payment)
    session.commit()
    session.refresh(payment)
    return payment
