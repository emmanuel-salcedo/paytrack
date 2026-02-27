from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import get_db_session
from app.models import AppSettings, PaySchedule
from app.services.actions_service import (
    ActionValidationError,
    mark_occurrence_paid,
    mark_payment_paid_off,
    reactivate_payment,
    skip_occurrence,
    undo_mark_paid,
    UpdatePaymentInput,
    update_payment_and_rebuild_future_scheduled,
)
from app.services.cycle_views_service import get_cycle_snapshot
from app.services.history_service import HistoryFilters, list_occurrence_history_page
from app.services.notifications_service import (
    NotificationsValidationError,
    count_notification_logs_filtered,
    count_notifications,
    create_in_app_notification,
    get_latest_telegram_delivery_error,
    get_unread_notifications_count,
    NotificationLogFilters,
    list_notification_logs,
    list_notifications,
    mark_all_notifications_read,
    mark_notification_read,
)
from app.services.notification_jobs_service import (
    run_notification_jobs_now_if_ready,
    run_notification_jobs_once_per_day_in_session_if_ready,
)
from app.services.occurrence_generation import (
    generate_occurrences_ahead,
    run_generate_occurrences_once_per_day_in_session_if_ready,
    run_generate_occurrences_once_per_day,
)
from app.services.payments_service import CreatePaymentInput, create_payment, list_payments
from app.services.settings_service import (
    SettingsValidationError,
    UpdateAppSettingsInput,
    UpdatePayScheduleInput,
    get_or_create_settings_rows,
    update_app_settings,
    update_pay_schedule,
)
from app.services.telegram_service import TelegramDeliveryError, send_telegram_message

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))
web_router = APIRouter(tags=["web"])
logger = logging.getLogger(__name__)


def _show_archived_enabled(value: str | None) -> bool:
    normalized = (value or "").strip().lower()
    return normalized in {"1", "true", "yes", "on"}


def _build_dashboard_context(
    db: Session,
    *,
    payment_error: str | None = None,
    payment_form_errors: dict[str, str] | None = None,
    payment_form_values: dict[str, str] | None = None,
    payment_edit_target_id: int | None = None,
    payment_edit_errors: dict[str, str] | None = None,
    payment_edit_values: dict[str, str] | None = None,
    generation_state: dict[str, object] | None = None,
    action_notice: str | None = None,
    action_error: str | None = None,
    show_archived: bool = True,
) -> dict[str, object]:
    schedule = db.query(PaySchedule).first()
    app_settings = db.query(AppSettings).first()
    payments = list_payments(db, include_archived=show_archived)
    current_cycle_snapshot = get_cycle_snapshot(db, today=date.today(), which="current")
    next_cycle_snapshot = get_cycle_snapshot(db, today=date.today(), which="next")
    notifications_unread_count = get_unread_notifications_count(db)
    return {
        "schedule": schedule,
        "app_settings": app_settings,
        "payments": payments,
        "current_cycle_snapshot": current_cycle_snapshot,
        "next_cycle_snapshot": next_cycle_snapshot,
        "notifications_unread_count": notifications_unread_count,
        "payment_error": payment_error,
        "payment_form_errors": payment_form_errors or {},
        "payment_form_values": payment_form_values
        or {
            "name": "",
            "expected_amount": "",
            "initial_due_date": "",
            "recurrence_type": "monthly",
            "priority": "",
        },
        "payment_edit_target_id": payment_edit_target_id,
        "payment_edit_errors": payment_edit_errors or {},
        "payment_edit_values": payment_edit_values or {},
        "generation_state": generation_state,
        "action_notice": action_notice,
        "action_error": action_error,
        "show_archived": show_archived,
        "payments_show_archived_path": "/dashboard",
    }


def _build_payments_only_context(
    db: Session,
    *,
    payment_error: str | None = None,
    payment_form_errors: dict[str, str] | None = None,
    payment_form_values: dict[str, str] | None = None,
    payment_edit_target_id: int | None = None,
    payment_edit_errors: dict[str, str] | None = None,
    payment_edit_values: dict[str, str] | None = None,
    action_notice: str | None = None,
    action_error: str | None = None,
    show_archived: bool = True,
) -> dict[str, object]:
    return {
        "payments": list_payments(db, include_archived=show_archived),
        "payment_error": payment_error,
        "payment_form_errors": payment_form_errors or {},
        "payment_form_values": payment_form_values
        or {
            "name": "",
            "expected_amount": "",
            "initial_due_date": "",
            "recurrence_type": "monthly",
            "priority": "",
        },
        "payment_edit_target_id": payment_edit_target_id,
        "payment_edit_errors": payment_edit_errors or {},
        "payment_edit_values": payment_edit_values or {},
        "action_notice": action_notice,
        "action_error": action_error,
        "notifications_unread_count": get_unread_notifications_count(db),
        "payments_panel_target": "#payments-page-shell",
        "payments_create_path": "/payments/page/create",
        "payments_paid_off_path_prefix": "/payments/page",
        "payments_reactivate_path_prefix": "/payments/page",
        "payments_update_path_prefix": "/payments/page",
        "show_archived": show_archived,
        "payments_show_archived_path": "/payments",
    }


