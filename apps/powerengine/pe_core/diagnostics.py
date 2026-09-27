"""Diagnostics export: one JSON bundle of what PowerEngine knows, for when there's no shell to pull files.

Asked for by the Health tab's diagnostics card (admin only, since HA only lets admins fire and subscribe to
events). The app answers with an event carrying the file-backed parts (config, write journal, plan, recent log);
the card adds live entity states and 24 h of history, then saves the lot as one file in the browser. A copy of the
app's part is also kept in /homeassistant/powerengine/diagnostics/ (the last few).
"""

from __future__ import annotations

import json
import os
from collections import deque
from datetime import datetime, timedelta

REQUEST_EVENT = "pe_diag_request"
BUNDLE_EVENT = "pe_diag_bundle"
LOG_LINES = 400
JOURNAL_HOURS = 48
KEEP_FILES = 5


class LogRing:
    """PowerEngine's own recent log lines, kept in memory for the export."""

    def __init__(self, size: int = LOG_LINES):
        self.lines: deque = deque(maxlen=size)

    def add(self, when: datetime, level: str, msg) -> None:
        self.lines.append({"t": when.isoformat(timespec="seconds"), "level": level or "INFO", "msg": str(msg)[:600]})


def recent_journal(entries: list[dict], now: datetime, hours: int = JOURNAL_HOURS) -> list[dict]:
    cutoff = (now - timedelta(hours=hours)).isoformat(timespec="seconds")
    return [e for e in entries if e.get("t", "") >= cutoff]


def safe(fn, *args, **kwargs):
    """A part of the bundle, or the error that stopped it (the export never fails as a whole)."""
    try:
        return fn(*args, **kwargs)
    except Exception as err:          # noqa: BLE001 - diagnostics must always produce something
        return {"error": repr(err)}


def save_copy(folder: str, stamp: str, bundle: dict) -> str:
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"diagnostics-{stamp}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(bundle, fh, default=str, separators=(",", ":"))
    old = sorted(f for f in os.listdir(folder) if f.startswith("diagnostics-") and f.endswith(".json"))
    for name in old[:-KEEP_FILES]:
        try:
            os.remove(os.path.join(folder, name))
        except OSError:
            pass
    return path
