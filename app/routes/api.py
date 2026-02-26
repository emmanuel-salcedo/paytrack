from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db import get_db_session
from app.models.payments import Payment
from app.services.actions_service import (
    ActionValidationError,
    mark_occurrence_paid,
    mark_payment_paid_off,
    reactivate_payment,
    skip_occurrence,
    undo_mark_paid,
    update_payment_and_rebuild_future_scheduled,
    UpdatePaymentInput,
)
from app.services.cycle_views_service import get_cycle_snapshot
from app.services.notification_jobs_service import (
    run_notification_jobs_now_if_ready,
    run_notification_jobs_once_per_day_in_session_if_ready,
)
from app.services.notifications_service import create_in_app_notification
from app.services.occurrence_generation import (
    generate_occurrences_ahead,
    run_generate_occurrences_once_per_day_in_session_if_ready,
    run_generate_occurrences_once_per_day,
)
from app.services.telegram_service import TelegramDeliveryError, send_telegram_message
from app.services.payments_service import CreatePaymentInput, create_payment, list_payments
from app.services.settings_service import (
    SettingsValidationError,
    UpdateAppSettingsInput,
    UpdatePayScheduleInput,
    get_or_create_settings_rows,
    update_app_settings,
    update_pay_schedule,
)

api_router = APIRouter(tags=["api"])


class PaymentCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    expected_amount: Decimal = Field(ge=0)
    initial_due_date: date
    recurrence_type: str
    priority: int | None = None


class PaymentResponse(BaseModel):
    id: int
    name: str
    expected_amount: Decimal
    initial_due_date: date
    recurrence_type: str
    priority: int | None
    is_active: bool

    @classmethod
    def from_model(cls, payment: Payment) -> "PaymentResponse":
        return cls(
            id=payment.id,
            name=payment.name,
            expected_amount=Decimal(str(payment.expected_amount)),
            initial_due_date=payment.initial_due_date,
            recurrence_type=payment.recurrence_type,
            priority=payment.priority,
            is_active=payment.is_active,
        )


class ManualGenerationRequest(BaseModel):
    today: date | None = None
    horizon_days: int = Field(default=90, ge=1, le=365)


class MarkPaidRequest(BaseModel):
    today: date | None = None
    amount_paid: Decimal | None = Field(default=None, ge=0)
    paid_date: date | None = None


class PaidOffRequest(BaseModel):
    paid_off_date: date | None = None


class ReactivatePaymentRequest(BaseModel):
    today: date | None = None
    horizon_days: int = Field(default=90, ge=1, le=365)


class UpdatePaymentRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    expected_amount: Decimal = Field(ge=0)
    initial_due_date: date
    recurrence_type: str
    priority: int | None = None
    today: date | None = None
    horizon_days: int = Field(default=90, ge=1, le=365)


class PayScheduleUpdateRequest(BaseModel):
    anchor_payday_date: date
    timezone: str = Field(min_length=1, max_length=64)


class AppSettingsUpdateRequest(BaseModel):
    due_soon_days: int = Field(ge=0, le=365)
    daily_summary_time: str
    telegram_enabled: bool = False
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None


def _serialize_settings(db: Session) -> dict[str, object]:
    pay_schedule, app_settings = get_or_create_settings_rows(db)
    return {
        "pay_schedule": {
            "anchor_payday_date": pay_schedule.anchor_payday_date.isoformat(),
            "timezone": pay_schedule.timezone,
        },
        "app_settings": {
            "due_soon_days": app_settings.due_soon_days,
            "daily_summary_time": app_settings.daily_summary_time,
            "telegram_enabled": app_settings.telegram_enabled,
            "telegram_bot_token": app_settings.telegram_bot_token,
            "telegram_chat_id": app_settings.telegram_chat_id,
        },
    }