def _parse_payment_form_fields(
    *,
    name: str,
    expected_amount: str,
    initial_due_date: str,
    recurrence_type: str,
    priority: str = "",
) -> tuple[UpdatePaymentInput | None, dict[str, str], dict[str, str]]:
    errors: dict[str, str] = {}
    values = {
        "name": name,
        "expected_amount": expected_amount,
        "initial_due_date": initial_due_date,
        "recurrence_type": recurrence_type,
        "priority": priority,
    }

    clean_name = name.strip()
    if not clean_name:
        errors["name"] = "Name is required."

    amount_value: Decimal | None = None
    try:
        amount_value = Decimal(expected_amount)
        if amount_value < 0:
            errors["expected_amount"] = "Amount must be non-negative."
    except (InvalidOperation, ValueError):
        errors["expected_amount"] = "Enter a valid amount."

    due_date_value: date | None = None
    try:
        due_date_value = date.fromisoformat(initial_due_date)
    except (TypeError, ValueError):
        errors["initial_due_date"] = "Enter a valid date."

    if recurrence_type not in {"one_time", "weekly", "biweekly", "monthly", "yearly"}:
        errors["recurrence_type"] = "Choose a valid recurrence."

    priority_value: int | None = None
    if priority.strip():
        try:
            priority_value = int(priority)
        except ValueError:
            errors["priority"] = "Priority must be a whole number."

    if errors:
        return None, errors, values

    assert amount_value is not None and due_date_value is not None
    return (
        UpdatePaymentInput(
            name=clean_name,
            expected_amount=amount_value,
            initial_due_date=due_date_value,
            recurrence_type=recurrence_type,
            priority=priority_value,
        ),
        {},
        values,
    )


def _render_dashboard_page(
    request: Request,
    db: Session,
    **context_overrides,
):
    return templates.TemplateResponse(
        request,
        "index.html",
        _build_dashboard_context(db, **context_overrides),
    )


def _render_payments_page(
    request: Request,
    db: Session,
    **context_overrides,
):
    return templates.TemplateResponse(
        request,
        "payments.html",
        _build_payments_only_context(db, **context_overrides),
    )


def _render_upcoming_page(request: Request, db: Session):
    return templates.TemplateResponse(
        request,
        "upcoming.html",
        {
            "next_cycle_snapshot": get_cycle_snapshot(db, today=date.today(), which="next"),
            "notifications_unread_count": get_unread_notifications_count(db),
        },
    )


@web_router.get("/")
def root_redirect() -> RedirectResponse:
    return RedirectResponse(url="/dashboard", status_code=307)


@web_router.get("/dashboard")
def dashboard_page(
    request: Request,
    show_archived: str = "1",
    db: Session = Depends(get_db_session),
):
    # First-request-of-day fallback: safe to call on every request because job_runs guard de-dupes.
    run_generate_occurrences_once_per_day_in_session_if_ready(db, today=date.today())
    run_notification_jobs_once_per_day_in_session_if_ready(db, today=date.today(), now=datetime.now())
    return _render_dashboard_page(request, db, show_archived=_show_archived_enabled(show_archived))


@web_router.get("/payments")
def payments_page(
    request: Request,
    show_archived: str = "1",
    db: Session = Depends(get_db_session),
):
    run_generate_occurrences_once_per_day_in_session_if_ready(db, today=date.today())
    run_notification_jobs_once_per_day_in_session_if_ready(db, today=date.today(), now=datetime.now())
    return _render_payments_page(request, db, show_archived=_show_archived_enabled(show_archived))


@web_router.get("/upcoming")
def upcoming_page(request: Request, db: Session = Depends(get_db_session)):
    run_generate_occurrences_once_per_day_in_session_if_ready(db, today=date.today())
    run_notification_jobs_once_per_day_in_session_if_ready(db, today=date.today(), now=datetime.now())
    return _render_upcoming_page(request, db)


def _render_interactive_panels(
    request: Request,
    db: Session,
    *,
    payment_error: str | None = None,
    payment_form_errors: dict[str, str] | None = None,
    payment_form_values: dict[str, str] | None = None,
    payment_edit_target_id: int | None = None,
    payment_edit_errors: dict[str, str] | None = None,
    payment_edit_values: dict[str, str] | None = None,
    generation_state: dict[str, object] | None = None,
    action_notice: str | None = None,
    action_error: str | None = None,
    show_archived: bool = True,
):
    return templates.TemplateResponse(
        request,
        "_interactive_panels.html",
        _build_dashboard_context(
            db,
            payment_error=payment_error,
            payment_form_errors=payment_form_errors,
            payment_form_values=payment_form_values,
            payment_edit_target_id=payment_edit_target_id,
            payment_edit_errors=payment_edit_errors,
            payment_edit_values=payment_edit_values,
            generation_state=generation_state,
            action_notice=action_notice,
            action_error=action_error,
            show_archived=show_archived,
        ),
    )


def _render_generation_panel(request: Request, generation_state: dict[str, object] | None = None):
    return templates.TemplateResponse(
        request,
        "_generation_panel.html",
        {
            "generation_state": generation_state,
        },
    )


