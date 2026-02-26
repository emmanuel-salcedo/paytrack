from __future__ import annotations

from collections.abc import Generator

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401
from app.db import get_db_session
from app.main import app
from app.models.base import Base


def _test_session_factory(tmp_path):
    db_path = tmp_path / "phase2_endpoints.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def _override_db(session_factory) -> Generator[Session, None, None]:
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


def test_api_payments_create_list_and_manual_generation(tmp_path) -> None:
    SessionLocal = _test_session_factory(tmp_path)
    def override_get_db():
        yield from _override_db(SessionLocal)

    app.dependency_overrides[get_db_session] = override_get_db
    try:
        with TestClient(app) as client:
            initial_list = client.get("/api/payments")
            assert initial_list.status_code == 200
            assert initial_list.json() == []

            created = client.post(
                "/api/payments",
                json={
                    "name": "Internet",
                    "expected_amount": "80.00",
                    "initial_due_date": "2026-01-15",
                    "recurrence_type": "monthly",
                },
            )
            assert created.status_code == 201
            assert created.json()["name"] == "Internet"
            internet_payment_id = created.json()["id"]

            created_weekly = client.post(
                "/api/payments",
                json={
                    "name": "Gym",
                    "expected_amount": "25.00",
                    "initial_due_date": "2026-01-08",
                    "recurrence_type": "weekly",
                },
            )
            assert created_weekly.status_code == 201

            listed = client.get("/api/payments")
            assert listed.status_code == 200
            assert len(listed.json()) == 2

            run_generation = client.post(
                "/api/admin/run-generation",
                json={"today": "2026-01-15", "horizon_days": 60},
            )
            assert run_generation.status_code == 200
            payload = run_generation.json()
            assert payload["generated_count"] >= 1
            assert payload["skipped_existing_count"] == 0

            rerun = client.post(
                "/api/admin/run-generation",
                json={"today": "2026-01-15", "horizon_days": 60},
            )
            assert rerun.status_code == 200
            rerun_payload = rerun.json()
            assert rerun_payload["generated_count"] == 0
            assert rerun_payload["skipped_existing_count"] >= 1

            current_cycle = client.get("/api/cycles/current", params={"today": "2026-01-15"})
            assert current_cycle.status_code == 200
            current_payload = current_cycle.json()
            assert current_payload["cycle_start"] == "2026-01-02"
            assert current_payload["cycle_end"] == "2026-01-15"
            assert any(row["payment_name"] == "Internet" for row in current_payload["occurrences"])
            internet_occurrence = next(
                row for row in current_payload["occurrences"] if row["payment_name"] == "Internet"
            )

            next_cycle = client.get("/api/cycles/next", params={"today": "2026-01-15"})
            assert next_cycle.status_code == 200
            next_payload = next_cycle.json()
            assert next_payload["cycle_start"] == "2026-01-16"
            assert next_payload["cycle_end"] == "2026-01-29"
            assert any(row["payment_name"] == "Gym" for row in next_payload["occurrences"])
            gym_occurrence = next(row for row in next_payload["occurrences"] if row["payment_name"] == "Gym")

            mark_paid = client.post(
                f"/api/occurrences/{internet_occurrence['occurrence_id']}/mark-paid",
                json={"today": "2026-01-15"},
            )
            assert mark_paid.status_code == 200
            assert mark_paid.json()["status"] == "completed"
            assert mark_paid.json()["amount_paid"] == "80.00"

            edit_paid = client.post(
                f"/api/occurrences/{internet_occurrence['occurrence_id']}/mark-paid",
                json={"today": "2026-01-15", "amount_paid": "79.25", "paid_date": "2026-01-18"},
            )
            assert edit_paid.status_code == 200
            assert edit_paid.json()["amount_paid"] == "79.25"
            assert edit_paid.json()["paid_date"] == "2026-01-18"

            undo_paid = client.post(f"/api/occurrences/{internet_occurrence['occurrence_id']}/undo-paid")
            assert undo_paid.status_code == 200
            assert undo_paid.json()["status"] == "scheduled"
            assert undo_paid.json()["amount_paid"] is None

            skip = client.post(f"/api/occurrences/{gym_occurrence['occurrence_id']}/skip")
            assert skip.status_code == 200
            assert skip.json()["status"] == "skipped"

            paid_off = client.post(
                f"/api/payments/{internet_payment_id}/paid-off",
                json={"paid_off_date": "2026-01-15"},
            )
            assert paid_off.status_code == 200
            assert paid_off.json()["payment_id"] == internet_payment_id
            assert paid_off.json()["canceled_occurrences_count"] >= 1

            cycle_with_future_internet_due = client.get("/api/cycles/next", params={"today": "2026-02-12"})
            assert cycle_with_future_internet_due.status_code == 200
            assert all(
                row["payment_name"] != "Internet"
                for row in cycle_with_future_internet_due.json()["occurrences"]
            )

            guarded_first = client.post(
                "/api/admin/run-generation-once-today",
                json={"today": "2026-01-16", "horizon_days": 60},
            )
            assert guarded_first.status_code == 200
            guarded_first_payload = guarded_first.json()
            assert guarded_first_payload["ran"] is True
            assert guarded_first_payload["job_name"] == "generate_occurrences_ahead"

            guarded_second = client.post(
                "/api/admin/run-generation-once-today",
                json={"today": "2026-01-16", "horizon_days": 60},
            )
            assert guarded_second.status_code == 200
            guarded_second_payload = guarded_second.json()
            assert guarded_second_payload["ran"] is False

            ensured_first = client.post(
                "/api/admin/ensure-daily-generation",
                json={"today": "2026-01-17", "horizon_days": 60},
            )
            assert ensured_first.status_code == 200
            ensured_first_payload = ensured_first.json()
            assert ensured_first_payload["trigger"] == "ensure-daily-generation"
            assert ensured_first_payload["ready"] is True
            assert ensured_first_payload["ran"] is True

            ensured_second = client.post(
                "/api/admin/ensure-daily-generation",
                json={"today": "2026-01-17", "horizon_days": 60},
            )
            assert ensured_second.status_code == 200
            ensured_second_payload = ensured_second.json()
            assert ensured_second_payload["ready"] is True
            assert ensured_second_payload["ran"] is False
    finally:
        app.dependency_overrides.clear()