def _serialize_cycle_snapshot(snapshot) -> dict[str, object]:
    return {
        "label": snapshot.label,
        "cycle_start": snapshot.cycle_start.isoformat(),
        "cycle_end": snapshot.cycle_end.isoformat(),
        "totals": {
            "scheduled": str(snapshot.scheduled_total),
            "paid": str(snapshot.paid_total),
            "skipped": str(snapshot.skipped_total),
            "remaining": str(snapshot.remaining_total),
        },
        "occurrence_count": snapshot.occurrence_count,
        "occurrences": [
            {
                "occurrence_id": row.occurrence_id,
                "payment_id": row.payment_id,
                "payment_name": row.payment_name,
                "due_date": row.due_date.isoformat(),
                "expected_amount": str(row.expected_amount),
                "status": row.status,
            }
            for row in snapshot.occurrences
        ],
    }


def _serialize_occurrence_action_result(occurrence) -> dict[str, object]:
    return {
        "occurrence_id": occurrence.id,
        "payment_id": occurrence.payment_id,
        "status": occurrence.status,
        "due_date": occurrence.due_date.isoformat(),
        "expected_amount": str(occurrence.expected_amount),
        "amount_paid": None if occurrence.amount_paid is None else str(occurrence.amount_paid),
        "paid_date": None if occurrence.paid_date is None else occurrence.paid_date.isoformat(),
    }


@api_router.get("/health")
def health_check(db: Session = Depends(get_db_session)) -> dict[str, str]:
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"status": "ok"}


@api_router.get("/payments", response_model=list[PaymentResponse])
def payments_list(db: Session = Depends(get_db_session)) -> list[PaymentResponse]:
    return [PaymentResponse.from_model(payment) for payment in list_payments(db)]


@api_router.post("/payments", response_model=PaymentResponse, status_code=201)
def payments_create(payload: PaymentCreateRequest, db: Session = Depends(get_db_session)) -> PaymentResponse:
    try:
        payment = create_payment(
            db,
            CreatePaymentInput(
                name=payload.name,
                expected_amount=payload.expected_amount,
                initial_due_date=payload.initial_due_date,
                recurrence_type=payload.recurrence_type,
                priority=payload.priority,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PaymentResponse.from_model(payment)


@api_router.get("/settings")
def get_settings_api(db: Session = Depends(get_db_session)) -> dict[str, object]:
    return _serialize_settings(db)


@api_router.post("/settings/pay-schedule")
def update_pay_schedule_api(
    payload: PayScheduleUpdateRequest,
    db: Session = Depends(get_db_session),
) -> dict[str, object]:
    try:
        row = update_pay_schedule(
            db,
            UpdatePayScheduleInput(
                anchor_payday_date=payload.anchor_payday_date,
                timezone=payload.timezone,
            ),
        )
    except SettingsValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "anchor_payday_date": row.anchor_payday_date.isoformat(),
        "timezone": row.timezone,
    }


@api_router.post("/settings/app")
def update_app_settings_api(
    payload: AppSettingsUpdateRequest,
    db: Session = Depends(get_db_session),
) -> dict[str, object]:
    try:
        row = update_app_settings(
            db,
            UpdateAppSettingsInput(
                due_soon_days=payload.due_soon_days,
                daily_summary_time=payload.daily_summary_time,
                telegram_enabled=payload.telegram_enabled,
                telegram_bot_token=payload.telegram_bot_token,
                telegram_chat_id=payload.telegram_chat_id,
            ),
        )
    except SettingsValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "due_soon_days": row.due_soon_days,
        "daily_summary_time": row.daily_summary_time,
        "telegram_enabled": row.telegram_enabled,
        "telegram_bot_token": row.telegram_bot_token,
        "telegram_chat_id": row.telegram_chat_id,
    }


@api_router.post("/settings/telegram/test")
def send_test_telegram_message_api(db: Session = Depends(get_db_session)) -> dict[str, object]:
    _, app_settings = get_or_create_settings_rows(db)
    if not app_settings.telegram_enabled:
        raise HTTPException(status_code=400, detail="Telegram is disabled.")
    if not app_settings.telegram_bot_token or not app_settings.telegram_chat_id:
        raise HTTPException(
            status_code=400,
            detail="Telegram bot token and chat ID are required to send a test message.",
        )
    try:
        send_telegram_message(
            bot_token=app_settings.telegram_bot_token,
            chat_id=app_settings.telegram_chat_id,
            text="PayTrack test message: Telegram delivery is configured.",
        )
    except TelegramDeliveryError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    row = create_in_app_notification(db, type="telegram_test", title="Telegram Test Sent", body="Telegram test message delivered.")
    return {
        "sent": True,
        "simulated": False,
        "notification_id": row.id,
        "message": "Telegram test message sent.",
    }