def _render_payments_page_shell(
    request: Request,
    db: Session,
    **context_overrides,
):
    return templates.TemplateResponse(
        request,
        "_payments_page_shell.html",
        _build_payments_only_context(db, **context_overrides),
    )


def _render_settings_page(
    request: Request,
    db: Session,
    *,
    settings_notice: str | None = None,
    settings_error: str | None = None,
    pay_schedule_errors: dict[str, str] | None = None,
    app_settings_errors: dict[str, str] | None = None,
):
    pay_schedule, app_settings = get_or_create_settings_rows(db)
    latest_telegram_delivery_error = get_latest_telegram_delivery_error(db)
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "pay_schedule": pay_schedule,
            "app_settings": app_settings,
            "settings_notice": settings_notice,
            "settings_error": settings_error,
            "pay_schedule_errors": pay_schedule_errors or {},
            "app_settings_errors": app_settings_errors or {},
            "notifications_unread_count": get_unread_notifications_count(db),
            "latest_telegram_delivery_error": latest_telegram_delivery_error,
        },
    )


def _render_notifications_page(
    request: Request,
    db: Session,
    *,
    notifications_notice: str | None = None,
    notifications_error: str | None = None,
    notifications_page_num: int = 1,
    notifications_per_page: int = 20,
    notifications_sort: str = "newest",
    delivery_log_page_num: int = 1,
    delivery_log_per_page: int = 20,
    delivery_log_sort: str = "newest",
    delivery_log_filters: NotificationLogFilters | None = None,
):
    notif_offset = max(notifications_page_num - 1, 0) * notifications_per_page
    log_offset = max(delivery_log_page_num - 1, 0) * delivery_log_per_page
    notifications_total = count_notifications(db)
    delivery_log_total = count_notification_logs_filtered(db, filters=delivery_log_filters)
    return templates.TemplateResponse(
        request,
        "notifications.html",
        {
            "notifications": list_notifications(
                db,
                limit=notifications_per_page,
                offset=notif_offset,
                sort=notifications_sort,
            ),
            "delivery_logs": list_notification_logs(
                db,
                limit=delivery_log_per_page,
                offset=log_offset,
                filters=delivery_log_filters,
                sort=delivery_log_sort,
            ),
            "notifications_unread_count": get_unread_notifications_count(db),
            "notifications_notice": notifications_notice,
            "notifications_error": notifications_error,
            "notifications_page_num": notifications_page_num,
            "notifications_per_page": notifications_per_page,
            "notifications_sort": notifications_sort,
            "notifications_total": notifications_total,
            "notifications_has_prev": notifications_page_num > 1,
            "notifications_has_next": notif_offset + notifications_per_page < notifications_total,
            "delivery_log_page_num": delivery_log_page_num,
            "delivery_log_per_page": delivery_log_per_page,
            "delivery_log_sort": delivery_log_sort,
            "delivery_log_total": delivery_log_total,
            "delivery_log_has_prev": delivery_log_page_num > 1,
            "delivery_log_has_next": log_offset + delivery_log_per_page < delivery_log_total,
            "delivery_log_filters": {
                "type": "" if delivery_log_filters is None or delivery_log_filters.type is None else delivery_log_filters.type,
                "channel": "" if delivery_log_filters is None or delivery_log_filters.channel is None else delivery_log_filters.channel,
                "status": "" if delivery_log_filters is None or delivery_log_filters.status is None else delivery_log_filters.status,
                "start_date": ""
                if delivery_log_filters is None or delivery_log_filters.start_date is None
                else delivery_log_filters.start_date.isoformat(),
                "end_date": ""
                if delivery_log_filters is None or delivery_log_filters.end_date is None
                else delivery_log_filters.end_date.isoformat(),
            },
        },
    )


def _notify_best_effort(
    db: Session,
    *,
    type: str,
    title: str,
    body: str,
    occurrence_id: int | None = None,
) -> None:
    try:
        create_in_app_notification(
            db,
            type=type,
            title=title,
            body=body,
            occurrence_id=occurrence_id,
        )
    except Exception:
        db.rollback()
        logger.exception("Failed to create in-app notification type=%s title=%s", type, title)


@web_router.post("/payments")
def create_payment_web(
    request: Request,
    name: str = Form(...),
    expected_amount: str = Form(...),
    initial_due_date: str = Form(...),
    recurrence_type: str = Form(...),
    priority: str = Form(""),
    show_archived: str = Form("1"),
    db: Session = Depends(get_db_session),
):
    show_archived_enabled = _show_archived_enabled(show_archived)
    try:
        parsed, field_errors, field_values = _parse_payment_form_fields(
            name=name,
            expected_amount=expected_amount,
            initial_due_date=initial_due_date,
            recurrence_type=recurrence_type,
            priority=priority,
        )
        if parsed is None:
            return _render_interactive_panels(
                request,
                db,
                payment_form_errors=field_errors,
                payment_form_values=field_values,
                action_error="Payment create failed.",
                show_archived=show_archived_enabled,
            )
        create_payment(
            db,
            CreatePaymentInput(
                name=parsed.name,
                expected_amount=parsed.expected_amount,
                initial_due_date=parsed.initial_due_date,
                recurrence_type=parsed.recurrence_type,
                priority=parsed.priority,
            ),
        )
        _notify_best_effort(db, type="payment_created", title="Payment Added", body=f"{parsed.name} was added.")
        return _render_interactive_panels(
            request,
            db,
            action_notice="Payment added.",
            show_archived=show_archived_enabled,
        )
    except (ValueError, InvalidOperation) as exc:
        return _render_interactive_panels(
            request,
            db,
            payment_error=str(exc),
            action_error="Payment create failed.",
            show_archived=show_archived_enabled,
        )