def test_web_htmx_payments_and_generation_panels(tmp_path) -> None:
    SessionLocal = _test_session_factory(tmp_path)
    def override_get_db():
        yield from _override_db(SessionLocal)

    app.dependency_overrides[get_db_session] = override_get_db
    try:
        with TestClient(app) as client:
            home = client.get("/")
            assert home.status_code == 200
            assert "Payments (Minimal CRUD)" in home.text
            assert "Occurrence Generation" in home.text
            assert "Current Cycle" in home.text
            assert "Next Cycle Preview" in home.text

            create_resp = client.post(
                "/payments",
                data={
                    "name": "Gym",
                    "expected_amount": "25.00",
                    "initial_due_date": "2026-01-08",
                    "recurrence_type": "weekly",
                },
                headers={"HX-Request": "true"},
            )
            assert create_resp.status_code == 200
            assert "Gym" in create_resp.text
            assert "interactive-panels" in create_resp.text
            assert "payments-panel" in create_resp.text

            gen_resp = client.post(
                "/admin/run-generation",
                data={"horizon_days": "30"},
                headers={"HX-Request": "true"},
            )
            assert gen_resp.status_code == 200
            assert "interactive-panels" in gen_resp.text
            assert "generation-panel" in gen_resp.text
            assert "Inserted" in gen_resp.text

            payments_api = client.get("/api/payments")
            assert payments_api.status_code == 200
            gym_payment = next(row for row in payments_api.json() if row["name"] == "Gym")

            current_cycle = client.get("/api/cycles/current")
            next_cycle = client.get("/api/cycles/next")
            all_occurrences = current_cycle.json()["occurrences"] + next_cycle.json()["occurrences"]
            gym_occurrence = next((row for row in all_occurrences if row["payment_name"] == "Gym"), None)
            assert gym_occurrence is not None

            mark_paid_web = client.post(
                f"/occurrences/{gym_occurrence['occurrence_id']}/mark-paid",
                data={},
                headers={"HX-Request": "true"},
            )
            assert mark_paid_web.status_code == 200
            assert "Occurrence marked paid." in mark_paid_web.text
            assert "completed" in mark_paid_web.text

            undo_paid_web = client.post(
                f"/occurrences/{gym_occurrence['occurrence_id']}/undo-paid",
                data={},
                headers={"HX-Request": "true"},
            )
            assert undo_paid_web.status_code == 200
            assert "Paid status undone." in undo_paid_web.text

            paid_off_web = client.post(
                f"/payments/{gym_payment['id']}/paid-off",
                data={},
                headers={"HX-Request": "true"},
            )
            assert paid_off_web.status_code == 200
            assert "Payment marked paid off." in paid_off_web.text
            assert "Archived" in paid_off_web.text

            guarded_first = client.post(
                "/admin/run-generation-once-today",
                data={"horizon_days": "30"},
                headers={"HX-Request": "true"},
            )
            assert guarded_first.status_code == 200
            assert "Guard blocked duplicate run" in guarded_first.text

            guarded_second = client.post(
                "/admin/run-generation-once-today",
                data={"horizon_days": "30"},
                headers={"HX-Request": "true"},
            )
            assert guarded_second.status_code == 200
            assert "Guard blocked duplicate run" in guarded_second.text
    finally:
        app.dependency_overrides.clear()