@api_router.post("/admin/run-generation")
def manual_run_generation(payload: ManualGenerationRequest, db: Session = Depends(get_db_session)) -> dict[str, object]:
    run_today = payload.today or date.today()
    result = generate_occurrences_ahead(db, today=run_today, horizon_days=payload.horizon_days)
    return {
        "generated_count": result.generated_count,
        "skipped_existing_count": result.skipped_existing_count,
        "range_start": result.range_start.isoformat(),
        "range_end": result.range_end.isoformat(),
        "horizon_days": payload.horizon_days,
    }


@api_router.post("/admin/run-generation-once-today")
def manual_run_generation_once_today(
    payload: ManualGenerationRequest,
    db: Session = Depends(get_db_session),
) -> dict[str, object]:
    run_today = payload.today or date.today()
    guarded = run_generate_occurrences_once_per_day(db, today=run_today, horizon_days=payload.horizon_days)

    response: dict[str, object] = {
        "job_name": guarded.job_name,
        "run_date": guarded.run_date.isoformat(),
        "ran": guarded.ran,
        "horizon_days": payload.horizon_days,
    }
    if guarded.generation_result is not None:
        response.update(
            {
                "generated_count": guarded.generation_result.generated_count,
                "skipped_existing_count": guarded.generation_result.skipped_existing_count,
                "range_start": guarded.generation_result.range_start.isoformat(),
                "range_end": guarded.generation_result.range_end.isoformat(),
            }
        )
    return response


@api_router.get("/cycles/current")
def current_cycle_snapshot_api(
    today: date | None = Query(default=None),
    db: Session = Depends(get_db_session),
) -> dict[str, object]:
    snapshot = get_cycle_snapshot(db, today=today or date.today(), which="current")
    return _serialize_cycle_snapshot(snapshot)


@api_router.get("/cycles/next")
def next_cycle_snapshot_api(
    today: date | None = Query(default=None),
    db: Session = Depends(get_db_session),
) -> dict[str, object]:
    snapshot = get_cycle_snapshot(db, today=today or date.today(), which="next")
    return _serialize_cycle_snapshot(snapshot)


@api_router.post("/admin/ensure-daily-generation")
def ensure_daily_generation_api(payload: ManualGenerationRequest, db: Session = Depends(get_db_session)) -> dict[str, object]:
    run_today = payload.today or date.today()
    guarded = run_generate_occurrences_once_per_day_in_session_if_ready(
        db,
        today=run_today,
        horizon_days=payload.horizon_days,
    )
    if guarded is None:
        return {
            "trigger": "ensure-daily-generation",
            "ready": False,
            "ran": False,
            "horizon_days": payload.horizon_days,
        }
    response: dict[str, object] = {
        "job_name": guarded.job_name,
        "run_date": guarded.run_date.isoformat(),
        "ran": guarded.ran,
        "horizon_days": payload.horizon_days,
        "trigger": "ensure-daily-generation",
        "ready": True,
    }
    if guarded.generation_result is not None:
        response.update(
            {
                "generated_count": guarded.generation_result.generated_count,
                "skipped_existing_count": guarded.generation_result.skipped_existing_count,
                "range_start": guarded.generation_result.range_start.isoformat(),
                "range_end": guarded.generation_result.range_end.isoformat(),
            }
        )
    return response


@api_router.post("/admin/run-notification-jobs")
def run_notification_jobs_api(
    today: date | None = Query(default=None),
    db: Session = Depends(get_db_session),
) -> dict[str, object]:
    result = run_notification_jobs_now_if_ready(db, today=today or date.today())
    if result is None:
        return {"ready": False, "ran": False, "job_name": "run_notification_jobs"}
    return {
        "ready": True,
        "ran": result.ran,
        "job_name": result.job_name,
        "run_date": result.run_date.isoformat(),
        "daily_summary_created": result.daily_summary_created,
        "due_soon_created": result.due_soon_created,
        "overdue_created": result.overdue_created,
        "telegram_sent": result.telegram_sent,
        "telegram_errors": result.telegram_errors,
    }


