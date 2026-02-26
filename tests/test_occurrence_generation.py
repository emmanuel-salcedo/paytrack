from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401
from app.models.base import Base
from app.models.jobs import JobRun
from app.models.payments import Occurrence, Payment
from app.services.occurrence_generation import (
    GENERATE_OCCURRENCES_JOB_NAME,
    generate_occurrences_ahead,
    run_generate_occurrences_once_per_day,
    try_mark_daily_job_run,
)


def _make_session(tmp_path) -> Session:
    db_path = tmp_path / "phase2_test.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    return SessionLocal()


def test_generate_occurrences_ahead_creates_expected_rows(tmp_path) -> None:
    session = _make_session(tmp_path)
    try:
        rent = Payment(
            name="Rent",
            expected_amount=Decimal("1250.00"),
            initial_due_date=date(2026, 1, 31),
            recurrence_type="monthly",
            is_active=True,
        )
        gym = Payment(
            name="Gym",
            expected_amount=Decimal("25.00"),
            initial_due_date=date(2026, 1, 8),
            recurrence_type="weekly",
            is_active=True,
        )
        old_loan = Payment(
            name="Old Loan",
            expected_amount=Decimal("50.00"),
            initial_due_date=date(2026, 1, 10),
            recurrence_type="weekly",
            is_active=False,
        )
        session.add_all([rent, gym, old_loan])
        session.commit()

        result = generate_occurrences_ahead(session, today=date(2026, 1, 15), horizon_days=45)

        assert result.range_start == date(2026, 1, 15)
        assert result.range_end == date(2026, 3, 1)
        assert result.generated_count > 0
        assert result.skipped_existing_count == 0

        rows = session.scalars(select(Occurrence).order_by(Occurrence.due_date, Occurrence.payment_id)).all()
        assert rows
        assert all(row.status == "scheduled" for row in rows)
        assert all(row.payment_id != old_loan.id for row in rows)  # inactive payment excluded
        assert any(row.due_date == date(2026, 2, 28) for row in rows)  # monthly clamp
        assert any(row.expected_amount == Decimal("1250.00") for row in rows)
    finally:
        session.close()


def test_generate_occurrences_ahead_is_idempotent(tmp_path) -> None:
    session = _make_session(tmp_path)
    try:
        session.add(
            Payment(
                name="Internet",
                expected_amount=Decimal("80.00"),
                initial_due_date=date(2026, 1, 15),
                recurrence_type="monthly",
                is_active=True,
            )
        )
        session.commit()

        first = generate_occurrences_ahead(session, today=date(2026, 1, 15), horizon_days=90)
        second = generate_occurrences_ahead(session, today=date(2026, 1, 15), horizon_days=90)

        count = session.scalar(select(func.count()).select_from(Occurrence))
        rows = session.scalars(select(Occurrence).order_by(Occurrence.due_date)).all()

        assert first.generated_count == len(rows)
        assert second.generated_count == 0
        assert second.skipped_existing_count == len(rows)
        assert count == len(rows)
        assert len({(row.payment_id, row.due_date) for row in rows}) == len(rows)
    finally:
        session.close()


def test_job_run_daily_guard_prevents_duplicate_day_runs(tmp_path) -> None:
    session = _make_session(tmp_path)
    try:
        first = try_mark_daily_job_run(
            session,
            job_name=GENERATE_OCCURRENCES_JOB_NAME,
            run_date=date(2026, 2, 26),
        )
        second = try_mark_daily_job_run(
            session,
            job_name=GENERATE_OCCURRENCES_JOB_NAME,
            run_date=date(2026, 2, 26),
        )
        third = try_mark_daily_job_run(
            session,
            job_name=GENERATE_OCCURRENCES_JOB_NAME,
            run_date=date(2026, 2, 27),
        )

        runs = session.scalars(select(JobRun).order_by(JobRun.run_date)).all()
        assert first is True
        assert second is False
        assert third is True
        assert len(runs) == 2
    finally:
        session.close()


def test_guarded_generation_runs_only_once_per_day(tmp_path) -> None:
    session = _make_session(tmp_path)
    try:
        session.add(
            Payment(
                name="Streaming",
                expected_amount=Decimal("12.99"),
                initial_due_date=date(2026, 1, 20),
                recurrence_type="monthly",
                is_active=True,
            )
        )
        session.commit()

        first = run_generate_occurrences_once_per_day(
            session,
            today=date(2026, 2, 26),
            horizon_days=45,
        )
        second = run_generate_occurrences_once_per_day(
            session,
            today=date(2026, 2, 26),
            horizon_days=45,
        )

        assert first.ran is True
        assert first.generation_result is not None
        assert second.ran is False
        assert second.generation_result is None
    finally:
        session.close()
