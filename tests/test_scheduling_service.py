from datetime import date
from decimal import Decimal

from app.services.date_engine import PayCycle
from app.services.scheduling_service import (
    PaymentScheduleSpec,
    ScheduledOccurrenceSeed,
    build_occurrence_seeds,
    build_occurrence_seeds_for_payment,
    get_current_cycle,
    get_next_cycle_for_date,
)


ANCHOR = date(2026, 1, 15)


def test_current_and_next_cycle_helpers() -> None:
    current_cycle = get_current_cycle(today=date(2026, 1, 20), anchor_payday_date=ANCHOR)
    next_cycle = get_next_cycle_for_date(today=date(2026, 1, 20), anchor_payday_date=ANCHOR)

    assert current_cycle == PayCycle(start=date(2026, 1, 16), end=date(2026, 1, 29))
    assert next_cycle == PayCycle(start=date(2026, 1, 30), end=date(2026, 2, 12))


def test_build_occurrence_seeds_for_payment_active_only() -> None:
    active_payment = PaymentScheduleSpec(
        payment_id=1,
        name="Rent",
        expected_amount=Decimal("1250.00"),
        initial_due_date=date(2026, 1, 1),
        recurrence_type="monthly",
        is_active=True,
    )
    inactive_payment = PaymentScheduleSpec(
        payment_id=2,
        name="Old Loan",
        expected_amount=Decimal("75.00"),
        initial_due_date=date(2026, 1, 5),
        recurrence_type="weekly",
        is_active=False,
    )

    active_seeds = build_occurrence_seeds_for_payment(
        payment=active_payment,
        range_start=date(2026, 1, 1),
        range_end=date(2026, 3, 31),
    )
    inactive_seeds = build_occurrence_seeds_for_payment(
        payment=inactive_payment,
        range_start=date(2026, 1, 1),
        range_end=date(2026, 3, 31),
    )

    assert [seed.due_date for seed in active_seeds] == [
        date(2026, 1, 1),
        date(2026, 2, 1),
        date(2026, 3, 1),
    ]
    assert all(seed.status == "scheduled" for seed in active_seeds)
    assert inactive_seeds == []


def test_build_occurrence_seeds_returns_sorted_generation_contract() -> None:
    payments = [
        PaymentScheduleSpec(
            payment_id=2,
            name="Internet",
            expected_amount=Decimal("80.00"),
            initial_due_date=date(2026, 1, 15),
            recurrence_type="monthly",
        ),
        PaymentScheduleSpec(
            payment_id=1,
            name="Gym",
            expected_amount=Decimal("25.00"),
            initial_due_date=date(2026, 1, 8),
            recurrence_type="weekly",
        ),
    ]

    seeds = build_occurrence_seeds(
        payments=payments,
        range_start=date(2026, 1, 1),
        range_end=date(2026, 1, 31),
    )

    assert seeds == [
        ScheduledOccurrenceSeed(
            payment_id=1,
            due_date=date(2026, 1, 8),
            expected_amount=Decimal("25.00"),
        ),
        ScheduledOccurrenceSeed(
            payment_id=1,
            due_date=date(2026, 1, 15),
            expected_amount=Decimal("25.00"),
        ),
        ScheduledOccurrenceSeed(
            payment_id=2,
            due_date=date(2026, 1, 15),
            expected_amount=Decimal("80.00"),
        ),
        ScheduledOccurrenceSeed(
            payment_id=1,
            due_date=date(2026, 1, 22),
            expected_amount=Decimal("25.00"),
        ),
        ScheduledOccurrenceSeed(
            payment_id=1,
            due_date=date(2026, 1, 29),
            expected_amount=Decimal("25.00"),
        ),
    ]