@api_router.post("/admin/run-notification-jobs-once-today")
def run_notification_jobs_once_today_api(
    today: date | None = Query(default=None),
    db: Session = Depends(get_db_session),
) -> dict[str, object]:
    result = run_notification_jobs_once_per_day_in_session_if_ready(db, today=today or date.today())
    if result is None:
        return {"ready": False, "ran": False, "job_name": "run_notification_jobs"}
    return {
        "ready": True,
        "ran": result.ran,
        "job_name": result.job_name,
        "run_date": result.run_date.isoformat(),
        "daily_summary_created": result.daily_summary_created,
        "due_soon_created": result.due_soon_created,
        "overdue_created": result.overdue_created,
        "telegram_sent": result.telegram_sent,
        "telegram_errors": result.telegram_errors,
    }


@api_router.post("/occurrences/{occurrence_id}/mark-paid")
def mark_paid_api(
    occurrence_id: int,
    payload: MarkPaidRequest,
    db: Session = Depends(get_db_session),
) -> dict[str, object]:
    try:
        occurrence = mark_occurrence_paid(
            db,
            occurrence_id=occurrence_id,
            today=payload.today or date.today(),
            amount_paid=payload.amount_paid,
            paid_date=payload.paid_date,
        )
    except ActionValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _serialize_occurrence_action_result(occurrence)


@api_router.post("/occurrences/{occurrence_id}/undo-paid")
def undo_mark_paid_api(occurrence_id: int, db: Session = Depends(get_db_session)) -> dict[str, object]:
    try:
        occurrence = undo_mark_paid(db, occurrence_id=occurrence_id)
    except ActionValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _serialize_occurrence_action_result(occurrence)


@api_router.post("/occurrences/{occurrence_id}/skip")
def skip_occurrence_api(occurrence_id: int, db: Session = Depends(get_db_session)) -> dict[str, object]:
    try:
        occurrence = skip_occurrence(db, occurrence_id=occurrence_id)
    except ActionValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _serialize_occurrence_action_result(occurrence)


@api_router.post("/payments/{payment_id}/paid-off")
def paid_off_payment_api(
    payment_id: int,
    payload: PaidOffRequest,
    db: Session = Depends(get_db_session),
) -> dict[str, object]:
    try:
        result = mark_payment_paid_off(
            db,
            payment_id=payment_id,
            paid_off_date=payload.paid_off_date or date.today(),
        )
    except ActionValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "payment_id": result.payment_id,
        "paid_off_date": result.paid_off_date.isoformat(),
        "canceled_occurrences_count": result.canceled_occurrences_count,
    }


@api_router.post("/payments/{payment_id}/reactivate")
def reactivate_payment_api(
    payment_id: int,
    payload: ReactivatePaymentRequest,
    db: Session = Depends(get_db_session),
) -> dict[str, object]:
    try:
        result = reactivate_payment(
            db,
            payment_id=payment_id,
            today=payload.today or date.today(),
            horizon_days=payload.horizon_days,
        )
    except ActionValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "payment_id": result.payment_id,
        "generated_occurrences_count": result.generated_occurrences_count,
        "skipped_existing_count": result.skipped_existing_count,
    }


@api_router.post("/payments/{payment_id}/update")
def update_payment_api(
    payment_id: int,
    payload: UpdatePaymentRequest,
    db: Session = Depends(get_db_session),
) -> dict[str, object]:
    try:
        result = update_payment_and_rebuild_future_scheduled(
            db,
            payment_id=payment_id,
            data=UpdatePaymentInput(
                name=payload.name,
                expected_amount=payload.expected_amount,
                initial_due_date=payload.initial_due_date,
                recurrence_type=payload.recurrence_type,
                priority=payload.priority,
            ),
            today=payload.today or date.today(),
            horizon_days=payload.horizon_days,
        )
    except ActionValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "payment_id": result.payment_id,
        "generated_occurrences_count": result.generated_occurrences_count,
        "skipped_existing_count": result.skipped_existing_count,
    }
