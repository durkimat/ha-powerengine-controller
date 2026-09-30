"""The overnight window survives a day whose smart slots are priced a little below that night's rate."""
from datetime import datetime, timedelta, timezone

from pe_core.readings import Window
from pe_core.tariff import cheap_tods, overnight_window

HALF = timedelta(minutes=30)


def _day(date, night, slot, peak, slot_hh=()):
    """48 half-hours in UTC: 00:00-05:30 at `night`, the half-hours in `slot_hh` at `slot`, the rest at `peak`."""
    start = datetime(*date, tzinfo=timezone.utc)
    out = []
    for i in range(48):
        v = night if i < 12 else (slot if i in slot_hh else peak)
        out.append(Window(start + i * HALF, start + (i + 1) * HALF, v))
    return out


def test_night_at_the_old_rate_stays_cheap_when_slots_are_priced_lower():
    # 30 Sep 2026: the night at 6.993p, EDF's smart slots already at the new 6.66p
    day1 = _day((2026, 9, 29), 0.06993, 0.06993, 0.3028, slot_hh=range(20, 26))
    day2 = _day((2026, 9, 30), 0.06993, 0.0666, 0.3028, slot_hh=range(16, 30))
    day3 = _day((2026, 10, 1), 0.0666, 0.0666, 0.2884)
    window = overnight_window(cheap_tods(day1 + day2 + day3))
    assert window == set(range(12))


def test_peak_is_never_cheap_and_daytime_slots_drop_out():
    day1 = _day((2026, 9, 29), 0.07, 0.07, 0.30, slot_hh=range(20, 26))
    day2 = _day((2026, 9, 30), 0.07, 0.07, 0.30)
    assert overnight_window(cheap_tods(day1 + day2)) == set(range(12))
