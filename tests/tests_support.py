"""Small Readings builders for tariff/certainty tests."""
from datetime import datetime, timedelta, timezone

from pe_core.readings import Readings, Window

UTC = timezone.utc
NOW = datetime(2026, 9, 22, 10, 0, tzinfo=UTC)


def _day_rates(day0: datetime, slot_tods=()):
    out = []
    for i in range(48):
        t = day0 + timedelta(minutes=30 * i)
        cheap = i < 12 or i in slot_tods              # 00:00-06:00 overnight
        out.append(Window(t, t + timedelta(minutes=30), 0.07 if cheap else 0.30))
    return out


def readings_with_slot():
    day0 = NOW.replace(hour=0)
    slot_at = day0 + timedelta(hours=15)             # 15:00, a smart slot
    r = Readings(now=NOW)
    r.rates = _day_rates(day0, {30, 31}) + _day_rates(day0 + timedelta(days=1))
    r.dispatches = [Window(slot_at, slot_at + timedelta(hours=1))]
    r.import_rate, r.export_rate = 0.30, 0.15
    return r, slot_at


def readings_two_days():
    day0 = NOW.replace(hour=0)
    when = day0 + timedelta(days=1, hours=15)         # a smart slot tomorrow at 15:00 in the published rates
    r = Readings(now=NOW)
    r.rates = _day_rates(day0) + _day_rates(day0 + timedelta(days=1), {30, 31})
    r.import_rate, r.export_rate = 0.30, 0.15
    return r, when
