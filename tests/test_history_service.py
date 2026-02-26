from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401
from app.models.base import Base
from app.models.payments import Occurrence, Payment
from app.services.history_service import HistoryFilters, list_occurrence_history


def _make_session(tmp_path) -> Session:
    engine = create_engine(f"sqlite:///{tmp_path / 'history.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    return SessionLocal()


def test_history_filters_status_date_and_search(tmp_path) -> None:
    session = _make_session(tmp_path)
    try:
        gym = Payment(
            name="Gym Membership",
            expected_amount=Decimal("25.00"),
            initial_due_date=date(2026, 1, 8),
            recurrence_type="weekly",
            is_active=True,
        )
        internet = Payment(
            name="Home Internet",
            expected_amount=Decimal("80.00"),
            initial_due_date=date(2026, 1, 15),
            recurrence_type="monthly",
            is_active=True,
        )
        session.add_all([gym, internet])
        session.commit()
        session.refresh(gym)
        session.refresh(internet)

        session.add_all(
            [
                Occurrence(
                    payment_id=gym.id,
                    due_date=date(2026, 1, 8),
                    expected_amount=Decimal("25.00"),
                    status="completed",
                    amount_paid=Decimal("25.00"),
                    paid_date=date(2026, 1, 9),
                ),
                Occurrence(
                    payment_id=gym.id,
                    due_date=date(2026, 1, 15),
                    expected_amount=Decimal("25.00"),
                    status="skipped",
                ),
                Occurrence(
                    payment_id=internet.id,
                    due_date=date(2026, 2, 15),
                    expected_amount=Decimal("80.00"),
                    status="canceled",
                ),
            ]
        )
        session.commit()

        completed = list_occurrence_history(session, filters=HistoryFilters(status="completed"))
        assert len(completed) == 1
        assert completed[0].status == "completed"

        searched = list_occurrence_history(session, filters=HistoryFilters(q="Internet"))
        assert len(searched) == 1
        assert searched[0].payment_name == "Home Internet"

        dated = list_occurrence_history(
            session,
            filters=HistoryFilters(start_date=date(2026, 1, 10), end_date=date(2026, 1, 20)),
        )
        assert {row.status for row in dated} == {"skipped"}
    finally:
        session.close()