@web_router.post("/payments/page/create")
def create_payment_web_page(
    request: Request,
    name: str = Form(...),
    expected_amount: str = Form(...),
    initial_due_date: str = Form(...),
    recurrence_type: str = Form(...),
    priority: str = Form(""),
    show_archived: str = Form("1"),
    db: Session = Depends(get_db_session),
):
    show_archived_enabled = _show_archived_enabled(show_archived)
    parsed, field_errors, field_values = _parse_payment_form_fields(
        name=name,
        expected_amount=expected_amount,
        initial_due_date=initial_due_date,
        recurrence_type=recurrence_type,
        priority=priority,
    )
    if parsed is None:
        return _render_payments_page_shell(
            request,
            db,
            payment_form_errors=field_errors,
            payment_form_values=field_values,
            action_error="Payment create failed.",
            show_archived=show_archived_enabled,
        )
    try:
        create_payment(
            db,
            CreatePaymentInput(
                name=parsed.name,
                expected_amount=parsed.expected_amount,
                initial_due_date=parsed.initial_due_date,
                recurrence_type=parsed.recurrence_type,
                priority=parsed.priority,
            ),
        )
        _notify_best_effort(db, type="payment_created", title="Payment Added", body=f"{parsed.name} was added.")
        return _render_payments_page_shell(
            request,
            db,
            action_notice="Payment added.",
            show_archived=show_archived_enabled,
        )
    except ValueError as exc:
        return _render_payments_page_shell(
            request,
            db,
            action_error=str(exc),
            show_archived=show_archived_enabled,
        )


@web_router.post("/admin/run-generation")
def run_generation_web(
    request: Request,
    horizon_days: int = Form(90),
    show_archived: str = Form("1"),
    db: Session = Depends(get_db_session),
):
    show_archived_enabled = _show_archived_enabled(show_archived)
    if horizon_days < 1 or horizon_days > 365:
        horizon_days = 90

    result = generate_occurrences_ahead(db, today=date.today(), horizon_days=horizon_days)
    return _render_interactive_panels(
        request,
        db,
        generation_state={
            "generated_count": result.generated_count,
            "skipped_existing_count": result.skipped_existing_count,
            "range_start": result.range_start,
            "range_end": result.range_end,
            "horizon_days": horizon_days,
            "mode": "manual",
            "ran": True,
        },
        action_notice="Manual occurrence generation completed.",
        show_archived=show_archived_enabled,
    )


@web_router.post("/admin/run-generation-once-today")
def run_generation_once_today_web(
    request: Request,
    horizon_days: int = Form(90),
    show_archived: str = Form("1"),
    db: Session = Depends(get_db_session),
):
    show_archived_enabled = _show_archived_enabled(show_archived)
    if horizon_days < 1 or horizon_days > 365:
        horizon_days = 90

    guarded = run_generate_occurrences_once_per_day(db, today=date.today(), horizon_days=horizon_days)
    generation_state: dict[str, object] = {
        "mode": "guarded",
        "ran": guarded.ran,
        "run_date": guarded.run_date,
        "job_name": guarded.job_name,
        "horizon_days": horizon_days,
    }
    if guarded.generation_result is not None:
        generation_state.update(
            {
                "generated_count": guarded.generation_result.generated_count,
                "skipped_existing_count": guarded.generation_result.skipped_existing_count,
                "range_start": guarded.generation_result.range_start,
                "range_end": guarded.generation_result.range_end,
            }
        )
    return _render_interactive_panels(
        request,
        db,
        generation_state=generation_state,
        action_notice=(
            "Guarded daily generation executed." if guarded.ran else "Guard blocked duplicate daily generation."
        ),
        show_archived=show_archived_enabled,
    )


@web_router.post("/occurrences/{occurrence_id}/mark-paid")
def mark_paid_web(
    request: Request,
    occurrence_id: int,
    amount_paid: str = Form(""),
    paid_date: str = Form(""),
    show_archived: str = Form("1"),
    db: Session = Depends(get_db_session),
):
    show_archived_enabled = _show_archived_enabled(show_archived)
    try:
        parsed_amount = Decimal(amount_paid) if amount_paid.strip() else None
        parsed_paid_date = date.fromisoformat(paid_date) if paid_date.strip() else None
        mark_occurrence_paid(
            db,
            occurrence_id=occurrence_id,
            today=date.today(),
            amount_paid=parsed_amount,
            paid_date=parsed_paid_date,
        )
        _notify_best_effort(
            db,
            type="occurrence_completed",
            title="Payment Marked Paid",
            body=f"Occurrence #{occurrence_id} marked paid.",
            occurrence_id=occurrence_id,
        )
        return _render_interactive_panels(
            request,
            db,
            action_notice="Occurrence marked paid.",
            show_archived=show_archived_enabled,
        )
    except (ActionValidationError, InvalidOperation, ValueError) as exc:
        return _render_interactive_panels(
            request,
            db,
            action_error=str(exc),
            show_archived=show_archived_enabled,
        )


