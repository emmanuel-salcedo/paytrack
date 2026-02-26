from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import get_db_session
from app.models import AppSettings, PaySchedule
from app.services.occurrence_generation import generate_occurrences_ahead
from app.services.payments_service import CreatePaymentInput, create_payment, list_payments

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))
web_router = APIRouter(tags=["web"])


@web_router.get("/")
def home(request: Request, db: Session = Depends(get_db_session)):
    schedule = db.query(PaySchedule).first()
    app_settings = db.query(AppSettings).first()
    payments = list_payments(db)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "schedule": schedule,
            "app_settings": app_settings,
            "payments": payments,
            "payment_error": None,
            "generation_state": None,
        },
    )


def _render_payments_panel(
    request: Request,
    db: Session,
    *,
    payment_error: str | None = None,
):
    return templates.TemplateResponse(
        request,
        "_payments_panel.html",
        {
            "payments": list_payments(db),
            "payment_error": payment_error,
        },
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
        return _render_payments_panel(request, db)
    except (ValueError, InvalidOperation) as exc:
        return _render_payments_panel(request, db, payment_error=str(exc))


@web_router.post("/admin/run-generation")
def run_generation_web(
    request: Request,
    horizon_days: int = Form(90),
    db: Session = Depends(get_db_session),
):
    if horizon_days < 1 or horizon_days > 365:
        horizon_days = 90

    result = generate_occurrences_ahead(db, today=date.today(), horizon_days=horizon_days)
    return _render_generation_panel(
        request,
        generation_state={
            "generated_count": result.generated_count,
            "skipped_existing_count": result.skipped_existing_count,
            "range_start": result.range_start,
            "range_end": result.range_end,
            "horizon_days": horizon_days,
        },
    )
