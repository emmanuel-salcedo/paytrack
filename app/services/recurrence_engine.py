from __future__ import annotations

import calendar
from datetime import date, timedelta


def _days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _add_months(year: int, month: int, offset: int) -> tuple[int, int]:
    zero_based = (year * 12 + (month - 1)) + offset
    return zero_based // 12, (zero_based % 12) + 1


def _monthly_occurrence(initial_due_date: date, month_offset: int) -> date:
    desired_dom = initial_due_date.day
    year, month = _add_months(initial_due_date.year, initial_due_date.month, month_offset)
    dom = min(desired_dom, _days_in_month(year, month))
    return date(year, month, dom)


def _yearly_occurrence(initial_due_date: date, year_offset: int) -> date:
    year = initial_due_date.year + year_offset
    month = initial_due_date.month
    dom = min(initial_due_date.day, _days_in_month(year, month))
    return date(year, month, dom)


def _generate_fixed_step(
    initial_due_date: date, range_start: date, range_end: date, step_days: int
) -> list[date]:
    if range_end < range_start or range_end < initial_due_date:
        return []

    if range_start <= initial_due_date:
        current = initial_due_date
    else:
        delta = (range_start - initial_due_date).days
        steps = (delta + step_days - 1) // step_days
        current = initial_due_date + timedelta(days=steps * step_days)

    results: list[date] = []
    while current <= range_end:
        if current >= range_start:
            results.append(current)
        current += timedelta(days=step_days)
    return results


def generate_due_dates(
    *,
    recurrence_type: str,
    initial_due_date: date,
    range_start: date,
    range_end: date,
) -> list[date]:
    if range_end < range_start:
        return []

    if recurrence_type == "one_time":
        if range_start <= initial_due_date <= range_end:
            return [initial_due_date]
        return []

    if recurrence_type == "weekly":
        return _generate_fixed_step(initial_due_date, range_start, range_end, 7)

    if recurrence_type == "biweekly":
        return _generate_fixed_step(initial_due_date, range_start, range_end, 14)

    if recurrence_type == "monthly":
        results: list[date] = []
        month_offset = 0

        while True:
            occurrence = _monthly_occurrence(initial_due_date, month_offset)
            if occurrence > range_end:
                break
            if occurrence >= initial_due_date and occurrence >= range_start:
                results.append(occurrence)
            month_offset += 1

        return results

    if recurrence_type == "yearly":
        results: list[date] = []
        year_offset = 0

        while True:
            occurrence = _yearly_occurrence(initial_due_date, year_offset)
            if occurrence > range_end:
                break
            if occurrence >= initial_due_date and occurrence >= range_start:
                results.append(occurrence)
            year_offset += 1

        return results

    raise ValueError(f"Unsupported recurrence_type: {recurrence_type}")

