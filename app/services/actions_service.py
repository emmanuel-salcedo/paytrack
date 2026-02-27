from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.payments import Occurrence, Payment, RECURRENCE_TYPES
from app.services.scheduling_service import (
    PaymentScheduleSpec,
    build_occurrence_seeds_for_payment,
)

logger = logging.getLogger(__name__)


class ActionValidationError(ValueError):
    pass


DEFAULT_PAYMENT_REBUILD_HORIZON_DAYS = 90


@dataclass(frozen=True)
class PaidOffResult:
    payment_id: int
    paid_off_date: date
    canceled_occurrences_count: int


@dataclass(frozen=True)
class ReactivatePaymentResult:
    payment_id: int
    generated_occurrences_count: int
    skipped_existing_count: int


@dataclass(frozen=True)
class UpdatePaymentInput:
    name: str
    expected_amount: Decimal
    initial_due_date: date
    recurrence_type: str
    priority: int | None = None


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


def _build_payment_spec(payment: Payment) -> PaymentScheduleSpec:
    return PaymentScheduleSpec(
        payment_id=payment.id,
        name=payment.name,
        expected_amount=Decimal(str(payment.expected_amount)),
        initial_due_date=payment.initial_due_date,
        recurrence_type=payment.recurrence_type,
        is_active=payment.is_active,
    )


def _insert_regenerated_scheduled_occurrences(
    session: Session,
    *,
    payment: Payment,
    today: date,
    horizon_days: int,
) -> tuple[int, int]:
    range_start = today
    # Keep same inclusive behavior used elsewhere (`today + horizon_days`).
    range_end = today.fromordinal(today.toordinal() + horizon_days)
    seeds = build_occurrence_seeds_for_payment(
        payment=_build_payment_spec(payment),
        range_start=range_start,
        range_end=range_end,
    )

    existing_keys = set(
        session.execute(
            select(Occurrence.payment_id, Occurrence.due_date).where(
                Occurrence.payment_id == payment.id,
                Occurrence.due_date >= range_start,
                Occurrence.due_date <= range_end,
            )
        ).all()
    )
    to_insert = [seed for seed in seeds if (seed.payment_id, seed.due_date) not in existing_keys]
    skipped_existing_count = len(seeds) - len(to_insert)
    for seed in to_insert:
        session.add(
            Occurrence(
                payment_id=seed.payment_id,
                due_date=seed.due_date,
                expected_amount=seed.expected_amount,
                status=seed.status,
            )
        )
    return len(to_insert), skipped_existing_count


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
    logger.info(
        "Occurrence marked paid occurrence_id=%s payment_id=%s amount_paid=%s paid_date=%s",
        occurrence.id,
        occurrence.payment_id,
        occurrence.amount_paid,
        occurrence.paid_date,
    )
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
    logger.info(
        "Occurrence mark-paid undone occurrence_id=%s payment_id=%s",
        occurrence.id,
        occurrence.payment_id,
    )
    return occurrence


def skip_occurrence(session: Session, *, occurrence_id: int) -> Occurrence:
    occurrence = _get_occurrence(session, occurrence_id)
    if occurrence.status != "scheduled":
        raise ActionValidationError(f"Cannot skip occurrence from status '{occurrence.status}'")

    occurrence.status = "skipped"
    session.commit()
    session.refresh(occurrence)
    logger.info(
        "Occurrence skipped occurrence_id=%s payment_id=%s",
        occurrence.id,
        occurrence.payment_id,
    )
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
    logger.info(
        "Payment marked paid off payment_id=%s paid_off_date=%s canceled_occurrences=%s",
        payment.id,
        paid_off_date,
        len(future_scheduled_occurrences),
    )
    return PaidOffResult(
        payment_id=payment.id,
        paid_off_date=paid_off_date,
        canceled_occurrences_count=len(future_scheduled_occurrences),
    )


def reactivate_payment(
    session: Session,
    *,
    payment_id: int,
    today: date,
    horizon_days: int = DEFAULT_PAYMENT_REBUILD_HORIZON_DAYS,
) -> ReactivatePaymentResult:
    payment = _get_payment(session, payment_id)
    payment.is_active = True
    payment.paid_off_date = None

    generated_count, skipped_existing_count = _insert_regenerated_scheduled_occurrences(
        session,
        payment=payment,
        today=today,
        horizon_days=horizon_days,
    )
    session.commit()
    session.refresh(payment)
    logger.info(
        "Payment reactivated payment_id=%s generated=%s skipped_existing=%s",
        payment.id,
        generated_count,
        skipped_existing_count,
    )
    return ReactivatePaymentResult(
        payment_id=payment.id,
        generated_occurrences_count=generated_count,
        skipped_existing_count=skipped_existing_count,
    )


def update_payment_and_rebuild_future_scheduled(
    session: Session,
    *,
    payment_id: int,
    data: UpdatePaymentInput,
    today: date,
    horizon_days: int = DEFAULT_PAYMENT_REBUILD_HORIZON_DAYS,
) -> ReactivatePaymentResult:
    if data.expected_amount < 0:
        raise ActionValidationError("expected_amount must be non-negative")
    if data.recurrence_type not in RECURRENCE_TYPES:
        raise ActionValidationError(f"Unsupported recurrence_type: {data.recurrence_type}")

    payment = _get_payment(session, payment_id)
    payment.name = data.name.strip()
    payment.expected_amount = data.expected_amount
    payment.initial_due_date = data.initial_due_date
    payment.recurrence_type = data.recurrence_type
    payment.priority = data.priority

    future_scheduled_rows = session.scalars(
        select(Occurrence).where(
            Occurrence.payment_id == payment.id,
            Occurrence.status == "scheduled",
            Occurrence.due_date >= today,
        )
    ).all()
    for row in future_scheduled_rows:
        session.delete(row)
    session.flush()

    generated_count, skipped_existing_count = _insert_regenerated_scheduled_occurrences(
        session,
        payment=payment,
        today=today,
        horizon_days=horizon_days,
    )
    session.commit()
    session.refresh(payment)
    logger.info(
        "Payment updated payment_id=%s deleted_future=%s generated=%s skipped_existing=%s",
        payment.id,
        len(future_scheduled_rows),
        generated_count,
        skipped_existing_count,
    )
    return ReactivatePaymentResult(
        payment_id=payment.id,
        generated_occurrences_count=generated_count,
        skipped_existing_count=skipped_existing_count,
    )
