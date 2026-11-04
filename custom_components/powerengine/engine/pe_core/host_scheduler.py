"""Timers for a host that has no AppDaemon: `run_in`, `run_every`, `run_daily` and `cancel_timer`, with AppDaemon's
meanings, and the one thread the callbacks run on.

AppDaemon pins every callback of an app (timers, listeners) to a single thread, so they never overlap, and the engine is
written for that: a long job hands the thread back by scheduling its next slice with `run_in`. `Dispatcher` is that
thread; `Scheduler` decides when a callback is due and hands it over. A callback that raises is logged and the
scheduler carries on, as AppDaemon does.

What a start time can be (the forms the engine and AppDaemon's documentation use): "now", "now+30" (seconds from now),
"HH:MM:SS" or "HH:MM" (the next time the clock reads that, in the configured time zone), a `datetime` or a `time`. A
timer whose first run is already in the past starts at the next interval counted from it, as AppDaemon does.
"""

from __future__ import annotations

import heapq
import itertools
import queue
import re
import threading
from collections.abc import Callable
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo


class Dispatcher:
    """Runs callbacks one at a time, in the order they were submitted, on its own thread. `inline=True` runs each on
    the caller's thread instead (for tests that drive the clock themselves)."""

    def __init__(self, log: Callable[..., Any] | None = None, inline: bool = False):
        self._log = log or (lambda *a, **k: None)
        self._inline = inline
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self.running_on: int | None = None          # thread id of the callback in progress

    def start(self) -> None:
        if self._inline or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="powerengine-callbacks", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        thread, self._thread = self._thread, None
        if thread is not None:
            self._queue.put(None)
            thread.join(timeout)

    def submit(self, fn: Callable[[], Any], what: str = "callback") -> None:
        if self._inline:
            self._run(fn, what)
        else:
            self._queue.put((fn, what))

    def idle(self, timeout: float = 5.0) -> bool:
        """Wait until everything submitted so far has run (for tests and an orderly stop)."""
        if self._inline:
            return True
        done = threading.Event()
        self._queue.put((done.set, "idle"))
        return done.wait(timeout)

    def _loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            self._run(*item)

    def _run(self, fn: Callable[[], Any], what: str) -> None:
        self.running_on = threading.get_ident()
        try:
            fn()
        except Exception as err:                    # one bad callback must not stop the rest
            self._log(f"Callback {what} failed: {err!r}", level="ERROR")
        finally:
            self.running_on = None


_START_IN = re.compile(r"^now\s*\+\s*(\d+(?:\.\d+)?)$")
_CLOCK = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$")


class Timer:
    __slots__ = ("handle", "callback", "kwargs", "due", "every", "daily", "gone")

    def __init__(self, handle: str, callback: Callable, kwargs: dict, due: datetime, every: float | None,
                 daily: time | None):
        self.handle, self.callback, self.kwargs, self.due = handle, callback, kwargs, due
        self.every, self.daily = every, daily
        self.gone = False


