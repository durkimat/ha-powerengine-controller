"""The nightly job's bookkeeping (design 2.4) with the subprocess faked: which days, start, timeout kill, one at a time,
backfill order, a runner that fails or writes nothing."""
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

from pe_core.compare import results
from pe_core.compare.schedule import LIMIT, Scheduler

TODAY = date(2026, 10, 7)
T0 = datetime(2026, 10, 7, 3, 20, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw)


class FakeProc:
    """A runner that ends when told to; `writes` is what it leaves behind (the result of its day) when it ends."""
    made = []

    def __init__(self, cmd, **kw):
        self.cmd, self.kw, self.rc, self.killed, self.pid = cmd, kw, None, False, 4242
        self.on_end = None
        FakeProc.made.append(self)

    def poll(self):
        return self.rc

    def kill(self):
        self.killed = True
        self.rc = -9

    def wait(self, timeout=None):
        return self.rc

    def finish(self, rc=0):
        self.rc = rc
        if self.on_end:
            self.on_end()


def day_of(proc):
    return date.fromisoformat(proc.cmd[proc.cmd.index("--day") + 1])


def setup(tmp_path, snapshots=(), done=()):
    FakeProc.made = []
    save = tmp_path / "pe"
    costs = save / "costs"
    (costs / "snapshots").mkdir(parents=True)
    for d in snapshots:
        (costs / "snapshots" / f"{d.isoformat()}.json").write_text("{}")
    for d in done:
        results.write_result(str(costs), d, {"version": 1, "day": d.isoformat(), "status": "ok", "made_at": "x"})
    clock = Clock()
    sched = Scheduler(str(save), str(tmp_path / "app"), popen=FakeProc, now_fn=clock, python="py", nice=False)
    return sched, clock, str(costs)


def write_ok(costs, day, status="ok", reason="", at="2026-10-07T03:40:00+00:00"):
    results.write_result(costs, day, {"version": 1, "day": day.isoformat(), "status": status, "reason": reason,
                                      "took_s": 600, "made_at": at})


def days(n):
    return [TODAY - timedelta(days=k) for k in range(1, n + 1)]


def test_tonight_is_yesterday_then_the_newest_missing_day(tmp_path):
    d = days(7)
    sched, _, _ = setup(tmp_path, snapshots=[d[0], d[2], d[3], d[5]], done=[d[3]])
    assert sched.plan(TODAY) == [d[0], d[2]]                  # yesterday, then ONE more: the newest without a result
    sched2, _, _ = setup(tmp_path / "b", snapshots=[d[1]], done=[d[0]])
    assert sched2.plan(TODAY) == [d[1]]                       # yesterday done already
    sched3, _, _ = setup(tmp_path / "c", snapshots=[])
    assert sched3.plan(TODAY) == [d[0]]                       # yesterday is always tried: it writes why it can't


def test_only_the_last_week_is_filled(tmp_path):
    old = TODAY - timedelta(days=9)
    sched, _, _ = setup(tmp_path, snapshots=[old, TODAY - timedelta(days=7)])
    assert sched.missing(TODAY) == [TODAY - timedelta(days=7)]


def test_the_runner_is_started_as_a_subprocess_in_the_app_folder(tmp_path):
    sched, _, _ = setup(tmp_path)
    assert sched.nightly(TODAY) == [days(1)[0]]
    (proc,) = FakeProc.made
    assert proc.cmd[:3] == ["py", "-m", "pe_core.compare.run"] and "--save-dir" in proc.cmd
    assert proc.cmd[proc.cmd.index("--save-dir") + 1] == str(tmp_path / "pe")
    assert day_of(proc) == days(1)[0] and proc.kw["cwd"] == str(tmp_path / "app")
    assert sched.running and sched.day == days(1)[0]


