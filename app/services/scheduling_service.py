from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.services.date_engine import PayCycle, cycle_for_date, next_cycle
from app.services.recurrence_engine import generate_due_dates


@dataclass(frozen=True)
class PaymentScheduleSpec:
    payment_id: int
    name: str
    expected_amount: Decimal
    initial_due_date: date
    recurrence_type: str
    is_active: bool = True


@dataclass(frozen=True)
class ScheduledOccurrenceSeed:
    payment_id: int
    due_date: date
    expected_amount: Decimal
    status: str = "scheduled"


def get_current_cycle(*, today: date, anchor_payday_date: date) -> PayCycle:
    return cycle_for_date(today, anchor_payday_date)


def get_next_cycle_for_date(*, today: date, anchor_payday_date: date) -> PayCycle:
    return next_cycle(get_current_cycle(today=today, anchor_payday_date=anchor_payday_date))


def build_occurrence_seeds_for_payment(
    *,
    payment: PaymentScheduleSpec,
    range_start: date,
    range_end: date,
) -> list[ScheduledOccurrenceSeed]:
    if not payment.is_active:
        return []

    due_dates = generate_due_dates(
        recurrence_type=payment.recurrence_type,
        initial_due_date=payment.initial_due_date,
        range_start=range_start,
        range_end=range_end,
    )

    return [
        ScheduledOccurrenceSeed(
            payment_id=payment.payment_id,
            due_date=due_date,
            expected_amount=payment.expected_amount,
        )
        for due_date in due_dates
    ]


def build_occurrence_seeds(
    *,
    payments: list[PaymentScheduleSpec],
    range_start: date,
    range_end: date,
) -> list[ScheduledOccurrenceSeed]:
    seeds: list[ScheduledOccurrenceSeed] = []
    for payment in payments:
        seeds.extend(
            build_occurrence_seeds_for_payment(
                payment=payment,
                range_start=range_start,
                range_end=range_end,
            )
        )

    seeds.sort(key=lambda item: (item.due_date, item.payment_id))
    return seeds

