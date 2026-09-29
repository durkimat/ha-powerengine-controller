"""The last special event is reported whole: back-to-back half-hours of one event add up."""

from pe_core.costbook import CostBook


def _rec(start, kwh, event="axle"):
    return {"start": start, "seconds": 1800,
            "v": {"event": event, "event_kwh": kwh, "event_gross": kwh * 1.15, "event_energy": kwh * 0.07,
                  "event_net": kwh * 1.08}}


def test_back_to_back_half_hours_make_one_event(tmp_path):
    book = CostBook(str(tmp_path))
    book._note_event(_rec("2026-09-28T17:00:00+00:00", 1.23))
    book._note_event(_rec("2026-09-28T17:30:00+00:00", 1.37))
    ev = book.last_event
    assert ev["time"] == "17:00" and ev["half_hours"] == 2
    assert ev["kwh"] == 2.6 and ev["gross"] == round(2.6 * 1.15, 2)


def test_a_gap_starts_a_new_event(tmp_path):
    book = CostBook(str(tmp_path))
    book._note_event(_rec("2026-09-24T17:00:00+00:00", 3.5))
    book._note_event(_rec("2026-09-28T17:00:00+00:00", 1.2))
    assert book.last_event["date"] == "2026-09-28" and book.last_event["kwh"] == 1.2
    book._note_event(_rec("2026-09-28T17:30:00+00:00", 0.05))        # next to no energy: ignored
    assert book.last_event["kwh"] == 1.2
