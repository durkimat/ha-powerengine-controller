"""The nightly job's bookkeeping, apart from the app: which days to compare, starting the runner as a separate process,
watching it, killing it at the limit, one at a time (design 2.4).

The app calls `nightly()` at 03:20 and `poll()` every 5 minutes; this class does the rest. The subprocess starter is
injected, so tests fake it. Nothing here touches Home Assistant."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections import deque
from datetime import date, datetime, timedelta, timezone

from . import results
from .snapfeed import snapshot_path

LIMIT = timedelta(minutes=90)
LOOKBACK_DAYS = 7
EXTRA_PER_NIGHT = 1            # days filled besides yesterday, newest first (the oldest go last)
LOG_TAIL = 6                   # lines of the runner's output in the warning when a run fails
LOCK = "running.json"


def _pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError, TypeError):
        return False
    return True


class Scheduler:
    def __init__(self, save_dir: str, app_dir: str, *, popen=None, now_fn=None, python: str | None = None,
                 nice: bool = True):
        self.save_dir, self.app_dir = save_dir, app_dir
        self.costs = os.path.join(save_dir, "costs")
        self.popen = popen or (lambda *a, **k: subprocess.Popen(*a, **k))
        self.now = now_fn or (lambda: datetime.now(timezone.utc))
        self.python = python or sys.executable
        self.nice = nice
        self.queue: deque[date] = deque()
        self.events: list[dict] = []          # runs that ended (or never started) since the last poll()
        self.proc = None
        self.day: date | None = None
        self.started: datetime | None = None
        self._log = None

    # --- which days -----------------------------------------------------------------------------------------------
    def has_snapshot(self, day: date) -> bool:
        return os.path.exists(snapshot_path(self.costs, day))

    def missing(self, today: date) -> list[date]:
        """Days of the last week (not yesterday) that have a forecast record and no result, newest first."""
        days = [today - timedelta(days=n) for n in range(2, LOOKBACK_DAYS + 1)]
        return [d for d in days if self.has_snapshot(d) and not results.has_result(self.costs, d)]

    def plan(self, today: date) -> list[date]:
        """Tonight's days: yesterday (always, unless it has a result), then the newest missing one."""
        yesterday = today - timedelta(days=1)
        days = [] if results.has_result(self.costs, yesterday) else [yesterday]
        return days + self.missing(today)[:EXTRA_PER_NIGHT]

    # --- the job --------------------------------------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self.proc is not None

    def busy(self) -> bool:
        """A runner of ours is going, here or left from before an app restart."""
        if self.proc is not None:
            return True
        try:
            with open(os.path.join(results.compare_dir(self.costs), LOCK), encoding="utf-8") as fh:
                lock = json.load(fh)
            started = datetime.fromisoformat(lock["started"])
            return _pid_alive(lock["pid"]) and self.now() - started < LIMIT
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def nightly(self, today: date) -> list[date]:
        """Queue tonight's days and start the first if nothing is running. Returns the days queued."""
        days = [d for d in self.plan(today) if d not in self.queue and d != self.day]
        self.queue.extend(days)
        if not self.running:
            self._next()
        return days

    def command(self, day: date) -> list[str]:
        cmd = [self.python, "-m", "pe_core.compare.run", "--save-dir", self.save_dir, "--day", day.isoformat()]
        nice = shutil.which("nice") if self.nice else None
        return [nice, "-n", "10", *cmd] if nice else cmd

    def _next(self) -> None:
        while self.queue and not self.running:
            day = self.queue.popleft()
            if results.has_result(self.costs, day) or self.busy():
                continue
            self._start(day)

    def _start(self, day: date) -> None:
        folder = results.compare_dir(self.costs)
        os.makedirs(folder, exist_ok=True)
        try:
            self._log = open(os.path.join(folder, "run.log"), "w", encoding="utf-8")
            self.proc = self.popen(self.command(day), cwd=self.app_dir, stdout=self._log, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL)
        except Exception as err:
            self._close()
            self.events.append(self._finish(day, "failed", f"could not start the runner: {err!r}", self.now()))
            return None
        self.day, self.started = day, self.now()
        self._lock({"pid": getattr(self.proc, "pid", None), "day": day.isoformat(),
                    "started": self.started.isoformat()})
        return None

    def _lock(self, data):
        path = os.path.join(results.compare_dir(self.costs), LOCK)
        try:
            if data is None:
                os.remove(path)
            else:
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(data, fh)
        except OSError:
            pass

    def _close(self):
        if self._log is not None:
            try:
                self._log.close()
            except OSError:
                pass
            self._log = None

    def _tail(self) -> str:
        try:
            path = os.path.join(results.compare_dir(self.costs), "run.log")
            with open(path, encoding="utf-8", errors="replace") as fh:
                lines = [x.rstrip() for x in fh.read().splitlines() if x.strip()]
        except OSError:
            return ""
        return " | ".join(lines[-LOG_TAIL:])[-600:]

    def _finish(self, day: date, status: str, message: str, started: datetime) -> dict:
        """A run that did not produce a result file: write one saying why (so it isn't tried again)."""
        took = (self.now() - started).total_seconds()
        if not results.has_result(self.costs, day):
            results.write_result(self.costs, day, results.refused_result(day, "failed", message, took,
                                                                         self.now().isoformat(timespec="seconds")))
        return {"day": day.isoformat(), "status": "failed", "took_s": round(took), "message": message}

    def poll(self) -> list[dict]:
        """Check the runner. Returns what ended since the last call: {day, status, took_s, message}; a failure is for
        the app to log as a warning. Starts the next queued day when one ends."""
        if self.proc is not None:
            day, started = self.day, self.started
            rc = self.proc.poll()
            ended = None
            if rc is None:
                if self.now() - started > LIMIT:
                    try:
                        self.proc.kill()
                        self.proc.wait(timeout=10)
                    except Exception:
                        pass
                    ended = self._finish(day, "failed", f"timed out after {int(LIMIT.total_seconds() // 60)} minutes",
                                         started)
            else:
                res = results.read_result(self.costs, day)
                if res is not None:
                    message = str(res.get("reason") or "")
                    if res.get("status") == "failed":
                        message = f"{message} {self._tail()}".strip()[:300]
                    ended = {"day": day.isoformat(), "status": res.get("status"), "took_s": res.get("took_s"),
                             "message": message}
                else:
                    ended = self._finish(day, "failed", f"the runner stopped (exit {rc}): {self._tail()}"[:300],
                                         started)
            if ended is not None:
                self.proc, self.day, self.started = None, None, None
                self._close()
                self._lock(None)
                self.events.append(ended)
        if not self.running:
            self._next()
        out, self.events = self.events, []
        return out

    def kill(self):
        """Stop a runner of ours (the app is stopping)."""
        if self.proc is not None:
            try:
                self.proc.kill()
            except Exception:
                pass
            self.proc, self.day, self.started = None, None, None
            self._close()
            self._lock(None)
