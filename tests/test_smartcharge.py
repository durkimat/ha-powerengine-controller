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
    assert sc.attempts[-1]["result"] == "slots" and sc.attempts[-1]["gained"] == 1 and sc.backoff == 0
    sc.observe_external(T0 + timedelta(hours=2), "07:00", "10:00", new)
    sc.resolve(T0 + timedelta(hours=2, minutes=20), new)
    s = sc.summary(T0 + timedelta(hours=3))
    assert s["powerengine"] == {"tries": 1, "won": 1, "rate": 100} and s["other"]["tries"] == 1
    assert s["other"]["won"] == 0


def test_passive_records_would_requests_without_waiting_for_results():
    sc = SmartCharger()
    a = sc.step(T0, T0.replace(tzinfo=None), (True, "x"), OPTS, "11:00", [], active=False)
    assert a["by"] == "would" and a["result"].startswith("not sent")