@web_router.post("/occurrences/{occurrence_id}/undo-paid")
def undo_mark_paid_web(
    request: Request,
    occurrence_id: int,
    show_archived: str = Form("1"),
    db: Session = Depends(get_db_session),
):
    show_archived_enabled = _show_archived_enabled(show_archived)
    try:
        undo_mark_paid(db, occurrence_id=occurrence_id)
        _notify_best_effort(
            db,
            type="occurrence_reopened",
            title="Paid Status Undone",
            body=f"Occurrence #{occurrence_id} returned to scheduled.",
            occurrence_id=occurrence_id,
        )
        return _render_interactive_panels(
            request,
            db,
            action_notice="Paid status undone.",
            show_archived=show_archived_enabled,
        )
    except ActionValidationError as exc:
        return _render_interactive_panels(
            request,
            db,
            action_error=str(exc),
            show_archived=show_archived_enabled,
        )


@web_router.post("/occurrences/{occurrence_id}/skip")
def skip_occurrence_web(
    request: Request,
    occurrence_id: int,
    show_archived: str = Form("1"),
    db: Session = Depends(get_db_session),
):
    show_archived_enabled = _show_archived_enabled(show_archived)
    try:
        skip_occurrence(db, occurrence_id=occurrence_id)
        _notify_best_effort(
            db,
            type="occurrence_skipped",
            title="Occurrence Skipped",
            body=f"Occurrence #{occurrence_id} skipped for this cycle.",
            occurrence_id=occurrence_id,
        )
        return _render_interactive_panels(
            request,
            db,
            action_notice="Occurrence skipped for this cycle.",
            show_archived=show_archived_enabled,
        )
    except ActionValidationError as exc:
        return _render_interactive_panels(
            request,
            db,
            action_error=str(exc),
            show_archived=show_archived_enabled,
        )


@web_router.post("/payments/{payment_id}/paid-off")
def mark_paid_off_web(
    request: Request,
    payment_id: int,
    paid_off_date: str = Form(""),
    show_archived: str = Form("1"),
    db: Session = Depends(get_db_session),
):
    show_archived_enabled = _show_archived_enabled(show_archived)
    try:
        resolved_date = date.fromisoformat(paid_off_date) if paid_off_date.strip() else date.today()
        result = mark_payment_paid_off(db, payment_id=payment_id, paid_off_date=resolved_date)
        _notify_best_effort(
            db,
            type="payment_paid_off",
            title="Payment Marked Paid Off",
            body=f"Payment #{payment_id} marked paid off. Canceled {result.canceled_occurrences_count} future occurrences.",
        )
        return _render_interactive_panels(
            request,
            db,
            action_notice=f"Payment marked paid off. Canceled {result.canceled_occurrences_count} future occurrences.",
            show_archived=show_archived_enabled,
        )
    except (ActionValidationError, ValueError) as exc:
        return _render_interactive_panels(
            request,
            db,
            action_error=str(exc),
            show_archived=show_archived_enabled,
        )


@web_router.post("/payments/{payment_id}/reactivate")
def reactivate_payment_web(
    request: Request,
    payment_id: int,
    show_archived: str = Form("1"),
    db: Session = Depends(get_db_session),
):
    show_archived_enabled = _show_archived_enabled(show_archived)
    try:
        result = reactivate_payment(db, payment_id=payment_id, today=date.today())
        _notify_best_effort(
            db,
            type="payment_reactivated",
            title="Payment Reactivated",
            body=f"Payment #{payment_id} reactivated with {result.generated_occurrences_count} generated occurrences.",
        )
        return _render_interactive_panels(
            request,
            db,
            action_notice=(
                f"Payment reactivated. Generated {result.generated_occurrences_count} future occurrences."
            ),
            show_archived=show_archived_enabled,
        )
    except ActionValidationError as exc:
        return _render_interactive_panels(
            request,
            db,
            action_error=str(exc),
            show_archived=show_archived_enabled,
        )


@web_router.post("/payments/page/{payment_id}/reactivate")
def reactivate_payment_web_page(
    request: Request,
    payment_id: int,
    show_archived: str = Form("1"),
    db: Session = Depends(get_db_session),
):
    show_archived_enabled = _show_archived_enabled(show_archived)
    try:
        result = reactivate_payment(db, payment_id=payment_id, today=date.today())
        _notify_best_effort(
            db,
            type="payment_reactivated",
            title="Payment Reactivated",
            body=f"Payment #{payment_id} reactivated with {result.generated_occurrences_count} generated occurrences.",
        )
        return _render_payments_page_shell(
            request,
            db,
            action_notice=f"Payment reactivated. Generated {result.generated_occurrences_count} future occurrences.",
            show_archived=show_archived_enabled,
        )
    except ActionValidationError as exc:
        return _render_payments_page_shell(
            request,
            db,
            action_error=str(exc),
            show_archived=show_archived_enabled,
        )