class Scheduler:
    """`now` is a function returning an aware datetime (replaced in tests); `tz` the zone "HH:MM:SS" starts are read in.
    `autostart=False` leaves the clock to the caller: `poll()` runs whatever is due at `now()`."""

    def __init__(self, dispatcher: Dispatcher, tz: str | ZoneInfo = "UTC", now: Callable[[], datetime] | None = None,
                 autostart: bool = True, log: Callable[..., Any] | None = None):
        self.tz = ZoneInfo(tz) if isinstance(tz, str) else tz
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._dispatcher = dispatcher
        self._log = log or (lambda *a, **k: None)
        self._heap: list[tuple[datetime, int, Timer]] = []
        self._timers: dict[str, Timer] = {}
        self._seq = itertools.count(1)
        self._lock = threading.Condition()
        self._thread: threading.Thread | None = None
        self._stopped = False
        if autostart:
            self.start()

    # --- the calls the engine makes
    def run_in(self, callback: Callable, delay: float, **kwargs: Any) -> str:
        return self._add(callback, kwargs, self._now() + timedelta(seconds=float(delay)), None, None)

    def run_every(self, callback: Callable, start: Any, interval: float, **kwargs: Any) -> str:
        every = float(interval)
        if every <= 0:
            raise ValueError("run_every needs an interval above zero")
        first = self._first(start)
        now = self._now()
        if first < now:                             # already past: the next interval counted from it
            first += timedelta(seconds=every * (int((now - first).total_seconds() // every) + 1))
        return self._add(callback, kwargs, first, every, None)

    def run_daily(self, callback: Callable, start: Any = None, **kwargs: Any) -> str:
        at = self._time_of_day(start)
        return self._add(callback, kwargs, self._next_daily(at), None, at)

    def cancel_timer(self, handle: Any, **kwargs: Any) -> bool:
        with self._lock:
            timer = self._timers.pop(handle, None)
            if timer is None:
                return False
            timer.gone = True
            return True

    def timer_running(self, handle: Any) -> bool:
        """Is this timer still waiting to run? (A one-off that has run, or a cancelled one, is not.)"""
        with self._lock:
            return handle in self._timers

    # --- the clock
    def start(self) -> None:
        if self._thread is None:
            self._stopped = False
            self._thread = threading.Thread(target=self._loop, name="powerengine-timers", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            self._lock.notify_all()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(5)

    def poll(self) -> int:
        """Hand over everything due now; how many callbacks that was. (The timer thread calls this when one is due.)"""
        due = self._take_due()
        for timer, kwargs in due:
            self._dispatcher.submit(lambda t=timer, k=kwargs: t.callback(k), f"timer {_name(timer.callback)}")
        return len(due)

    def next_due(self) -> datetime | None:
        with self._lock:
            self._skip_gone()
            return self._heap[0][0] if self._heap else None

    def pending(self) -> int:
        with self._lock:
            return len(self._timers)

    def _loop(self) -> None:
        while True:
            with self._lock:
                if self._stopped:
                    return
                self._skip_gone()
                wait = 60.0
                if self._heap:
                    wait = max(0.0, min(wait, (self._heap[0][0] - self._now()).total_seconds()))
                if wait > 0:
                    self._lock.wait(wait)
                    if self._stopped:
                        return
            self.poll()

    # --- inside
    def _add(self, callback: Callable, kwargs: dict, due: datetime, every: float | None, daily: time | None) -> str:
        handle = f"timer-{next(self._seq)}"
        timer = Timer(handle, callback, dict(kwargs), due, every, daily)
        with self._lock:
            self._timers[handle] = timer
            heapq.heappush(self._heap, (due, next(self._seq), timer))
            self._lock.notify_all()
        return handle

    def _skip_gone(self) -> None:
        while self._heap and self._heap[0][2].gone:
            heapq.heappop(self._heap)

    def _take_due(self) -> list[tuple[Timer, dict]]:
        now = self._now()
        out: list[tuple[Timer, dict]] = []
        with self._lock:
            while True:
                self._skip_gone()
                if not self._heap or self._heap[0][0] > now:
                    break
                _, _, timer = heapq.heappop(self._heap)
                out.append((timer, dict(timer.kwargs)))
                if timer.every is not None:         # the next one counted from when this was due, not from now
                    due = timer.due + timedelta(seconds=timer.every)
                    if due <= now:
                        due += timedelta(seconds=timer.every * (int((now - due).total_seconds() // timer.every) + 1))
                    timer.due = due
                    heapq.heappush(self._heap, (due, next(self._seq), timer))
                elif timer.daily is not None:
                    timer.due = self._next_daily(timer.daily, after=now)
                    heapq.heappush(self._heap, (timer.due, next(self._seq), timer))
                else:
                    self._timers.pop(timer.handle, None)
        return out

    def _first(self, start: Any) -> datetime:
        now = self._now()
        if start is None:
            return now
        if isinstance(start, datetime):
            return start if start.tzinfo else start.replace(tzinfo=self.tz)
        if isinstance(start, time):
            return self._next_daily(start)
        text = str(start).strip().lower()
        if text == "now":
            return now
        m = _START_IN.match(text)
        if m:
            return now + timedelta(seconds=float(m.group(1)))
        return self._next_daily(self._time_of_day(text))

    @staticmethod
    def _time_of_day(start: Any) -> time:
        if isinstance(start, time):
            return start
        if isinstance(start, datetime):
            return start.time()
        m = _CLOCK.match(str(start or "").strip())
        if not m:
            raise ValueError(f"not a time of day: {start!r} (use HH:MM:SS)")
        return time(int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))

    def _next_daily(self, at: time, after: datetime | None = None) -> datetime:
        """The next time the wall clock reads `at`, after `after` (default now)."""
        after = after or self._now()
        day: date = after.astimezone(self.tz).date()
        for _ in range(3):                          # today, tomorrow (and a day more across a clock change)
            candidate = datetime.combine(day, at, tzinfo=self.tz)
            if candidate.astimezone(timezone.utc) > after.astimezone(timezone.utc):
                return candidate
            day += timedelta(days=1)
        return candidate


def _name(callback: Callable) -> str:
    return getattr(callback, "__qualname__", None) or getattr(callback, "__name__", None) or repr(callback)

