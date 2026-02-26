from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import get_db_session
from app.models import AppSettings, PaySchedule
from app.services.actions_service import (
    ActionValidationError,
    mark_occurrence_paid,
    mark_payment_paid_off,
    skip_occurrence,
    undo_mark_paid,
)
from app.services.cycle_views_service import get_cycle_snapshot
from app.services.occurrence_generation import (
    generate_occurrences_ahead,
    run_generate_occurrences_once_per_day_in_session_if_ready,
    run_generate_occurrences_once_per_day,
)
from app.services.payments_service import CreatePaymentInput, create_payment, list_payments

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))
web_router = APIRouter(tags=["web"])


def _build_dashboard_context(
    db: Session,
    *,
    payment_error: str | None = None,
    generation_state: dict[str, object] | None = None,
    action_notice: str | None = None,
    action_error: str | None = None,
) -> dict[str, object]:
    schedule = db.query(PaySchedule).first()
    app_settings = db.query(AppSettings).first()
    payments = list_payments(db)
    current_cycle_snapshot = get_cycle_snapshot(db, today=date.today(), which="current")
    next_cycle_snapshot = get_cycle_snapshot(db, today=date.today(), which="next")
    return {
        "schedule": schedule,
        "app_settings": app_settings,
        "payments": payments,
        "current_cycle_snapshot": current_cycle_snapshot,
        "next_cycle_snapshot": next_cycle_snapshot,
        "payment_error": payment_error,
        "generation_state": generation_state,
        "action_notice": action_notice,
        "action_error": action_error,
    }


@web_router.get("/")
def home(request: Request, db: Session = Depends(get_db_session)):
    # First-request-of-day fallback: safe to call on every request because job_runs guard de-dupes.
    run_generate_occurrences_once_per_day_in_session_if_ready(db, today=date.today())
    return templates.TemplateResponse(
        request,
        "index.html",
        _build_dashboard_context(db),
    )


def _render_interactive_panels(
    request: Request,
    db: Session,
    *,
    payment_error: str | None = None,
    generation_state: dict[str, object] | None = None,
    action_notice: str | None = None,
    action_error: str | None = None,
):
    return templates.TemplateResponse(
        request,
        "_interactive_panels.html",
        _build_dashboard_context(
            db,
            payment_error=payment_error,
            generation_state=generation_state,
            action_notice=action_notice,
            action_error=action_error,
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


@web_router.post("/payments")
def create_payment_web(
    request: Request,
    name: str = Form(...),
    expected_amount: str = Form(...),
    initial_due_date: str = Form(...),
    recurrence_type: str = Form(...),
    db: Session = Depends(get_db_session),
):
    try:
        amount = Decimal(expected_amount)
        due_date = date.fromisoformat(initial_due_date)
        create_payment(
            db,
            CreatePaymentInput(
                name=name,
                expected_amount=amount,
                initial_due_date=due_date,
                recurrence_type=recurrence_type,
            ),
        )
        return _render_interactive_panels(request, db, action_notice="Payment added.")
    except (ValueError, InvalidOperation) as exc:
        return _render_interactive_panels(request, db, payment_error=str(exc), action_error="Payment create failed.")


@web_router.post("/admin/run-generation")
def run_generation_web(
    request: Request,
    horizon_days: int = Form(90),
    db: Session = Depends(get_db_session),
):
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
    )


@web_router.post("/admin/run-generation-once-today")
def run_generation_once_today_web(
    request: Request,
    horizon_days: int = Form(90),
    db: Session = Depends(get_db_session),
):
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
    )


@web_router.post("/occurrences/{occurrence_id}/mark-paid")
def mark_paid_web(
    request: Request,
    occurrence_id: int,
    amount_paid: str = Form(""),
    paid_date: str = Form(""),
    db: Session = Depends(get_db_session),
):
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
        return _render_interactive_panels(request, db, action_notice="Occurrence marked paid.")
    except (ActionValidationError, InvalidOperation, ValueError) as exc:
        return _render_interactive_panels(request, db, action_error=str(exc))


@web_router.post("/occurrences/{occurrence_id}/undo-paid")
def undo_mark_paid_web(
    request: Request,
    occurrence_id: int,
    db: Session = Depends(get_db_session),
):
    try:
        undo_mark_paid(db, occurrence_id=occurrence_id)
        return _render_interactive_panels(request, db, action_notice="Paid status undone.")
    except ActionValidationError as exc:
        return _render_interactive_panels(request, db, action_error=str(exc))


@web_router.post("/occurrences/{occurrence_id}/skip")
def skip_occurrence_web(
    request: Request,
    occurrence_id: int,
    db: Session = Depends(get_db_session),
):
    try:
        skip_occurrence(db, occurrence_id=occurrence_id)
        return _render_interactive_panels(request, db, action_notice="Occurrence skipped for this cycle.")
    except ActionValidationError as exc:
        return _render_interactive_panels(request, db, action_error=str(exc))


@web_router.post("/payments/{payment_id}/paid-off")
def mark_paid_off_web(
    request: Request,
    payment_id: int,
    paid_off_date: str = Form(""),
    db: Session = Depends(get_db_session),
):
    try:
        resolved_date = date.fromisoformat(paid_off_date) if paid_off_date.strip() else date.today()
        result = mark_payment_paid_off(db, payment_id=payment_id, paid_off_date=resolved_date)
        return _render_interactive_panels(
            request,
            db,
            action_notice=f"Payment marked paid off. Canceled {result.canceled_occurrences_count} future occurrences.",
        )
    except (ActionValidationError, ValueError) as exc:
        return _render_interactive_panels(request, db, action_error=str(exc))
