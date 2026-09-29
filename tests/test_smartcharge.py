from datetime import datetime, timedelta, timezone

from pe_core.readings import Window
from pe_core.smartcharge import DAILY_CAP, SmartCharger, choose_time, worth_asking

OPTS = ["04:00", "04:30", "05:00", "05:30", "06:00", "06:30", "07:00", "07:30", "08:00", "08:30", "09:00", "09:30",
        "10:00", "10:30", "11:00"]
T0 = datetime(2026, 9, 26, 14, 0, tzinfo=timezone.utc)


def local(h, m=0):
    return datetime(2026, 9, 26, h, m)


def test_next_nearest_ready_by_time():
    assert choose_time(OPTS, "11:00", local(6, 0)) == "07:00"      # an hour ahead, next option
    assert choose_time(OPTS, "07:00", local(6, 0)) == "07:30"      # must differ from the current setting
    assert choose_time(OPTS, "11:00", local(16, 0)) == "04:00"     # afternoon: tomorrow's first
    assert choose_time(OPTS, "04:00", local(23, 0)) == "04:30"
    assert choose_time([], "04:00", local(9)) is None


def test_worth_asking_rules():
    now = T0
    soon = [Window(now + timedelta(hours=1), now + timedelta(hours=2), -3)]
    assert worth_asking("unplugged", [], now, False, 50, 100, False, 15, 7)[0] is False
    assert worth_asking("charging", [], now, False, 50, 100, False, 15, 7)[0] is False
    assert worth_asking("plugged_in", soon, now, False, 50, 100, False, 15, 7)[0] is False
    assert worth_asking("plugged_in", [], now, True, 50, 100, False, 15, 7)[0] is False     # already cheap
    assert worth_asking("plugged_in", [], now, False, 50, 100, False, 15, 7)[0] is True     # battery has room
    assert worth_asking("plugged_in", [], now, False, 100, 100, False, 15, 7)[0] is False   # full, no arbitrage
    ok, why = worth_asking("plugged_in", [], now, False, 100, 100, True, 15, 7)             # full, arbitrage on
    assert ok and "arbitrage" in why


def test_back_off_gap_and_daily_cap(tmp_path):
    sc = SmartCharger(str(tmp_path / "s.json"))
    yes = (True, "battery has room")
    t, cur, sent = T0, "11:00", []
    for _ in range(24 * 12):                                   # every 5 minutes for a day, never any slots
        a = sc.step(t, t.replace(tzinfo=None), yes, OPTS, cur, [], active=True)
        if a:
            sent.append(t)
            cur = a["to"]
        sc.resolve(t, [])
        t += timedelta(minutes=5)
    gaps = [(b - a).total_seconds() / 60 for a, b in zip(sent[:-1], sent[1:], strict=True)]
    assert len([x for x in sent if x.date() == T0.date()]) <= DAILY_CAP
    assert gaps[:5] == [30, 60, 120, 240, 240]                  # back-off grows, then stays at 4 hours


def test_success_resets_back_off_and_external_changes_are_scored():
    sc = SmartCharger()
    a = sc.step(T0, T0.replace(tzinfo=None), (True, "x"), OPTS, "11:00", [], active=True)
    assert a and a["result"] is None
    new = [Window(T0 + timedelta(hours=3), T0 + timedelta(hours=4), -3)]
    sc.resolve(T0 + timedelta(minutes=16), new)
    assert sc.attempts[-1]["result"] == "slots" and sc.attempts[-1]["gained"] == 2 and sc.backoff == 0
    sc.observe_external(T0 + timedelta(hours=2), "07:00", "10:00", new)
    sc.resolve(T0 + timedelta(hours=2, minutes=20), new)
    s = sc.summary(T0 + timedelta(hours=3))
    assert s["powerengine"] == {"tries": 1, "won": 1, "rate": 100} and s["other"]["tries"] == 1
    assert s["other"]["won"] == 0


def test_passive_records_would_requests_without_waiting_for_results():
    sc = SmartCharger()
    a = sc.step(T0, T0.replace(tzinfo=None), (True, "x"), OPTS, "11:00", [], active=False)
    assert a["by"] == "would" and a["result"].startswith("not sent")


def test_a_request_that_loses_planned_slots_counts_as_lost():
    sc = SmartCharger()
    before = [Window(T0 + timedelta(hours=4), T0 + timedelta(hours=6), -7)]           # 4 half-hours planned
    sc.step(T0, T0.replace(tzinfo=None), (True, "x"), OPTS, "11:00", before, active=True)
    after = [Window(T0 + timedelta(hours=5), T0 + timedelta(hours=5, minutes=30), -3)]  # EDF re-planned: 1 left
    sc.resolve(T0 + timedelta(minutes=16), after)
    a = sc.attempts[-1]
    assert (a["result"], a["gained"], a["lost"]) == ("lost slots", 0, 3) and sc.backoff == 1
    assert sc.summary(T0 + timedelta(hours=1))["powerengine"] == {"tries": 1, "won": 0, "rate": 0}