@web_router.post("/payments/{payment_id}/update")
def update_payment_web(
    request: Request,
    payment_id: int,
    name: str = Form(...),
    expected_amount: str = Form(...),
    initial_due_date: str = Form(...),
    recurrence_type: str = Form(...),
    priority: str = Form(""),
    show_archived: str = Form("1"),
    db: Session = Depends(get_db_session),
):
    show_archived_enabled = _show_archived_enabled(show_archived)
    parsed, field_errors, field_values = _parse_payment_form_fields(
        name=name,
        expected_amount=expected_amount,
        initial_due_date=initial_due_date,
        recurrence_type=recurrence_type,
        priority=priority,
    )
    if parsed is None:
        return _render_interactive_panels(
            request,
            db,
            payment_edit_target_id=payment_id,
            payment_edit_errors=field_errors,
            payment_edit_values=field_values,
            action_error="Payment update failed.",
            show_archived=show_archived_enabled,
        )
    try:
        result = update_payment_and_rebuild_future_scheduled(
            db,
            payment_id=payment_id,
            data=parsed,
            today=date.today(),
        )
        _notify_best_effort(
            db,
            type="payment_updated",
            title="Payment Updated",
            body=f"Payment #{payment_id} updated. Rebuilt {result.generated_occurrences_count} future occurrences.",
        )
        return _render_interactive_panels(
            request,
            db,
            action_notice=(
                f"Payment updated. Rebuilt {result.generated_occurrences_count} future scheduled occurrences."
            ),
            show_archived=show_archived_enabled,
        )
    except ActionValidationError as exc:
        return _render_interactive_panels(
            request,
            db,
            payment_edit_target_id=payment_id,
            payment_edit_values=field_values,
            action_error=str(exc),
            show_archived=show_archived_enabled,
        )


@web_router.post("/payments/page/{payment_id}/update")
def update_payment_web_page(
    request: Request,
    payment_id: int,
    name: str = Form(...),
    expected_amount: str = Form(...),
    initial_due_date: str = Form(...),
    recurrence_type: str = Form(...),
    priority: str = Form(""),
    show_archived: str = Form("1"),
    db: Session = Depends(get_db_session),
):
    show_archived_enabled = _show_archived_enabled(show_archived)
    parsed, field_errors, field_values = _parse_payment_form_fields(
        name=name,
        expected_amount=expected_amount,
        initial_due_date=initial_due_date,
        recurrence_type=recurrence_type,
        priority=priority,
    )
    if parsed is None:
        return _render_payments_page_shell(
            request,
            db,
            payment_edit_target_id=payment_id,
            payment_edit_errors=field_errors,
            payment_edit_values=field_values,
            action_error="Payment update failed.",
            show_archived=show_archived_enabled,
        )
    try:
        result = update_payment_and_rebuild_future_scheduled(
            db,
            payment_id=payment_id,
            data=parsed,
            today=date.today(),
        )
        _notify_best_effort(
            db,
            type="payment_updated",
            title="Payment Updated",
            body=f"Payment #{payment_id} updated. Rebuilt {result.generated_occurrences_count} future occurrences.",
        )
        return _render_payments_page_shell(
            request,
            db,
            action_notice=f"Payment updated. Rebuilt {result.generated_occurrences_count} future scheduled occurrences.",
            show_archived=show_archived_enabled,
        )
    except ActionValidationError as exc:
        return _render_payments_page_shell(
            request,
            db,
            payment_edit_target_id=payment_id,
            payment_edit_values=field_values,
            action_error=str(exc),
            show_archived=show_archived_enabled,
        )


@web_router.post("/payments/page/{payment_id}/paid-off")
def mark_paid_off_web_page(
    request: Request,
    payment_id: int,
    paid_off_date: str = Form(""),
    show_archived: str = Form("1"),
    db: Session = Depends(get_db_session),
):
    show_archived_enabled = _show_archived_enabled(show_archived)
    try:
        resolved_date = date.fromisoformat(paid_off_date) if paid_off_date.strip() else date.today()
        result = mark_payment_paid_off(db, payment_id=payment_id, paid_off_date=resolved_date)
        _notify_best_effort(
            db,
            type="payment_paid_off",
            title="Payment Marked Paid Off",
            body=f"Payment #{payment_id} marked paid off. Canceled {result.canceled_occurrences_count} future occurrences.",
        )
        return _render_payments_page_shell(
            request,
            db,
            action_notice=f"Payment marked paid off. Canceled {result.canceled_occurrences_count} future occurrences.",
            show_archived=show_archived_enabled,
        )
    except (ActionValidationError, ValueError) as exc:
        return _render_payments_page_shell(
            request,
            db,
            action_error=str(exc),
            show_archived=show_archived_enabled,
        )


