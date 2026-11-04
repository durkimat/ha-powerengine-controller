"""One worker thread that does slow work in order, so the thread that calls the engine's callbacks doesn't wait for it.

AppDaemon runs every callback of the app on one thread. A remote-control command is three service calls, each of which
waits for the Modbus integration to talk to the inverter (2 to 4 seconds each on a slow day), so a command change held
that thread for up to 15 seconds. The worker takes the calls instead, one after another in the order they were handed
over, and hands the result back (`done`) for the caller to pass to whatever must run on the callback thread.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from typing import Any


class Worker:
    def __init__(self, log: Callable[..., Any] | None = None, name: str = "powerengine-writes"):
        self._log = log or (lambda *a, **k: None)
        self._name = name
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def submit(self, work: Callable[[], Any], done: Callable[[Any], Any] | None = None) -> None:
        """Run `work()` on the worker after everything handed over before it, then `done(its result)` on the worker."""
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, name=self._name, daemon=True)
                self._thread.start()
        self._queue.put((work, done))

    def idle(self, timeout: float = 10.0) -> bool:
        """Wait until everything handed over so far has been done (to stop in order, or not to overlap it)."""
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                return True
        finished = threading.Event()
        self._queue.put((finished.set, None))
        return finished.wait(timeout)

    def busy(self) -> bool:
        return not self._queue.empty()

    def _loop(self) -> None:
        while True:
            work, done = self._queue.get()
            try:
                result = work()
            except Exception as err:                    # the caller's work handles its own errors; this is the last net
                self._log(f"Background work failed: {err!r}", level="ERROR")
                result = None
            if done is not None:
                try:
                    done(result)
                except Exception as err:
                    self._log(f"Background work's follow-up failed: {err!r}", level="ERROR")
