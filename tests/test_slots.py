from datetime import datetime, timedelta, timezone

from pe_core.readings import Window
from pe_core.slots import SlotTracker

T0 = datetime(2026, 9, 26, 22, 0, tzinfo=timezone.utc)
M = timedelta(minutes=1)


def W(start_min, end_min, kwh=-3.0):
    return Window(T0 + start_min * M, T0 + end_min * M, kwh)


def run(tr, minutes, planned, completed=(), charging=False, car_w=0.0):
    changed = False
    for m in minutes:
        changed |= tr.update(T0 + m * M, planned, list(completed), charging, car_w, 60)
    return changed


def test_slot_used_by_the_car_is_done_with_its_energy(tmp_path):
    tr = SlotTracker(str(tmp_path / "s.json"))
    slot = W(60, 120)
    run(tr, range(0, 60), [slot])                                      # announced, waiting
    run(tr, range(60, 120), [slot], charging=True, car_w=7000)         # the car charges for the hour
    run(tr, range(120, 125), [], completed=[slot])
    rec = tr.slots[slot.start.isoformat()]
    assert rec["status"] == "done" and rec["confirmed"] and abs(rec["car_kwh"] - 7.0) < 0.01
    assert rec["planned_kwh"] == 3.0
    tr.save()
    assert SlotTracker(str(tmp_path / "s.json")).slots[slot.start.isoformat()]["status"] == "done"


def test_slot_that_vanishes_before_it_starts_is_cancelled():
    tr = SlotTracker()
    slot = W(60, 120)
    run(tr, range(0, 30), [slot])
    run(tr, range(30, 40), [])                                          # gone (e.g. unplugged)
    assert tr.slots[slot.start.isoformat()]["status"] == "cancelled"


def test_unplugging_mid_slot_cuts_it_short():
    tr = SlotTracker()
    slot = W(0, 120)
    run(tr, range(0, 30), [slot], charging=True, car_w=7000)
    run(tr, range(30, 35), [])
    rec = tr.slots[slot.start.isoformat()]
    assert rec["status"] == "cut_short" and abs(rec["car_kwh"] - 3.5) < 0.01


def test_car_finishing_early_shows_less_than_planned_and_summary_counts():
    tr = SlotTracker()
    a, b, c = W(0, 60), W(120, 180), W(240, 300)
    run(tr, range(0, 20), [a, b, c], charging=True, car_w=7000)         # car full after 20 minutes
    run(tr, range(20, 60), [a, b, c])
    run(tr, range(60, 200), [b, c])                                     # b runs with no car draw
    run(tr, range(200, 205), [])                                        # c withdrawn before it starts
    s = tr.summary(T0 + 205 * M)
    assert (s["slots"], s["used"], s["done_no_car"], s["cancelled"], s["cut_short"]) == (3, 1, 1, 1, 0)
    assert abs(s["car_kwh"] - 7000 * 20 / 60 / 1000) < 0.05 and s["planned_kwh"] == 9.0     # rounded to 0.1
    assert s["recent"][0]["time"] == "22:00–23:00"