@web_router.get("/history")
def history_page(
    request: Request,
    status: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    q: str | None = None,
    page: int = 1,
    per_page: int = 25,
    sort: str = "due_desc",
    db: Session = Depends(get_db_session),
):
    parsed_start = date.fromisoformat(start_date) if start_date else None
    parsed_end = date.fromisoformat(end_date) if end_date else None
    filters = HistoryFilters(
        status=status or None,
        start_date=parsed_start,
        end_date=parsed_end,
        q=q or None,
    )
    per_page = min(max(per_page, 1), 100)
    page = max(page, 1)
    history_page_result = list_occurrence_history_page(
        db,
        filters=filters,
        limit=per_page,
        offset=(page - 1) * per_page,
        sort=sort,
    )
    return templates.TemplateResponse(
        request,
        "history.html",
        {
            "history_rows": history_page_result.rows,
            "history_total": history_page_result.total_count,
            "history_page_num": page,
            "history_per_page": per_page,
            "history_sort": sort,
            "history_has_prev": page > 1,
            "history_has_next": ((page - 1) * per_page) + per_page < history_page_result.total_count,
            "notifications_unread_count": get_unread_notifications_count(db),
            "filters": {
                "status": status or "",
                "start_date": start_date or "",
                "end_date": end_date or "",
                "q": q or "",
            },
        },
    )


@web_router.get("/settings")
def settings_page(
    request: Request,
    db: Session = Depends(get_db_session),
    settings_notice: str | None = None,
    settings_error: str | None = None,
    pay_schedule_errors: dict[str, str] | None = None,
    app_settings_errors: dict[str, str] | None = None,
):
    return _render_settings_page(
        request,
        db,
        settings_notice=settings_notice,
        settings_error=settings_error,
        pay_schedule_errors=pay_schedule_errors,
        app_settings_errors=app_settings_errors,
    )


@web_router.post("/settings/pay-schedule")
def update_pay_schedule_web(
    request: Request,
    anchor_payday_date: str = Form(...),
    timezone: str = Form(...),
    db: Session = Depends(get_db_session),
):
    errors: dict[str, str] = {}
    try:
        parsed_anchor = date.fromisoformat(anchor_payday_date)
    except ValueError:
        errors["anchor_payday_date"] = "Enter a valid date."
        parsed_anchor = date.today()
    if not timezone.strip():
        errors["timezone"] = "Timezone is required."

    if errors:
        return _render_settings_page(
            request,
            db,
            settings_error="Pay schedule update failed.",
            pay_schedule_errors=errors,
        )
    try:
        update_pay_schedule(
            db,
            UpdatePayScheduleInput(anchor_payday_date=parsed_anchor, timezone=timezone),
        )
        return _render_settings_page(request, db, settings_notice="Pay schedule updated.")
    except SettingsValidationError as exc:
        return _render_settings_page(
            request,
            db,
            settings_error=str(exc),
            pay_schedule_errors={"timezone": str(exc)},
        )


@web_router.post("/settings/app")
def update_app_settings_web(
    request: Request,
    due_soon_days: str = Form(...),
    daily_summary_time: str = Form(...),
    telegram_enabled: str | None = Form(None),
    telegram_bot_token: str = Form(""),
    telegram_chat_id: str = Form(""),
    db: Session = Depends(get_db_session),
):
    errors: dict[str, str] = {}
    try:
        due_soon = int(due_soon_days)
        if due_soon < 0:
            raise ValueError
    except ValueError:
        errors["due_soon_days"] = "Enter a non-negative whole number."
        due_soon = 5

    if not daily_summary_time.strip():
        errors["daily_summary_time"] = "Daily summary time is required."

    if errors:
        return _render_settings_page(
            request,
            db,
            settings_error="App settings update failed.",
            app_settings_errors=errors,
        )
    try:
        update_app_settings(
            db,
            UpdateAppSettingsInput(
                due_soon_days=due_soon,
                daily_summary_time=daily_summary_time,
                telegram_enabled=telegram_enabled is not None,
                telegram_bot_token=telegram_bot_token,
                telegram_chat_id=telegram_chat_id,
            ),
        )
        return _render_settings_page(request, db, settings_notice="App settings updated.")
    except SettingsValidationError as exc:
        return _render_settings_page(
            request,
            db,
            settings_error=str(exc),
            app_settings_errors={"daily_summary_time": str(exc)},
        )


@web_router.post("/settings/telegram/test")
def send_test_telegram_message_web(
    request: Request,
    db: Session = Depends(get_db_session),
):
    _, app_settings = get_or_create_settings_rows(db)
    if not app_settings.telegram_enabled:
        return _render_settings_page(request, db, settings_error="Telegram is disabled.")
    if not app_settings.telegram_bot_token or not app_settings.telegram_chat_id:
        return _render_settings_page(
            request,
            db,
            settings_error="Telegram bot token and chat ID are required to send a test message.",
        )

    try:
        send_telegram_message(
            bot_token=app_settings.telegram_bot_token,
            chat_id=app_settings.telegram_chat_id,
            text="PayTrack test message: Telegram delivery is configured.",
        )
    except TelegramDeliveryError as exc:
        return _render_settings_page(request, db, settings_error=str(exc))

    _notify_best_effort(db, type="telegram_test", title="Telegram Test Sent", body="Telegram test message delivered.")
    return _render_settings_page(
        request,
        db,
        settings_notice="Telegram test message sent.",
    )


