"""The timers a host without AppDaemon needs (pe_core/host_scheduler.py): AppDaemon's meanings for run_in, run_every,
run_daily and cancel_timer, and callbacks that never overlap."""

import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from pe_core.host_scheduler import Dispatcher, Scheduler

UTC = timezone.utc


class Clock:
    def __init__(self, at):
        self.at = at

    def __call__(self):
        return self.at

    def advance(self, **kw):
        self.at += timedelta(**kw)


def make(at=datetime(2026, 3, 1, 12, 0, tzinfo=UTC), tz="UTC"):
    clock = Clock(at)
    errors = []
    sched = Scheduler(Dispatcher(log=lambda m, **k: errors.append(m), inline=True), tz, now=clock, autostart=False)
    return sched, clock, errors


def test_run_in_calls_back_once_with_its_keywords():
    sched, clock, _ = make()
    seen = []
    handle = sched.run_in(lambda kw: seen.append(kw), 5, role="x", tries=2)
    assert sched.poll() == 0
    clock.advance(seconds=5)
    assert sched.poll() == 1 and seen == [{"role": "x", "tries": 2}]
    clock.advance(seconds=60)
    assert sched.poll() == 0 and not sched.timer_running(handle)


def test_cancel_timer_stops_a_timer_and_says_whether_it_was_there():
    sched, clock, _ = make()
    seen = []
    handle = sched.run_in(lambda kw: seen.append(1), 5)
    assert sched.cancel_timer(handle) is True and sched.cancel_timer(handle) is False
    clock.advance(seconds=10)
    sched.poll()
    assert seen == []


def test_run_every_now_plus_starts_after_that_many_seconds_then_repeats():
    sched, clock, _ = make()
    seen = []
    sched.run_every(lambda kw: seen.append(clock()), "now+30", 60)
    clock.advance(seconds=29)
    assert sched.poll() == 0
    clock.advance(seconds=1)
    assert sched.poll() == 1
    clock.advance(seconds=59)
    assert sched.poll() == 0
    clock.advance(seconds=1)
    assert sched.poll() == 1 and len(seen) == 2


def test_a_late_repeat_does_not_run_the_missed_ones_in_a_burst():
    sched, clock, _ = make()
    seen = []
    sched.run_every(lambda kw: seen.append(1), "now", 10)
    sched.poll()
    clock.advance(seconds=95)                       # asleep for nine intervals
    assert sched.poll() == 1
    assert sched.next_due() == datetime(2026, 3, 1, 12, 1, 40, tzinfo=UTC)      # back on its own beat


def test_run_every_with_a_start_in_the_past_waits_for_the_next_interval():
    sched, clock, _ = make()
    sched.run_every(lambda kw: None, datetime(2026, 3, 1, 11, 0, tzinfo=UTC), 600)
    assert sched.next_due() == datetime(2026, 3, 1, 12, 10, tzinfo=UTC)


def test_run_daily_runs_at_that_wall_clock_time_in_the_configured_zone():
    sched, clock, _ = make(datetime(2026, 7, 1, 12, 0, tzinfo=UTC), "Europe/London")     # 13:00 in London (BST)
    seen = []
    sched.run_daily(lambda kw: seen.append(clock()), "08:00:00")
    assert sched.next_due() == datetime(2026, 7, 2, 8, 0, tzinfo=ZoneInfo("Europe/London"))
    clock.at = datetime(2026, 7, 2, 7, 0, tzinfo=UTC)            # 08:00 London
    assert sched.poll() == 1
    assert sched.next_due() == datetime(2026, 7, 3, 8, 0, tzinfo=ZoneInfo("Europe/London"))


def test_run_daily_keeps_the_wall_clock_time_across_a_clock_change():
    sched, clock, _ = make(datetime(2026, 10, 24, 12, 0, tzinfo=UTC), "Europe/London")   # clocks go back on the 25th
    sched.run_daily(lambda kw: None, "00:10:00")
    assert sched.next_due().astimezone(ZoneInfo("Europe/London")).strftime("%d %H:%M") == "25 00:10"
    clock.at = datetime(2026, 10, 24, 23, 11, tzinfo=UTC)         # 00:11 BST on the 25th
    sched.poll()
    assert sched.next_due().astimezone(ZoneInfo("Europe/London")).strftime("%d %H:%M") == "26 00:10"


@pytest.mark.parametrize("start", ["08:00", "08:00:00", "8:00:00"])
def test_a_time_of_day_can_be_written_with_or_without_seconds(start):
    sched, _, _ = make()
    sched.run_daily(lambda kw: None, start)
    assert sched.next_due() == datetime(2026, 3, 2, 8, 0, tzinfo=UTC)


def test_a_start_that_is_not_a_time_is_refused():
    sched, _, _ = make()
    with pytest.raises(ValueError):
        sched.run_daily(lambda kw: None, "breakfast")
    with pytest.raises(ValueError):
        sched.run_every(lambda kw: None, "now", 0)


def test_a_callback_that_raises_is_logged_and_the_rest_still_run():
    sched, clock, errors = make()
    seen = []

    def bad(kw):
        raise RuntimeError("boom")
    sched.run_in(bad, 1)
    sched.run_in(lambda kw: seen.append(1), 1)
    clock.advance(seconds=1)
    sched.poll()
    assert seen == [1] and any("boom" in e for e in errors)


def test_timers_that_fall_due_together_run_in_the_order_they_were_set():
    sched, clock, _ = make()
    order = []
    for n in range(5):
        sched.run_in(lambda kw, n=n: order.append(n), 1)
    clock.advance(seconds=1)
    sched.poll()
    assert order == [0, 1, 2, 3, 4]


def test_the_timer_thread_runs_a_real_timer_and_callbacks_never_overlap():
    dispatcher = Dispatcher()
    dispatcher.start()
    sched = Scheduler(dispatcher)
    running, overlaps, done = [0], [0], threading.Event()

    def slow(kw):
        running[0] += 1
        overlaps[0] += running[0] > 1
        time.sleep(0.05)
        running[0] -= 1
        if kw["n"] == 2:
            done.set()
    for n in range(3):
        sched.run_in(slow, 0.01, n=n)
    assert done.wait(5)
    sched.stop()
    dispatcher.stop()
    assert overlaps[0] == 0


def test_stop_ends_the_timer_thread():
    dispatcher = Dispatcher()
    sched = Scheduler(dispatcher)
    sched.run_in(lambda kw: None, 3600)
    sched.stop()
    assert sched._thread is None