def test_nice_is_used_where_there_is_one(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/nice" if name == "nice" else None)
    sched, _, _ = setup(tmp_path)
    sched.nice = True
    assert sched.command(TODAY)[:3] == ["/usr/bin/nice", "-n", "10"]
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert sched.command(TODAY)[0] == sys.executable or sched.command(TODAY)[0] == "py"


def test_one_at_a_time_and_the_next_starts_when_one_ends(tmp_path):
    d = days(3)
    sched, clock, costs = setup(tmp_path, snapshots=[d[1]])
    sched.nightly(TODAY)
    assert len(FakeProc.made) == 1 and sched.poll() == []        # still running: nothing ended, nothing new
    clock.advance(minutes=5)
    assert len(FakeProc.made) == 1
    again = sched.nightly(TODAY)                                  # a second trigger while busy queues nothing new
    assert again == [] and len(FakeProc.made) == 1
    FakeProc.made[0].on_end = lambda: write_ok(costs, d[0], at=clock().isoformat(timespec="seconds"))
    FakeProc.made[0].finish(0)
    ended = sched.poll()
    assert [e["day"] for e in ended] == [d[0].isoformat()] and ended[0]["status"] == "ok"
    assert len(FakeProc.made) == 2 and day_of(FakeProc.made[1]) == d[1]     # the backfill day follows, not alongside
    assert sched.running and sched.day == d[1]
    FakeProc.made[1].on_end = lambda: write_ok(costs, d[1], "no_snapshot", "no forecast record")
    FakeProc.made[1].finish(2)
    ended = sched.poll()
    assert ended[0]["status"] == "no_snapshot" and not sched.running and len(FakeProc.made) == 2


def test_a_run_over_90_minutes_is_killed_and_the_day_marked_failed(tmp_path):
    sched, clock, costs = setup(tmp_path)
    sched.nightly(TODAY)
    clock.advance(minutes=89)
    assert sched.poll() == [] and not FakeProc.made[0].killed
    clock.advance(minutes=2)
    ended = sched.poll()
    assert FakeProc.made[0].killed and ended[0]["status"] == "failed"
    assert "timed out after 90 minutes" in ended[0]["message"]
    r = results.read_result(costs, days(1)[0])
    assert r["status"] == "failed" and "timed out" in r["reason"] and r["took_s"] >= 90 * 60
    assert not sched.running and LIMIT == timedelta(minutes=90)


def test_a_runner_that_dies_without_a_result_is_a_failure_with_its_last_lines(tmp_path):
    sched, clock, costs = setup(tmp_path)
    sched.nightly(TODAY)
    log = os.path.join(costs, "compare", "run.log")
    with open(log, "w") as fh:
        fh.write("starting\nTraceback (most recent call last):\nValueError: boom\n")
    clock.advance(minutes=3)
    FakeProc.made[0].finish(1)
    (ended,) = sched.poll()
    assert ended["status"] == "failed" and "exit 1" in ended["message"] and "ValueError: boom" in ended["message"]
    assert results.read_result(costs, days(1)[0])["status"] == "failed"      # written, so it isn't tried again
    assert sched.nightly(TODAY) == []


def test_a_runner_that_reports_its_own_failure_keeps_its_file_and_adds_the_tail(tmp_path):
    sched, clock, costs = setup(tmp_path)
    sched.nightly(TODAY)
    with open(os.path.join(costs, "compare", "run.log"), "w") as fh:
        fh.write("Traceback\nKeyError: 'x'\n")
    write_ok(costs, days(1)[0], "failed", "replay failed: KeyError('x')")
    FakeProc.made[0].finish(1)
    (ended,) = sched.poll()
    assert ended["status"] == "failed" and "replay failed" in ended["message"] and "KeyError: 'x'" in ended["message"]


def test_exit_zero_with_no_file_is_a_failure(tmp_path):
    sched, _, _ = setup(tmp_path)
    sched.nightly(TODAY)
    FakeProc.made[0].finish(0)
    assert sched.poll()[0]["status"] == "failed"


def test_a_runner_that_cannot_start_is_a_failure_and_the_queue_moves_on(tmp_path):
    d = days(2)
    sched, _, costs = setup(tmp_path, snapshots=[d[1]])
    calls = []

    def popen(cmd, **kw):
        calls.append(cmd)
        if len(calls) == 1:
            raise OSError("no such file")
        return FakeProc(cmd, **kw)
    sched.popen = popen
    sched.nightly(TODAY)
    events = sched.poll()
    assert [e["status"] for e in events] == ["failed"] and "could not start" in events[0]["message"]
    assert results.read_result(costs, d[0])["status"] == "failed"
    assert sched.running and day_of(FakeProc.made[0]) == d[1]


def test_a_runner_left_by_an_earlier_app_start_blocks_a_second(tmp_path):
    sched, clock, costs = setup(tmp_path)
    os.makedirs(os.path.join(costs, "compare"))
    lock = os.path.join(costs, "compare", "running.json")
    with open(lock, "w") as fh:
        json.dump({"pid": os.getpid(), "day": "2026-10-06", "started": clock().isoformat()}, fh)   # alive: this test
    assert sched.busy()
    sched.nightly(TODAY)
    assert FakeProc.made == []
    clock.advance(minutes=95)                                                 # stale after the limit
    assert not sched.busy()
    sched.nightly(TODAY)
    assert len(FakeProc.made) == 1


def test_kill_stops_the_runner_when_the_app_stops(tmp_path):
    sched, _, costs = setup(tmp_path)
    sched.nightly(TODAY)
    sched.kill()
    assert FakeProc.made[0].killed and not sched.running
    assert not os.path.exists(os.path.join(costs, "compare", "running.json"))


def test_finished_days_are_never_started_again(tmp_path):
    d = days(2)
    sched, _, _ = setup(tmp_path, snapshots=[d[1]], done=[d[0], d[1]])
    assert sched.nightly(TODAY) == [] and FakeProc.made == []
