from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
import logging

from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.jobs import JobRun
from app.models.payments import Occurrence, Payment
from app.services.scheduling_service import PaymentScheduleSpec, ScheduledOccurrenceSeed, build_occurrence_seeds

logger = logging.getLogger(__name__)


DEFAULT_GENERATION_HORIZON_DAYS = 90
GENERATE_OCCURRENCES_JOB_NAME = "generate_occurrences_ahead"


@dataclass(frozen=True)
class OccurrenceGenerationResult:
    generated_count: int
    skipped_existing_count: int
    range_start: date
    range_end: date


@dataclass(frozen=True)
class GuardedOccurrenceGenerationRunResult:
    job_name: str
    run_date: date
    ran: bool
    generation_result: OccurrenceGenerationResult | None


def _to_payment_schedule_spec(payment: Payment) -> PaymentScheduleSpec:
    amount = payment.expected_amount
    if not isinstance(amount, Decimal):
        amount = Decimal(str(amount))

    return PaymentScheduleSpec(
        payment_id=payment.id,
        name=payment.name,
        expected_amount=amount,
        initial_due_date=payment.initial_due_date,
        recurrence_type=payment.recurrence_type,
        is_active=payment.is_active,
    )


def _seed_to_occurrence(seed: ScheduledOccurrenceSeed) -> Occurrence:
    return Occurrence(
        payment_id=seed.payment_id,
        due_date=seed.due_date,
        expected_amount=seed.expected_amount,
        status=seed.status,
    )


def generate_occurrences_ahead(
    session: Session,
    *,
    today: date,
    horizon_days: int = DEFAULT_GENERATION_HORIZON_DAYS,
) -> OccurrenceGenerationResult:
    range_start = today
    range_end = today + timedelta(days=horizon_days)

    payments = session.scalars(select(Payment).where(Payment.is_active.is_(True))).all()
    payment_specs = [_to_payment_schedule_spec(payment) for payment in payments]

    seeds = build_occurrence_seeds(payments=payment_specs, range_start=range_start, range_end=range_end)
    if not seeds:
        logger.info(
            "Occurrence generation produced no seeds range_start=%s range_end=%s active_payments=%s",
            range_start,
            range_end,
            len(payment_specs),
        )
        return OccurrenceGenerationResult(
            generated_count=0,
            skipped_existing_count=0,
            range_start=range_start,
            range_end=range_end,
        )

    existing_keys = set(
        session.execute(
            select(Occurrence.payment_id, Occurrence.due_date).where(
                Occurrence.due_date >= range_start,
                Occurrence.due_date <= range_end,
            )
        ).all()
    )

    to_insert = [seed for seed in seeds if (seed.payment_id, seed.due_date) not in existing_keys]
    skipped_existing_count = len(seeds) - len(to_insert)

    if to_insert:
        session.add_all([_seed_to_occurrence(seed) for seed in to_insert])
        try:
            session.commit()
        except IntegrityError:
            # Another process may have inserted rows after the pre-check.
            session.rollback()
            inserted = 0
            for seed in to_insert:
                session.add(_seed_to_occurrence(seed))
                try:
                    session.commit()
                    inserted += 1
                except IntegrityError:
                    session.rollback()
            return OccurrenceGenerationResult(
                generated_count=inserted,
                skipped_existing_count=skipped_existing_count + (len(to_insert) - inserted),
                range_start=range_start,
                range_end=range_end,
            )

    logger.info(
        "Occurrence generation completed range_start=%s range_end=%s generated=%s skipped_existing=%s active_payments=%s",
        range_start,
        range_end,
        len(to_insert),
        skipped_existing_count,
        len(payment_specs),
    )
    return OccurrenceGenerationResult(
        generated_count=len(to_insert),
        skipped_existing_count=skipped_existing_count,
        range_start=range_start,
        range_end=range_end,
    )


def try_mark_daily_job_run(session: Session, *, job_name: str, run_date: date) -> bool:
    session.add(JobRun(job_name=job_name, run_date=run_date))
    try:
        session.commit()
        return True
    except IntegrityError:
        session.rollback()
        return False


def run_generate_occurrences_once_per_day(
    session: Session,
    *,
    today: date,
    horizon_days: int = DEFAULT_GENERATION_HORIZON_DAYS,
) -> GuardedOccurrenceGenerationRunResult:
    did_mark = try_mark_daily_job_run(
        session,
        job_name=GENERATE_OCCURRENCES_JOB_NAME,
        run_date=today,
    )
    if not did_mark:
        logger.info("Occurrence generation guard skip job=%s run_date=%s", GENERATE_OCCURRENCES_JOB_NAME, today)
        return GuardedOccurrenceGenerationRunResult(
            job_name=GENERATE_OCCURRENCES_JOB_NAME,
            run_date=today,
            ran=False,
            generation_result=None,
        )

    generation_result = generate_occurrences_ahead(session, today=today, horizon_days=horizon_days)
    logger.info(
        "Occurrence generation guard run job=%s run_date=%s generated=%s skipped_existing=%s",
        GENERATE_OCCURRENCES_JOB_NAME,
        today,
        generation_result.generated_count,
        generation_result.skipped_existing_count,
    )
    return GuardedOccurrenceGenerationRunResult(
        job_name=GENERATE_OCCURRENCES_JOB_NAME,
        run_date=today,
        ran=True,
        generation_result=generation_result,
    )


def run_generate_occurrences_once_per_day_in_session_if_ready(
    session: Session,
    *,
    today: date,
    horizon_days: int = DEFAULT_GENERATION_HORIZON_DAYS,
) -> GuardedOccurrenceGenerationRunResult | None:
    inspector = inspect(session.bind)
    tables = set(inspector.get_table_names())
    if not {"payments", "occurrences", "job_runs"}.issubset(tables):
        logger.debug("Occurrence generation readiness check failed tables=%s", ",".join(sorted(tables)))
        return None
    return run_generate_occurrences_once_per_day(session, today=today, horizon_days=horizon_days)


def generate_occurrences_ahead_if_ready(
    *,
    today: date,
    horizon_days: int = DEFAULT_GENERATION_HORIZON_DAYS,
) -> OccurrenceGenerationResult | None:
    with SessionLocal() as session:
        inspector = inspect(session.bind)
        tables = set(inspector.get_table_names())
        if not {"payments", "occurrences"}.issubset(tables):
            return None
        return generate_occurrences_ahead(session, today=today, horizon_days=horizon_days)


def run_generate_occurrences_once_per_day_if_ready(
    *,
    today: date,
    horizon_days: int = DEFAULT_GENERATION_HORIZON_DAYS,
) -> GuardedOccurrenceGenerationRunResult | None:
    with SessionLocal() as session:
        return run_generate_occurrences_once_per_day_in_session_if_ready(
            session,
            today=today,
            horizon_days=horizon_days,
        )


def ensure_daily_generation_via_guard_if_ready(
    *,
    today: date,
    horizon_days: int = DEFAULT_GENERATION_HORIZON_DAYS,
) -> GuardedOccurrenceGenerationRunResult | None:
    # Thin alias intended for cron or first-request fallback call sites.
    return run_generate_occurrences_once_per_day_if_ready(today=today, horizon_days=horizon_days)
