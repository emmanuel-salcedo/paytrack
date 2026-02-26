from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.payments import Occurrence, Payment


class ActionValidationError(ValueError):
    pass


@dataclass(frozen=True)
class PaidOffResult:
    payment_id: int
    paid_off_date: date
    canceled_occurrences_count: int


def _get_occurrence(session: Session, occurrence_id: int) -> Occurrence:
    occurrence = session.get(Occurrence, occurrence_id)
    if occurrence is None:
        raise ActionValidationError(f"Occurrence {occurrence_id} not found")
    return occurrence


def _get_payment(session: Session, payment_id: int) -> Payment:
    payment = session.get(Payment, payment_id)
    if payment is None:
        raise ActionValidationError(f"Payment {payment_id} not found")
    return payment


def mark_occurrence_paid(
    session: Session,
    *,
    occurrence_id: int,
    today: date,
    amount_paid: Decimal | None = None,
    paid_date: date | None = None,
) -> Occurrence:
    occurrence = _get_occurrence(session, occurrence_id)

    if occurrence.status not in {"scheduled", "completed"}:
        raise ActionValidationError(f"Cannot mark paid from status '{occurrence.status}'")

    resolved_amount = amount_paid if amount_paid is not None else Decimal(str(occurrence.expected_amount))
    if resolved_amount < 0:
        raise ActionValidationError("amount_paid must be non-negative")

    occurrence.amount_paid = resolved_amount
    occurrence.paid_date = paid_date or today
    occurrence.status = "completed"
    session.commit()
    session.refresh(occurrence)
    return occurrence


def undo_mark_paid(session: Session, *, occurrence_id: int) -> Occurrence:
    occurrence = _get_occurrence(session, occurrence_id)
    if occurrence.status != "completed":
        raise ActionValidationError(f"Cannot undo mark paid from status '{occurrence.status}'")

    occurrence.status = "scheduled"
    occurrence.amount_paid = None
    occurrence.paid_date = None
    session.commit()
    session.refresh(occurrence)
    return occurrence


def skip_occurrence(session: Session, *, occurrence_id: int) -> Occurrence:
    occurrence = _get_occurrence(session, occurrence_id)
    if occurrence.status != "scheduled":
        raise ActionValidationError(f"Cannot skip occurrence from status '{occurrence.status}'")

    occurrence.status = "skipped"
    session.commit()
    session.refresh(occurrence)
    return occurrence


def mark_payment_paid_off(
    session: Session,
    *,
    payment_id: int,
    paid_off_date: date,
) -> PaidOffResult:
    payment = _get_payment(session, payment_id)

    payment.is_active = False
    payment.paid_off_date = paid_off_date

    future_scheduled_occurrences = session.scalars(
        select(Occurrence).where(
            Occurrence.payment_id == payment.id,
            Occurrence.status == "scheduled",
            Occurrence.due_date >= paid_off_date,
        )
    ).all()
    for occurrence in future_scheduled_occurrences:
        occurrence.status = "canceled"

    session.commit()
    session.refresh(payment)
    return PaidOffResult(
        payment_id=payment.id,
        paid_off_date=paid_off_date,
        canceled_occurrences_count=len(future_scheduled_occurrences),
    )

