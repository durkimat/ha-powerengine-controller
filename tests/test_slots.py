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
    assert abs(s["car_kwh"] - 7000 * 20 / 60 / 1000) < 0.05 and s["planned_kwh"] == 6.0  # a, b ran; c never did
    assert s["recent"][0]["time"] == "22:00–23:00"


def test_car_idle_after_a_slot_it_drew_nothing_in():
    from datetime import datetime, timedelta, timezone

    from pe_core.slots import SlotTracker
    now = datetime(2026, 9, 28, 7, 41, tzinfo=timezone.utc)
    t = SlotTracker()
    def rec(start_h, end_h, mins, status="done"):
        s, e = now.replace(hour=start_h, minute=0), now.replace(hour=end_h, minute=0)
        t.slots[s.isoformat()] = {"start": s.isoformat(), "end": e.isoformat(), "status": status,
                                  "car_kwh": mins * 0.12, "charging_min": float(mins), "confirmed": False}
    assert not t.car_idle(now)                               # nothing seen: assume it may charge
    rec(2, 3, 55)
    assert not t.car_idle(now)                               # it charged in the last one
    rec(6, 7, 0)
    assert t.car_idle(now)                                   # the latest passed with the car idle: full
    rec(7, 9, 0, status="planned")                           # running for 41 min, still nothing
    assert t.car_idle(now)
    t.slots[now.replace(hour=7, minute=0).isoformat()]["charging_min"] = 12.0
    assert not t.car_idle(now)                               # it has started drawing
    assert not t.car_idle(now + timedelta(hours=20))         # too long ago to tell


def _running(t, charging_pattern, dt_s=30):
    """Run a slot through a pattern of cycle states (True = the charger says charging); returns its record."""
    from datetime import timedelta

    from pe_core.readings import Window
    tr = t[0]
    start = t[1]
    w = Window(start, start + timedelta(hours=1), -7.0)
    now = start
    for charging in charging_pattern:
        now += timedelta(seconds=dt_s)
        tr.update(now, [w], [], charging, 7000 if charging else 0, dt_s)
    return tr, tr.slots[start.isoformat()]


def test_the_longest_unbroken_run_is_kept_not_the_total():
    from datetime import datetime, timezone

    from pe_core.slots import SlotTracker, drew
    start = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
    # eight one-cycle blips, each 30 s with a break between: 4 minutes in all, but never more than 30 s unbroken
    _, rec = _running((SlotTracker(), start), [True, False] * 8)
    assert rec["charging_min"] == 4.0 and rec["longest_min"] == 0.5 and not drew(rec)
    # a real charge: six minutes straight (after a blip)
    _, rec = _running((SlotTracker(), start), [True, False] + [True] * 12 + [False])
    assert rec["longest_min"] == 6.0 and rec["run_min"] == 0.0 and drew(rec)
    assert drew(rec, 6.0) and not drew(rec, 6.5)


def test_summary_counts_used_by_unbroken_minutes():
    from datetime import datetime, timedelta, timezone

    from pe_core.slots import SlotTracker
    now = datetime(2026, 10, 5, 8, 0, tzinfo=timezone.utc)
    t = SlotTracker()
    for i, (run, total) in enumerate([(0.5, 4.0), (3.7, 3.7), (0.0, 0.0)]):
        s = now - timedelta(hours=3 * (i + 1))
        t.slots[s.isoformat()] = {"start": s.isoformat(), "end": (s + timedelta(minutes=30)).isoformat(),
                                  "status": "done", "car_kwh": total * 0.12, "charging_min": total,
                                  "longest_min": run, "confirmed": False}
    s = t.summary(now)
    assert (s["used"], s["done_no_car"], s["min_charge_min"]) == (1, 2, 2.0)
    assert t.summary(now, min_charge_min=0.4)["used"] == 2
    assert [r["longest_min"] for r in s["recent"]] == [0.0, 3.7, 0.5]


def _confirmed(runs):
    from datetime import datetime, timedelta, timezone
    base = datetime(2026, 9, 20, 1, 0, tzinfo=timezone.utc)
    out = {}
    for i, run in enumerate(runs):
        s = base + timedelta(days=i)
        out[s.isoformat()] = {"start": s.isoformat(), "end": (s + timedelta(hours=1)).isoformat(), "status": "done",
                              "car_kwh": run * 0.12, "charging_min": run, "longest_min": run, "confirmed": True}
    return out


def test_learning_waits_for_evidence_then_moves_a_little_and_is_bounded():
    from pe_core.slots import LEARN_MIN_SLOTS, learn_min_charge
    assert learn_min_charge({}, 2.0) == (2.0, 0)
    few = _confirmed([30.0] * (LEARN_MIN_SLOTS - 1))
    assert learn_min_charge(few, 2.0) == (2.0, LEARN_MIN_SLOTS - 1)             # not enough yet
    # 10 long charges: aim 0.5 x 30 = 15 min, but only 10/30 of the way, and never past double the setting
    value, n = learn_min_charge(_confirmed([30.0] * 10), 2.0)
    assert n == 10 and value == 4.0
    # short real charges pull it down, but not below half the setting (1 minute at the least)
    value, _ = learn_min_charge(_confirmed([0.8] * 40), 2.0)
    assert value == 1.0
    assert learn_min_charge(_confirmed([0.8] * 40), 0.5)[0] == 1.0              # the floor holds for a small setting
    # a blip-only confirmed slot (half a minute) is not evidence of a real charge
    assert learn_min_charge(_confirmed([0.5] * 40), 2.0) == (2.0, 0)
    # unconfirmed or cancelled slots never count
    odd = _confirmed([30.0] * 10)
    for r in odd.values():
        r["confirmed"] = False
    assert learn_min_charge(odd, 2.0) == (2.0, 0)