@web_router.get("/notifications")
def notifications_page(
    request: Request,
    page: int = 1,
    per_page: int = 20,
    sort: str = "newest",
    log_page: int = 1,
    log_per_page: int = 20,
    log_sort: str = "newest",
    log_type: str | None = None,
    log_channel: str | None = None,
    log_status: str | None = None,
    log_start_date: str | None = None,
    log_end_date: str | None = None,
    db: Session = Depends(get_db_session),
    notifications_notice: str | None = None,
    notifications_error: str | None = None,
):
    per_page = min(max(per_page, 1), 100)
    log_per_page = min(max(log_per_page, 1), 100)
    if sort not in {"newest", "oldest", "unread_first"}:
        sort = "newest"
    if log_sort not in {"newest", "oldest"}:
        log_sort = "newest"
    parsed_log_start = date.fromisoformat(log_start_date) if log_start_date else None
    parsed_log_end = date.fromisoformat(log_end_date) if log_end_date else None
    delivery_log_filters = NotificationLogFilters(
        type=(log_type or "").strip() or None,
        channel=(log_channel or "").strip() or None,
        status=(log_status or "").strip() or None,
        start_date=parsed_log_start,
        end_date=parsed_log_end,
    )
    return _render_notifications_page(
        request,
        db,
        notifications_notice=notifications_notice,
        notifications_error=notifications_error,
        notifications_page_num=max(page, 1),
        notifications_per_page=per_page,
        notifications_sort=sort,
        delivery_log_page_num=max(log_page, 1),
        delivery_log_per_page=log_per_page,
        delivery_log_sort=log_sort,
        delivery_log_filters=delivery_log_filters,
    )


@web_router.post("/notifications/{notification_id}/read")
def mark_notification_read_web(
    request: Request,
    notification_id: int,
    db: Session = Depends(get_db_session),
):
    try:
        mark_notification_read(db, notification_id=notification_id, now=datetime.now())
        return _render_notifications_page(request, db, notifications_notice="Notification marked read.")
    except NotificationsValidationError as exc:
        return _render_notifications_page(request, db, notifications_error=str(exc))


@web_router.post("/notifications/mark-all-read")
def mark_all_notifications_read_web(
    request: Request,
    db: Session = Depends(get_db_session),
):
    count = mark_all_notifications_read(db, now=datetime.now())
    return _render_notifications_page(request, db, notifications_notice=f"Marked {count} notifications read.")


@web_router.post("/notifications/run-jobs")
def run_notification_jobs_web(
    request: Request,
    force_daily_summary: str | None = Form(None),
    db: Session = Depends(get_db_session),
):
    force_daily = force_daily_summary is not None
    result = run_notification_jobs_now_if_ready(
        db,
        today=date.today(),
        now=datetime.now(),
        force_daily_summary=force_daily,
    )
    if result is None:
        return _render_notifications_page(request, db, notifications_error="Notification jobs are not ready yet.")
    notice = (
        "Notification jobs ran. "
        f"Daily summary: {result.daily_summary_created}, due soon: {result.due_soon_created}, "
        f"overdue: {result.overdue_created}, telegram sent: {result.telegram_sent}."
    )
    if result.telegram_errors:
        notice += f" Telegram errors: {result.telegram_errors}."
    if result.daily_summary_deferred_before_time and result.daily_summary_ready_time:
        notice += f" Daily summary deferred until {result.daily_summary_ready_time}."
    if force_daily:
        notice += " Daily summary force-run requested."
    return _render_notifications_page(request, db, notifications_notice=notice)


@web_router.post("/notifications/run-jobs-once-today")
def run_notification_jobs_once_today_web(
    request: Request,
    db: Session = Depends(get_db_session),
):
    result = run_notification_jobs_once_per_day_in_session_if_ready(db, today=date.today(), now=datetime.now())
    if result is None:
        return _render_notifications_page(request, db, notifications_error="Notification jobs are not ready yet.")
    if not result.ran:
        return _render_notifications_page(request, db, notifications_notice="Notification jobs already ran today.")
    notice = (
        "Notification jobs ran (guarded). "
        f"Daily summary: {result.daily_summary_created}, due soon: {result.due_soon_created}, "
        f"overdue: {result.overdue_created}, telegram sent: {result.telegram_sent}."
    )
    if result.telegram_errors:
        notice += f" Telegram errors: {result.telegram_errors}."
    if result.daily_summary_deferred_before_time and result.daily_summary_ready_time:
        notice += f" Daily summary deferred until {result.daily_summary_ready_time}."
    return _render_notifications_page(request, db, notifications_notice=notice)


@web_router.post("/notifications/run-daily-summary-now")
def run_daily_summary_now_web(
    request: Request,
    db: Session = Depends(get_db_session),
):
    result = run_notification_jobs_now_if_ready(
        db,
        today=date.today(),
        now=datetime.now(),
        force_daily_summary=True,
    )
    if result is None:
        return _render_notifications_page(request, db, notifications_error="Notification jobs are not ready yet.")
    notice = "Forced daily summary run executed."
    if result.daily_summary_created:
        notice += " Daily summary created."
    else:
        notice += " Daily summary already existed for today."
    if result.telegram_errors:
        notice += f" Telegram errors: {result.telegram_errors}."
    return _render_notifications_page(request, db, notifications_notice=notice)
