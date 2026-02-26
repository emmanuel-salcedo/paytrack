from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Occurrence, PaySchedule, Payment
from app.services.scheduling_service import get_current_cycle, get_next_cycle_for_date


DEFAULT_ANCHOR_PAYDAY_DATE = date(2026, 1, 15)


@dataclass(frozen=True)
class CycleOccurrenceView:
    occurrence_id: int
    payment_id: int
    payment_name: str
    due_date: date
    expected_amount: Decimal
    status: str


@dataclass(frozen=True)
class CycleSnapshotView:
    label: str
    cycle_start: date
    cycle_end: date
    scheduled_total: Decimal
    paid_total: Decimal
    skipped_total: Decimal
    remaining_total: Decimal
    occurrence_count: int
    occurrences: list[CycleOccurrenceView]


def _get_anchor_payday_date(session: Session) -> date:
    schedule = session.query(PaySchedule).first()
    return schedule.anchor_payday_date if schedule else DEFAULT_ANCHOR_PAYDAY_DATE


def get_cycle_snapshot(
    session: Session,
    *,
    today: date,
    which: str,
) -> CycleSnapshotView:
    anchor = _get_anchor_payday_date(session)
    if which == "current":
        cycle = get_current_cycle(today=today, anchor_payday_date=anchor)
        label = "Current Cycle"
    elif which == "next":
        cycle = get_next_cycle_for_date(today=today, anchor_payday_date=anchor)
        label = "Next Cycle Preview"
    else:
        raise ValueError(f"Unsupported cycle snapshot type: {which}")

    rows = session.execute(
        select(Occurrence, Payment)
        .join(Payment, Payment.id == Occurrence.payment_id)
        .where(
            Occurrence.due_date >= cycle.start,
            Occurrence.due_date <= cycle.end,
            Occurrence.status != "canceled",
        )
        .order_by(Occurrence.due_date.asc(), Payment.name.asc(), Occurrence.id.asc())
    ).all()

    occurrences = [
        CycleOccurrenceView(
            occurrence_id=occurrence.id,
            payment_id=payment.id,
            payment_name=payment.name,
            due_date=occurrence.due_date,
            expected_amount=Decimal(str(occurrence.expected_amount)),
            status=occurrence.status,
        )
        for occurrence, payment in rows
    ]

    # Totals semantics follow the locked scope definitions:
    # - scheduled/skipped/remaining are based on occurrences due in this cycle (due_date)
    # - paid is based on completed occurrences with paid_date in this cycle (cash-flow view)
    scheduled_total = sum(
        (item.expected_amount for item in occurrences if item.status in {"scheduled", "completed", "skipped"}),
        start=Decimal("0.00"),
    )
    skipped_total = sum(
        (item.expected_amount for item in occurrences if item.status == "skipped"),
        start=Decimal("0.00"),
    )
    remaining_total = sum(
        (item.expected_amount for item in occurrences if item.status == "scheduled"),
        start=Decimal("0.00"),
    )
    paid_rows = session.scalars(
        select(Occurrence).where(
            Occurrence.status == "completed",
            Occurrence.paid_date.is_not(None),
            Occurrence.paid_date >= cycle.start,
            Occurrence.paid_date <= cycle.end,
        )
    ).all()
    paid_total = sum(
        (
            Decimal(str(occ.amount_paid))
            for occ in paid_rows
            if occ.amount_paid is not None
        ),
        start=Decimal("0.00"),
    )

    return CycleSnapshotView(
        label=label,
        cycle_start=cycle.start,
        cycle_end=cycle.end,
        scheduled_total=scheduled_total,
        paid_total=paid_total,
        skipped_total=skipped_total,
        remaining_total=remaining_total,
        occurrence_count=len(occurrences),
        occurrences=occurrences,
    )
