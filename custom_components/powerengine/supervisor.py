"""Starts the engine as a process of Home Assistant's own Python, keeps it running, and restarts it when it should.

The engine (engine/engine_main.py) is a separate process so that an update replaces its files and only it restarts, not
Home Assistant. The supervisor:

  * starts it with the address of the integration's end of the connection and a secret (in its environment, never on the
    command line), and sends what it prints to Home Assistant's log;
  * restarts it when it stops by itself, after a wait that grows (5 s, 10 s, 30 s, 1 min, 5 min), and raises a Repair if
    it keeps stopping;
  * restarts it when the installed engine is not the one running (a HACS update replaced the files) or on request;
  * does not restart it when it stopped because its protocol is not the integration's (exit status 10): that needs
    Home Assistant restarted, and a Repair says so.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import sys
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .const import (
    EXIT_PROTOCOL,
    EXIT_SECRET,
    STATUS_BACKING_OFF,
    STATUS_FAILED,
    STATUS_NEEDS_RESTART,
    STATUS_RESTARTING,
    STATUS_RUNNING,
    STATUS_STARTING,
    STATUS_STOPPED,
)

_LOGGER = logging.getLogger(__name__)
_ENGINE_LOGGER = logging.getLogger(f"{__package__}.engine")

BACKOFF_S = (5, 10, 30, 60, 300)
STABLE_AFTER_S = 120.0            # an engine that has run this long starts the waits afresh
CRASHES_BEFORE_REPAIR = 4         # stops by itself within CRASH_WINDOW_S that raise a Repair
CRASH_WINDOW_S = 900.0
STOP_WAIT_S = 20.0                # how long the engine has to stop when asked before it is terminated
LOG_KEEP = 300                    # engine log lines kept for diagnostics
_LEVELS = {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "WARNING": logging.WARNING, "ERROR": logging.ERROR,
           "CRITICAL": logging.CRITICAL}
_VERSION = re.compile(r"""__version__\s*=\s*['"]([^'"]+)['"]""")


def installed_version(engine_dir: Path) -> str | None:
    """The engine's version as written in its files now (None if they can't be read)."""
    try:
        match = _VERSION.search((engine_dir / "pe_core" / "__init__.py").read_text(encoding="utf-8"))
    except OSError:
        return None
    return match.group(1) if match else None


class EngineSupervisor:
    def __init__(self, engine_dir: Path, settings_path: str, address: Callable[[], str], secret: str,
                 running_version: Callable[[], str | None], ask_stop: Callable[[], Any],
                 on_change: Callable[[], None] | None = None,
                 on_crashing: Callable[[bool], None] | None = None, python: str | None = None,
                 backoff: tuple[float, ...] = BACKOFF_S, script: str = "engine_main.py"):
        self.engine_dir, self.settings_path = engine_dir, settings_path
        self._address, self._secret, self._running_version = address, secret, running_version
        self._ask_stop = ask_stop                       # asks the connected engine to stop (it hands the inverter back)
        self._on_change = on_change or (lambda: None)
        self._on_crashing = on_crashing or (lambda crashing: None)
        self._python, self._backoff, self._script = python or sys.executable, backoff, script
        self.status = STATUS_STOPPED
        self.exit_code: int | None = None
        self.started_at: float | None = None
        self.restarts = 0
        self.log: deque[str] = deque(maxlen=LOG_KEEP)
        self._process: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task | None = None
        self._task: asyncio.Task | None = None
        self._restart_now = asyncio.Event()
        self._stopping = False
        self._connected = False
        self._installed: str | None = None                  # set by refresh_installed()
        self._stops: deque[float] = deque()
        self._crashing = False

    # --- what the integration reads
    @property
    def installed(self) -> str | None:
        """The installed engine's version as last read (`refresh_installed`: reading is a file read, which must not
        happen on Home Assistant's event loop, where this is asked for every time the status sensor is written)."""
        return self._installed

    def refresh_installed(self) -> str | None:
        """Read the installed version from the files now. Blocking: call it in an executor."""
        self._installed = installed_version(self.engine_dir)
        return self._installed

    @property
    def running(self) -> str | None:
        return self._running_version() if self.status == STATUS_RUNNING else None

    def _set(self, status: str) -> None:
        if status != self.status:
            self.status = status
            self._on_change()

    @property
    def started(self) -> bool:
        """Has the engine been started (it may since have stopped for good: see the status)?"""
        return self._task is not None

    def connected(self) -> None:
        """The engine has connected (the server's handshake): it is running."""
        self._connected = True
        if self._process is not None and self._process.returncode is None and self.status == STATUS_STARTING:
            self._set(STATUS_RUNNING)

    # --- start, stop, restart
    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopping = False
            self._task = asyncio.get_running_loop().create_task(self._run(), name="powerengine-supervisor")

    async def stop(self) -> None:
        self._stopping = True
        self._restart_now.set()
        await self._end_process()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._set(STATUS_STOPPED)

    async def restart(self) -> None:
        """Restart the engine now (the files were replaced, or the owner asked): ask it to stop; the loop starts the new
        one at once, without a wait. If it is not running (backing off, or never started) it starts now."""
        if self._task is None or self._task.done():
            self.start()
            return
        self._restart_now.set()
        if self._process is not None and self._process.returncode is None:
            self._set(STATUS_RESTARTING)
            await self._end_process()

    def update_needed(self) -> bool:
        """Is the engine running not the one installed? (A HACS update replaced the files.)"""
        installed, running = self.installed, self.running
        return bool(installed and running and installed != running)

    # --- the loop
    async def _run(self) -> None:
        waits = 0
        while not self._stopping:
            self._restart_now.clear()
            self._set(STATUS_STARTING)
            began = time.monotonic()
            try:
                await self._spawn()
            except OSError as err:
                _LOGGER.error("Could not start the PowerEngine engine: %s", err)
                code = -1
            else:
                self.started_at = time.time()
                self._connected = False
                code = await self._wait()
            self.exit_code = code
            self._process = None
            self._set(STATUS_STOPPED)
            if self._stopping:
                return
            if self._restart_now.is_set():                   # asked to restart: no wait
                self.restarts += 1
                self._set(STATUS_RESTARTING)
                continue
            if code == EXIT_PROTOCOL:
                _LOGGER.error("The engine speaks another protocol than this integration: restart Home Assistant to "
                              "finish updating PowerEngine")
                self._set(STATUS_NEEDS_RESTART)
                return
            if code == EXIT_SECRET:
                _LOGGER.error("The engine's secret was refused: this is a bug; restart Home Assistant")
                self._set(STATUS_FAILED)
                return
            if time.monotonic() - began >= STABLE_AFTER_S:
                waits = 0
            self._note_stop()
            wait = self._backoff[min(waits, len(self._backoff) - 1)]
            waits += 1
            _LOGGER.warning("The PowerEngine engine stopped (exit status %s); starting it again in %s s", code, wait)
            self._set(STATUS_BACKING_OFF)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._restart_now.wait(), wait)
            self.restarts += 1

    def _note_stop(self) -> None:
        now = time.monotonic()
        self._stops.append(now)
        while self._stops and now - self._stops[0] > CRASH_WINDOW_S:
            self._stops.popleft()
        crashing = len(self._stops) >= CRASHES_BEFORE_REPAIR
        if crashing != self._crashing:
            self._crashing = crashing
            self._on_crashing(crashing)

    async def _spawn(self) -> None:
        env = {**os.environ, "PE_SECRET": self._secret, "PYTHONUNBUFFERED": "1"}
        self._process = await asyncio.create_subprocess_exec(
            self._python, self._script, "--address", self._address(), "--settings", self.settings_path,
            cwd=str(self.engine_dir), env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        self._reader = asyncio.get_running_loop().create_task(self._forward(self._process.stdout))

    async def _wait(self) -> int:
        process = self._process
        code = await process.wait()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await asyncio.wait_for(self._reader, 5)
        return code

    async def _forward(self, stream: asyncio.StreamReader) -> None:
        """What the engine prints ("LEVEL message") goes to Home Assistant's log at that level."""
        while True:
            line = await stream.readline()
            if not line:
                return
            text = line.decode("utf-8", "replace").rstrip()
            if not text:
                continue
            self.log.append(text)
            level, _, message = text.partition(" ")
            if level in _LEVELS:
                _ENGINE_LOGGER.log(_LEVELS[level], "%s", message)
            else:
                _ENGINE_LOGGER.info("%s", text)

    async def _end_process(self) -> None:
        """Stop the engine: ask it, wait, then terminate, then kill."""
        process = self._process
        if process is None or process.returncode is not None:
            return
        try:
            asked = bool(self._ask_stop())
        except Exception as err:
            _LOGGER.debug("Could not ask the engine to stop: %s", err)
            asked = False
        if asked:                                         # connected: it hands the inverter back and exits
            try:
                await asyncio.wait_for(process.wait(), STOP_WAIT_S)
                return
            except TimeoutError:
                _LOGGER.warning("The PowerEngine engine did not stop when asked; terminating it")
        process.terminate()                               # (not connected yet, or not answering)
        try:
            await asyncio.wait_for(process.wait(), 10)
        except TimeoutError:
            process.kill()
            await process.wait()
