from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401
from app.models.base import Base
from app.models.payments import Occurrence, Payment
from app.services.actions_service import (
    ActionValidationError,
    mark_occurrence_paid,
    mark_payment_paid_off,
    skip_occurrence,
    undo_mark_paid,
)


def _make_session(tmp_path) -> Session:
    db_path = tmp_path / "phase3_actions.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    return SessionLocal()


def _seed_payment_with_occurrences(session: Session) -> tuple[Payment, list[Occurrence]]:
    payment = Payment(
        name="Loan",
        expected_amount=Decimal("100.00"),
        initial_due_date=date(2026, 1, 15),
        recurrence_type="monthly",
        is_active=True,
    )
    session.add(payment)
    session.commit()
    session.refresh(payment)

    occurrences = [
        Occurrence(
            payment_id=payment.id,
            due_date=date(2026, 1, 15),
            expected_amount=Decimal("100.00"),
            status="scheduled",
        ),
        Occurrence(
            payment_id=payment.id,
            due_date=date(2026, 2, 15),
            expected_amount=Decimal("100.00"),
            status="scheduled",
        ),
        Occurrence(
            payment_id=payment.id,
            due_date=date(2026, 3, 15),
            expected_amount=Decimal("100.00"),
            status="scheduled",
        ),
    ]
    session.add_all(occurrences)
    session.commit()
    for occ in occurrences:
        session.refresh(occ)
    return payment, occurrences


def test_mark_paid_defaults_edit_and_undo(tmp_path) -> None:
    session = _make_session(tmp_path)
    try:
        _, occurrences = _seed_payment_with_occurrences(session)
        target = occurrences[0]

        completed = mark_occurrence_paid(session, occurrence_id=target.id, today=date(2026, 1, 16))
        assert completed.status == "completed"
        assert completed.amount_paid == Decimal("100.00")
        assert completed.paid_date == date(2026, 1, 16)

        edited = mark_occurrence_paid(
            session,
            occurrence_id=target.id,
            today=date(2026, 1, 16),
            amount_paid=Decimal("95.50"),
            paid_date=date(2026, 1, 20),
        )
        assert edited.status == "completed"
        assert edited.amount_paid == Decimal("95.50")
        assert edited.paid_date == date(2026, 1, 20)

        undone = undo_mark_paid(session, occurrence_id=target.id)
        assert undone.status == "scheduled"
        assert undone.amount_paid is None
        assert undone.paid_date is None
    finally:
        session.close()


def test_skip_occurrence_only_from_scheduled(tmp_path) -> None:
    session = _make_session(tmp_path)
    try:
        _, occurrences = _seed_payment_with_occurrences(session)
        target = occurrences[1]

        skipped = skip_occurrence(session, occurrence_id=target.id)
        assert skipped.status == "skipped"

        try:
            skip_occurrence(session, occurrence_id=target.id)
        except ActionValidationError as exc:
            assert "Cannot skip occurrence" in str(exc)
        else:
            raise AssertionError("Expected skip validation error")
    finally:
        session.close()


def test_paid_off_archives_payment_and_cancels_future_scheduled_only(tmp_path) -> None:
    session = _make_session(tmp_path)
    try:
        payment, occurrences = _seed_payment_with_occurrences(session)
        # Make one occurrence completed and one skipped so they should not be changed by paid-off.
        mark_occurrence_paid(session, occurrence_id=occurrences[0].id, today=date(2026, 1, 15))
        skip_occurrence(session, occurrence_id=occurrences[1].id)

        result = mark_payment_paid_off(session, payment_id=payment.id, paid_off_date=date(2026, 2, 15))

        payment_after = session.get(Payment, payment.id)
        all_rows = session.scalars(select(Occurrence).where(Occurrence.payment_id == payment.id)).all()
        by_due = {row.due_date: row for row in all_rows}

        assert payment_after is not None
        assert payment_after.is_active is False
        assert payment_after.paid_off_date == date(2026, 2, 15)
        assert result.canceled_occurrences_count == 1
        assert by_due[date(2026, 1, 15)].status == "completed"
        assert by_due[date(2026, 2, 15)].status == "skipped"
        assert by_due[date(2026, 3, 15)].status == "canceled"
    finally:
        session.close()

