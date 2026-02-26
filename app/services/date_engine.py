from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


PAY_CYCLE_LENGTH_DAYS = 14


@dataclass(frozen=True)
class PayCycle:
    start: date
    end: date

    def contains(self, due_date: date) -> bool:
        return self.start <= due_date <= self.end


def _ceil_div(numerator: int, denominator: int) -> int:
    return -((-numerator) // denominator)


def cycle_for_date(target_date: date, anchor_payday_date: date) -> PayCycle:
    delta_days = (target_date - anchor_payday_date).days
    payday_index = _ceil_div(delta_days, PAY_CYCLE_LENGTH_DAYS)
    cycle_end = anchor_payday_date + timedelta(days=payday_index * PAY_CYCLE_LENGTH_DAYS)
    cycle_start = cycle_end - timedelta(days=PAY_CYCLE_LENGTH_DAYS - 1)
    return PayCycle(start=cycle_start, end=cycle_end)


def is_payday(target_date: date, anchor_payday_date: date) -> bool:
    delta_days = (target_date - anchor_payday_date).days
    return delta_days % PAY_CYCLE_LENGTH_DAYS == 0


def next_cycle(cycle: PayCycle) -> PayCycle:
    next_end = cycle.end + timedelta(days=PAY_CYCLE_LENGTH_DAYS)
    return PayCycle(start=next_end - timedelta(days=PAY_CYCLE_LENGTH_DAYS - 1), end=next_end)

