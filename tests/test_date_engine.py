from datetime import date, timedelta

from app.services.date_engine import PayCycle, cycle_for_date, is_payday


ANCHOR = date(2026, 1, 15)


def test_cycle_boundaries_around_anchor_payday() -> None:
    cycle_on_payday = cycle_for_date(date(2026, 1, 15), ANCHOR)
    assert cycle_on_payday == PayCycle(start=date(2026, 1, 15), end=date(2026, 1, 28))

    cycle_day_before = cycle_for_date(date(2026, 1, 14), ANCHOR)
    assert cycle_day_before == PayCycle(start=date(2026, 1, 1), end=date(2026, 1, 14))

    cycle_day_after = cycle_for_date(date(2026, 1, 16), ANCHOR)
    assert cycle_day_after == cycle_on_payday


def test_cycle_is_inclusive_and_always_14_days() -> None:
    cycle = cycle_for_date(date(2026, 2, 11), ANCHOR)
    assert cycle.start == date(2026, 1, 29)
    assert cycle.end == date(2026, 2, 11)
    assert (cycle.end - cycle.start).days == 13
    assert cycle.contains(date(2026, 1, 29))
    assert cycle.contains(date(2026, 2, 11))
    assert not cycle.contains(date(2026, 1, 28))
    assert not cycle.contains(date(2026, 2, 12))


def test_payday_cadence_matches_every_14_days_from_anchor_long_range() -> None:
    d = ANCHOR
    for _ in range(120):
        assert is_payday(d, ANCHOR)
        assert d.weekday() == 3  # Thursday
        d += timedelta(days=14)

    # Spot-check future date still resolves to the correct cycle end cadence.
    target = date(2032, 7, 1)
    cycle = cycle_for_date(target, ANCHOR)
    assert cycle.start.weekday() == 3
    assert ((cycle.start - ANCHOR).days % 14) == 0
    assert cycle.contains(target)
