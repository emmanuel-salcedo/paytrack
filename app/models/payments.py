from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Date, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


RECURRENCE_TYPES = ("one_time", "weekly", "biweekly", "monthly", "yearly")
OCCURRENCE_STATUSES = ("scheduled", "completed", "skipped", "canceled")


class Payment(TimestampMixin, Base):
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint(
            "recurrence_type IN ('one_time','weekly','biweekly','monthly','yearly')",
            name="ck_payments_recurrence_type",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    expected_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    initial_due_date: Mapped[date] = mapped_column(Date, nullable=False)
    recurrence_type: Mapped[str] = mapped_column(String(24), nullable=False)
    priority: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    paid_off_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    occurrences: Mapped[list["Occurrence"]] = relationship(
        back_populates="payment",
        cascade="all, delete-orphan",
    )


class Occurrence(TimestampMixin, Base):
    __tablename__ = "occurrences"
    __table_args__ = (
        UniqueConstraint("payment_id", "due_date", name="uq_occurrences_payment_due_date"),
        CheckConstraint(
            "status IN ('scheduled','completed','skipped','canceled')",
            name="ck_occurrences_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("payments.id", ondelete="CASCADE"), nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    expected_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="scheduled")
    amount_paid: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    paid_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    payment: Mapped[Payment] = relationship(back_populates="occurrences")

