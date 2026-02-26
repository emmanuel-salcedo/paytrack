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

            next_cycle = client.get("/api/cycles/next", params={"today": "2026-01-15"})
            assert next_cycle.status_code == 200
            next_payload = next_cycle.json()
            assert next_payload["cycle_start"] == "2026-01-16"
            assert next_payload["cycle_end"] == "2026-01-29"
            assert any(row["payment_name"] == "Gym" for row in next_payload["occurrences"])

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
            assert "payments-panel" in create_resp.text

            gen_resp = client.post(
                "/admin/run-generation",
                data={"horizon_days": "30"},
                headers={"HX-Request": "true"},
            )
            assert gen_resp.status_code == 200
            assert "generation-panel" in gen_resp.text
            assert "Inserted" in gen_resp.text

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
