from datetime import date

from app.services.recurrence_engine import generate_due_dates


def test_one_time_recurrence_in_range() -> None:
    dates = generate_due_dates(
        recurrence_type="one_time",
        initial_due_date=date(2026, 1, 20),
        range_start=date(2026, 1, 1),
        range_end=date(2026, 2, 1),
    )
    assert dates == [date(2026, 1, 20)]


def test_weekly_and_biweekly_recurrence() -> None:
    weekly = generate_due_dates(
        recurrence_type="weekly",
        initial_due_date=date(2026, 1, 1),
        range_start=date(2026, 1, 10),
        range_end=date(2026, 1, 31),
    )
    assert weekly == [date(2026, 1, 15), date(2026, 1, 22), date(2026, 1, 29)]

    biweekly = generate_due_dates(
        recurrence_type="biweekly",
        initial_due_date=date(2026, 1, 1),
        range_start=date(2026, 1, 10),
        range_end=date(2026, 2, 15),
    )
    assert biweekly == [date(2026, 1, 15), date(2026, 1, 29), date(2026, 2, 12)]


def test_monthly_dom_clamps_and_returns_to_desired_day() -> None:
    dates = generate_due_dates(
        recurrence_type="monthly",
        initial_due_date=date(2026, 1, 31),
        range_start=date(2026, 1, 1),
        range_end=date(2026, 4, 30),
    )
    assert dates == [
        date(2026, 1, 31),
        date(2026, 2, 28),
        date(2026, 3, 31),
        date(2026, 4, 30),
    ]


def test_monthly_dom_handles_leap_year_february() -> None:
    dates = generate_due_dates(
        recurrence_type="monthly",
        initial_due_date=date(2028, 1, 31),
        range_start=date(2028, 1, 1),
        range_end=date(2028, 3, 31),
    )
    assert dates == [date(2028, 1, 31), date(2028, 2, 29), date(2028, 3, 31)]


def test_yearly_recurrence_long_range() -> None:
    dates = generate_due_dates(
        recurrence_type="yearly",
        initial_due_date=date(2026, 6, 15),
        range_start=date(2026, 1, 1),
        range_end=date(2031, 12, 31),
    )
    assert dates == [
        date(2026, 6, 15),
        date(2027, 6, 15),
        date(2028, 6, 15),
        date(2029, 6, 15),
        date(2030, 6, 15),
        date(2031, 6, 15),
    ]


def test_invalid_range_and_unsupported_type() -> None:
    assert generate_due_dates(
        recurrence_type="weekly",
        initial_due_date=date(2026, 1, 1),
        range_start=date(2026, 2, 1),
        range_end=date(2026, 1, 1),
    ) == []

    try:
        generate_due_dates(
            recurrence_type="daily",
            initial_due_date=date(2026, 1, 1),
            range_start=date(2026, 1, 1),
            range_end=date(2026, 1, 2),
        )
    except ValueError as exc:
        assert "Unsupported recurrence_type" in str(exc)
    else:
        raise AssertionError("Expected ValueError for unsupported recurrence type")

