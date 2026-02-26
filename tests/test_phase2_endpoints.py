from __future__ import annotations

from collections.abc import Generator

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401
from app.db import get_db_session
from app.main import app
from app.models.base import Base
from app.models.notifications import Notification, NotificationLog


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
            assert current_payload["totals"] == {
                "scheduled": "105.00",
                "paid": "0.00",
                "skipped": "0.00",
                "remaining": "105.00",
            }
            assert any(row["payment_name"] == "Internet" for row in current_payload["occurrences"])
            internet_occurrence = next(
                row for row in current_payload["occurrences"] if row["payment_name"] == "Internet"
            )

            next_cycle = client.get("/api/cycles/next", params={"today": "2026-01-15"})
            assert next_cycle.status_code == 200
            next_payload = next_cycle.json()
            assert next_payload["cycle_start"] == "2026-01-16"
            assert next_payload["cycle_end"] == "2026-01-29"
            assert next_payload["totals"] == {
                "scheduled": "50.00",
                "paid": "0.00",
                "skipped": "0.00",
                "remaining": "50.00",
            }
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

            current_after_edit = client.get("/api/cycles/current", params={"today": "2026-01-15"})
            next_after_edit = client.get("/api/cycles/next", params={"today": "2026-01-15"})
            assert current_after_edit.status_code == 200
            assert next_after_edit.status_code == 200
            assert current_after_edit.json()["totals"] == {
                "scheduled": "105.00",
                "paid": "0.00",
                "skipped": "0.00",
                "remaining": "25.00",
            }
            assert next_after_edit.json()["totals"] == {
                "scheduled": "50.00",
                "paid": "79.25",
                "skipped": "0.00",
                "remaining": "50.00",
            }

            undo_paid = client.post(f"/api/occurrences/{internet_occurrence['occurrence_id']}/undo-paid")
            assert undo_paid.status_code == 200
            assert undo_paid.json()["status"] == "scheduled"
            assert undo_paid.json()["amount_paid"] is None

            skip = client.post(f"/api/occurrences/{gym_occurrence['occurrence_id']}/skip")
            assert skip.status_code == 200
            assert skip.json()["status"] == "skipped"

            next_after_skip = client.get("/api/cycles/next", params={"today": "2026-01-15"})
            assert next_after_skip.status_code == 200
            assert next_after_skip.json()["totals"] == {
                "scheduled": "50.00",
                "paid": "0.00",
                "skipped": "25.00",
                "remaining": "25.00",
            }

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

            reactivate = client.post(
                f"/api/payments/{internet_payment_id}/reactivate",
                json={"today": "2026-01-16", "horizon_days": 90},
            )
            assert reactivate.status_code == 200
            reactivate_payload = reactivate.json()
            assert reactivate_payload["payment_id"] == internet_payment_id
            assert reactivate_payload["generated_occurrences_count"] >= 1

            update_payment = client.post(
                f"/api/payments/{internet_payment_id}/update",
                json={
                    "name": "Internet Premium",
                    "expected_amount": "95.00",
                    "initial_due_date": "2026-01-20",
                    "recurrence_type": "monthly",
                    "today": "2026-01-20",
                    "horizon_days": 90,
                },
            )
            assert update_payment.status_code == 200
            assert update_payment.json()["payment_id"] == internet_payment_id

            payments_after_update = client.get("/api/payments")
            assert payments_after_update.status_code == 200
            updated_payment = next(row for row in payments_after_update.json() if row["id"] == internet_payment_id)
            assert updated_payment["name"] == "Internet Premium"
            assert updated_payment["expected_amount"] == "95.00"

            cycle_after_update = client.get("/api/cycles/next", params={"today": "2026-02-12"})
            assert cycle_after_update.status_code == 200
            assert any(
                row["payment_name"] == "Internet Premium"
                for row in cycle_after_update.json()["occurrences"]
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

            dashboard_page = client.get("/dashboard")
            upcoming_page = client.get("/upcoming")
            payments_page = client.get("/payments")
            settings_page = client.get("/settings")
            notifications_page = client.get("/notifications")
            assert dashboard_page.status_code == 200
            assert upcoming_page.status_code == 200
            assert payments_page.status_code == 200
            assert settings_page.status_code == 200
            assert notifications_page.status_code == 200
            assert "Next Pay Cycle Preview" in upcoming_page.text
            assert "Manage Payments" in payments_page.text
            assert "Settings" in settings_page.text
            assert "Notifications" in notifications_page.text
            assert "Dashboard" in payments_page.text

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

            create_resp_page = client.post(
                "/payments/page/create",
                data={
                    "name": "Water",
                    "expected_amount": "40.00",
                    "initial_due_date": "2026-01-10",
                    "recurrence_type": "monthly",
                },
                headers={"HX-Request": "true"},
            )
            assert create_resp_page.status_code == 200
            assert "payments-page-shell" in create_resp_page.text
            assert "Water" in create_resp_page.text

            invalid_create = client.post(
                "/payments",
                data={
                    "name": "",
                    "expected_amount": "abc",
                    "initial_due_date": "not-a-date",
                    "recurrence_type": "bad",
                },
                headers={"HX-Request": "true"},
            )
            assert invalid_create.status_code == 200
            assert "Enter a valid amount." in invalid_create.text
            assert "Enter a valid date." in invalid_create.text
            assert "Choose a valid recurrence." in invalid_create.text

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

            paid_off_page = client.post(
                f"/payments/page/{gym_payment['id']}/paid-off",
                data={},
                headers={"HX-Request": "true"},
            )
            assert paid_off_page.status_code == 200
            assert "payments-page-shell" in paid_off_page.text

            reactivate_web = client.post(
                f"/payments/{gym_payment['id']}/reactivate",
                data={},
                headers={"HX-Request": "true"},
            )
            assert reactivate_web.status_code == 200
            assert "Payment reactivated." in reactivate_web.text

            reactivate_page = client.post(
                f"/payments/page/{gym_payment['id']}/reactivate",
                data={},
                headers={"HX-Request": "true"},
            )
            assert reactivate_page.status_code == 200
            assert "payments-page-shell" in reactivate_page.text

            edit_invalid_web = client.post(
                f"/payments/{gym_payment['id']}/update",
                data={
                    "name": "",
                    "expected_amount": "-1",
                    "initial_due_date": "bad-date",
                    "recurrence_type": "oops",
                },
                headers={"HX-Request": "true"},
            )
            assert edit_invalid_web.status_code == 200
            assert "Payment update failed." in edit_invalid_web.text
            assert "Name is required." in edit_invalid_web.text

            edit_invalid_page = client.post(
                f"/payments/page/{gym_payment['id']}/update",
                data={
                    "name": "",
                    "expected_amount": "x",
                    "initial_due_date": "bad",
                    "recurrence_type": "oops",
                },
                headers={"HX-Request": "true"},
            )
            assert edit_invalid_page.status_code == 200
            assert "payments-page-shell" in edit_invalid_page.text
            assert "Payment update failed." in edit_invalid_page.text

            edit_valid_web = client.post(
                f"/payments/{gym_payment['id']}/update",
                data={
                    "name": "Gym Plus",
                    "expected_amount": "30.00",
                    "initial_due_date": "2026-01-09",
                    "recurrence_type": "weekly",
                    "priority": "2",
                },
                headers={"HX-Request": "true"},
            )
            assert edit_valid_web.status_code == 200
            assert "Payment updated." in edit_valid_web.text
            assert "Gym Plus" in edit_valid_web.text

            edit_valid_page = client.post(
                f"/payments/page/{gym_payment['id']}/update",
                data={
                    "name": "Gym Ultra",
                    "expected_amount": "35.00",
                    "initial_due_date": "2026-01-10",
                    "recurrence_type": "weekly",
                },
                headers={"HX-Request": "true"},
            )
            assert edit_valid_page.status_code == 200
            assert "payments-page-shell" in edit_valid_page.text
            assert "Gym Ultra" in edit_valid_page.text

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

            notifications_after_actions = client.get("/notifications")
            assert notifications_after_actions.status_code == 200
            assert "Payment Added" in notifications_after_actions.text
            assert "Payment Marked Paid Off" in notifications_after_actions.text
            assert "Payment Reactivated" in notifications_after_actions.text
    finally:
        app.dependency_overrides.clear()


def test_settings_and_notifications_pages_are_db_backed(tmp_path, monkeypatch) -> None:
    SessionLocal = _test_session_factory(tmp_path)

    def override_get_db():
        yield from _override_db(SessionLocal)

    app.dependency_overrides[get_db_session] = override_get_db
    try:
        monkeypatch.setattr(
            "app.routes.web.send_telegram_message",
            lambda **kwargs: {"ok": True},
        )
        with TestClient(app) as client:
            settings_before = client.get("/settings")
            assert settings_before.status_code == 200
            assert "App Settings" in settings_before.text

            update_schedule = client.post(
                "/settings/pay-schedule",
                data={"anchor_payday_date": "2026-01-29", "timezone": "America/Los_Angeles"},
            )
            assert update_schedule.status_code == 200
            assert "Pay schedule updated." in update_schedule.text

            update_app = client.post(
                "/settings/app",
                data={
                    "due_soon_days": "7",
                    "daily_summary_time": "08:15",
                    "telegram_enabled": "1",
                    "telegram_bot_token": "bot-token",
                    "telegram_chat_id": "chat-1",
                },
            )
            assert update_app.status_code == 200
            assert "App settings updated." in update_app.text

            test_telegram = client.post("/settings/telegram/test")
            assert test_telegram.status_code == 200
            assert "Telegram test message sent." in test_telegram.text

            bad_settings = client.post(
                "/settings/app",
                data={"due_soon_days": "-1", "daily_summary_time": "bad"},
            )
            assert bad_settings.status_code == 200
            assert "App settings update failed." in bad_settings.text

            # Seed notifications directly via the test DB session
            with SessionLocal() as session:
                session.add_all(
                    [
                        Notification(type="due_soon", title="Due Soon", body="Water bill due", is_read=False),
                        Notification(type="overdue", title="Overdue", body="Gym overdue", is_read=False),
                    ]
                )
                session.commit()

            notifications_page = client.get("/notifications")
            assert notifications_page.status_code == 200
            assert "Notifications Center" in notifications_page.text
            assert "Unread: 3" in notifications_page.text
            assert "nav-badge" in notifications_page.text
            assert "Telegram Test Sent" in notifications_page.text

            mark_one = client.post("/notifications/1/read")
            assert mark_one.status_code == 200
            assert "Notification marked read." in mark_one.text

            mark_all = client.post("/notifications/mark-all-read")
            assert mark_all.status_code == 200
            assert "Marked 2 notifications read." in mark_all.text
    finally:
        app.dependency_overrides.clear()


def test_settings_api_endpoints_and_telegram_test(tmp_path, monkeypatch) -> None:
    SessionLocal = _test_session_factory(tmp_path)

    def override_get_db():
        yield from _override_db(SessionLocal)

    app.dependency_overrides[get_db_session] = override_get_db
    try:
        monkeypatch.setattr(
            "app.routes.api.send_telegram_message",
            lambda **kwargs: {"ok": True},
        )
        with TestClient(app) as client:
            settings_get = client.get("/api/settings")
            assert settings_get.status_code == 200
            payload = settings_get.json()
            assert payload["pay_schedule"]["anchor_payday_date"] == "2026-01-15"
            assert payload["app_settings"]["telegram_enabled"] is False

            bad_telegram_test = client.post("/api/settings/telegram/test")
            assert bad_telegram_test.status_code == 400
            assert bad_telegram_test.json()["detail"] == "Telegram is disabled."

            update_schedule = client.post(
                "/api/settings/pay-schedule",
                json={"anchor_payday_date": "2026-01-29", "timezone": "America/Los_Angeles"},
            )
            assert update_schedule.status_code == 200
            assert update_schedule.json()["anchor_payday_date"] == "2026-01-29"

            update_app = client.post(
                "/api/settings/app",
                json={
                    "due_soon_days": 6,
                    "daily_summary_time": "08:30",
                    "telegram_enabled": True,
                    "telegram_bot_token": "bot-token",
                    "telegram_chat_id": "chat-1",
                },
            )
            assert update_app.status_code == 200
            assert update_app.json()["telegram_enabled"] is True

            bad_app = client.post(
                "/api/settings/app",
                json={"due_soon_days": 1, "daily_summary_time": "invalid"},
            )
            assert bad_app.status_code == 400
            assert "HH:MM" in bad_app.json()["detail"]

            ok_telegram_test = client.post("/api/settings/telegram/test")
            assert ok_telegram_test.status_code == 200
            assert ok_telegram_test.json()["sent"] is True
            assert ok_telegram_test.json()["simulated"] is False

            notifications_page = client.get("/notifications")
            assert notifications_page.status_code == 200
            assert "Telegram Test Sent" in notifications_page.text
    finally:
        app.dependency_overrides.clear()


def test_admin_notification_jobs_create_due_soon_overdue_and_guard(tmp_path) -> None:
    SessionLocal = _test_session_factory(tmp_path)

    def override_get_db():
        yield from _override_db(SessionLocal)

    app.dependency_overrides[get_db_session] = override_get_db
    try:
        with TestClient(app) as client:
            client.post(
                "/api/payments",
                json={
                    "name": "Rent",
                    "expected_amount": "1000.00",
                    "initial_due_date": "2026-01-15",
                    "recurrence_type": "monthly",
                },
            )
            client.post(
                "/api/payments",
                json={
                    "name": "Gym",
                    "expected_amount": "25.00",
                    "initial_due_date": "2026-01-10",
                    "recurrence_type": "weekly",
                },
            )
            gen = client.post("/api/admin/run-generation", json={"today": "2026-01-01", "horizon_days": 60})
            assert gen.status_code == 200

            run_jobs = client.post(
                "/api/admin/run-notification-jobs",
                params={"today": "2026-01-15", "now": "2026-01-15T08:00:00"},
            )
            assert run_jobs.status_code == 200
            payload = run_jobs.json()
            assert payload["ready"] is True
            assert payload["ran"] is True
            assert payload["daily_summary_created"] == 1
            assert payload["due_soon_created"] == 1
            assert payload["overdue_created"] == 1
            assert payload["daily_summary_deferred_before_time"] is False

            run_jobs_again = client.post(
                "/api/admin/run-notification-jobs",
                params={"today": "2026-01-15", "now": "2026-01-15T08:05:00"},
            )
            assert run_jobs_again.status_code == 200
            assert run_jobs_again.json()["daily_summary_created"] == 0
            assert run_jobs_again.json()["due_soon_created"] == 0
            assert run_jobs_again.json()["overdue_created"] == 0

            client.post(
                "/api/settings/app",
                json={
                    "due_soon_days": 5,
                    "daily_summary_time": "23:59",
                    "telegram_enabled": False,
                },
            )

            guarded_first = client.post(
                "/api/admin/run-notification-jobs-once-today",
                params={"today": "2026-01-16", "now": "2026-01-16T09:00:00"},
            )
            assert guarded_first.status_code == 200
            assert guarded_first.json()["ready"] is True
            assert guarded_first.json()["ran"] is True
            assert guarded_first.json()["daily_summary_created"] == 0
            assert guarded_first.json()["daily_summary_deferred_before_time"] is True

            guarded_second = client.post(
                "/api/admin/run-notification-jobs-once-today",
                params={"today": "2026-01-16", "now": "2026-01-16T23:59:00"},
            )
            assert guarded_second.status_code == 200
            assert guarded_second.json()["ran"] is False

            forced_after_defer = client.post(
                "/api/admin/run-daily-summary-now",
                params={"today": "2026-01-16", "now": "2026-01-16T23:59:00"},
            )
            assert forced_after_defer.status_code == 200
            assert forced_after_defer.json()["daily_summary_created"] == 1
            assert forced_after_defer.json()["forced_daily_summary"] is True

            notifications_page = client.get("/notifications")
            assert notifications_page.status_code == 200
            assert "Daily Summary" in notifications_page.text
            assert "Due Soon" in notifications_page.text
            assert "Overdue" in notifications_page.text
            assert "Force Daily Summary Now" in notifications_page.text
            assert "Delivery Log" in notifications_page.text

            paged_notifications = client.get("/notifications", params={"per_page": 1, "log_per_page": 1, "sort": "oldest"})
            assert paged_notifications.status_code == 200
            assert "Showing 1 of" in paged_notifications.text
    finally:
        app.dependency_overrides.clear()


def test_notification_log_records_telegram_error_status(tmp_path, monkeypatch) -> None:
    SessionLocal = _test_session_factory(tmp_path)

    def override_get_db():
        yield from _override_db(SessionLocal)

    app.dependency_overrides[get_db_session] = override_get_db
    try:
        from app.services.telegram_service import TelegramDeliveryError

        monkeypatch.setattr(
            "app.services.notification_jobs_service.send_telegram_message",
            lambda **kwargs: (_ for _ in ()).throw(TelegramDeliveryError("telegram down")),
        )
        with TestClient(app) as client:
            client.post(
                "/api/settings/app",
                json={
                    "due_soon_days": 5,
                    "daily_summary_time": "00:00",
                    "telegram_enabled": True,
                    "telegram_bot_token": "token",
                    "telegram_chat_id": "chat",
                },
            )
            client.post(
                "/api/payments",
                json={
                    "name": "Gym",
                    "expected_amount": "25.00",
                    "initial_due_date": "2026-01-10",
                    "recurrence_type": "weekly",
                },
            )
            client.post("/api/admin/run-generation", json={"today": "2026-01-01", "horizon_days": 30})
            run_jobs = client.post(
                "/api/admin/run-notification-jobs",
                params={"today": "2026-01-15", "now": "2026-01-15T08:00:00"},
            )
            assert run_jobs.status_code == 200
            assert run_jobs.json()["telegram_errors"] >= 1

            with SessionLocal() as session:
                telegram_logs = session.query(NotificationLog).filter(NotificationLog.channel == "telegram").all()
                assert telegram_logs
                assert any(row.status == "error" for row in telegram_logs)
                assert any((row.error_message or "").startswith("telegram down") for row in telegram_logs)
    finally:
        app.dependency_overrides.clear()


def test_history_page_renders_and_filters(tmp_path) -> None:
    SessionLocal = _test_session_factory(tmp_path)

    def override_get_db():
        yield from _override_db(SessionLocal)

    app.dependency_overrides[get_db_session] = override_get_db
    try:
        with TestClient(app) as client:
            # Seed a payment + occurrences via existing flows
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

            run_generation = client.post(
                "/api/admin/run-generation",
                json={"today": "2026-01-15", "horizon_days": 60},
            )
            assert run_generation.status_code == 200

            current_cycle = client.get("/api/cycles/current", params={"today": "2026-01-15"})
            occ = next(row for row in current_cycle.json()["occurrences"] if row["payment_name"] == "Internet")
            mark_paid = client.post(
                f"/api/occurrences/{occ['occurrence_id']}/mark-paid",
                json={"today": "2026-01-15"},
            )
            assert mark_paid.status_code == 200

            history = client.get("/history")
            assert history.status_code == 200
            assert "Occurrence History" in history.text
            assert "Internet" in history.text

            filtered_completed = client.get("/history", params={"status": "completed"})
            assert filtered_completed.status_code == 200
            assert "completed" in filtered_completed.text

            paged_history = client.get("/history", params={"per_page": 1, "sort": "due_asc"})
            assert paged_history.status_code == 200
            assert "Rows Per Page" in paged_history.text

            filtered_search = client.get("/history", params={"q": "Inter"})
            assert filtered_search.status_code == 200
            assert "Internet" in filtered_search.text
    finally:
        app.dependency_overrides.clear()