def test_no_request_until_settled():
    sc = SmartCharger()
    assert sc.step(T0, T0.replace(tzinfo=None), (True, "x"), OPTS, "11:00", [], active=True, settled=False) is None
    assert not sc.attempts


def test_half_hours():
    from pe_core.smartcharge import half_hours
    w = [Window(T0, T0 + timedelta(hours=1), -1)]
    assert len(half_hours(w)) == 2 and len(half_hours(w, T0 + timedelta(minutes=40))) == 1


# --- configurable rules (smart_* settings, slots_whole_house, smart_skip_full_car) ----------------

def test_worth_asking_lookahead_is_configurable():
    now = T0
    in_5h = [Window(now + timedelta(hours=5), now + timedelta(hours=6), -3)]
    assert worth_asking("plugged_in", in_5h, now, False, 50, 100, False, 15, 7)[0] is True       # default 3 h: far off
    assert worth_asking("plugged_in", in_5h, now, False, 50, 100, False, 15, 7,
                        lookahead=timedelta(hours=8))[0] is False                                # 8 h: due within it
    assert worth_asking("plugged_in", in_5h, now, False, 50, 100, False, 15, 7,
                        lookahead=timedelta(hours=1))[0] is True


def test_worth_asking_whole_house_off_never_asks():
    args = ("plugged_in", [], T0, False, 50, 100, False, 15, 7)
    assert worth_asking(*args)[0] is True
    ok, why = worth_asking(*args, whole_house=False)
    assert not ok and why == "smart slots don't cover the house"
    assert worth_asking("unplugged", [], T0, False, 50, 100, False, 15, 7, whole_house=False)[1] == "car not plugged in"
    assert worth_asking("charging", [], T0, False, 50, 100, False, 15, 7, whole_house=False)[1] == \
        "car already charging"                                           # the car checks come first


def test_worth_asking_skip_full_car():
    args = ("plugged_in", [], T0, False, 50, 100, False, 15, 7)
    assert worth_asking(*args, car_full=True)[0] is True                       # not skipping: asks anyway
    assert worth_asking(*args, skip_full=True)[0] is True                      # skipping, but the car isn't full
    ok, why = worth_asking(*args, car_full=True, skip_full=True)
    assert not ok and why == "the car looks full"


def run_day(tmp_path, cap, gap_min):
    sc = SmartCharger(str(tmp_path / "s.json"))
    t, cur, sent = T0, "11:00", []
    for _ in range(24 * 12):
        a = sc.step(t, t.replace(tzinfo=None), (True, "battery has room"), OPTS, cur, [], active=True, daily_cap=cap,
                    min_gap=timedelta(minutes=gap_min))
        if a:
            sent.append(t)
            cur = a["to"]
        sc.resolve(t, [])
        t += timedelta(minutes=5)
    return [x for x in sent if x.date() == T0.date()]


def test_daily_cap_of_4_and_10(tmp_path):
    # with the back-off (30, 60, 120, 240, 240 ...) from 14:00 only 4 fit before midnight; make room with a cap of 2
    assert len(run_day(tmp_path, 2, 20)) == 2
    (tmp_path / "s.json").unlink(missing_ok=True)
    assert len(run_day(tmp_path, 4, 10)) <= 4


def test_daily_cap_is_the_limit_when_gaps_are_short(tmp_path):
    def many(cap):
        sc = SmartCharger()
        n, t = 0, T0.replace(hour=0, minute=0)
        for _ in range(24 * 12):
            sc.backoff = 0                                       # (keep the back-off out of it: only the cap)
            sc.next_allowed = None
            if sc.step(t, t.replace(tzinfo=None), (True, "x"), OPTS, "11:00", [], active=True, daily_cap=cap,
                       min_gap=timedelta(minutes=10)):
                n += 1
                sc.attempts[-1]["result"] = "none"               # settled, so the next may follow
            t += timedelta(minutes=5)
        return n
    assert many(4) == 4 and many(10) == 10 and many(6) == 6


def test_min_gap_of_60_minutes(tmp_path):
    sc = SmartCharger()
    assert sc.step(T0, T0.replace(tzinfo=None), (True, "x"), OPTS, "11:00", [], active=True,
                   min_gap=timedelta(minutes=60))
    sc.attempts[-1]["result"] = "none"
    sc.next_allowed = None
    t = T0 + timedelta(minutes=45)
    assert sc.step(t, t.replace(tzinfo=None), (True, "x"), OPTS, "11:00", [], active=True) is not None   # 20 min: ok
    sc2 = SmartCharger()
    sc2.step(T0, T0.replace(tzinfo=None), (True, "x"), OPTS, "11:00", [], active=True, min_gap=timedelta(minutes=60))
    sc2.attempts[-1]["result"] = "none"
    sc2.next_allowed = None
    assert sc2.step(t, t.replace(tzinfo=None), (True, "x"), OPTS, "11:00", [], active=True,
                    min_gap=timedelta(minutes=60)) is None                                  # 45 < 60
    t = T0 + timedelta(minutes=61)
    assert sc2.step(t, t.replace(tzinfo=None), (True, "x"), OPTS, "11:00", [], active=True,
                    min_gap=timedelta(minutes=60)) is not None
