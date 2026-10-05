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
LOG_RECENT, LOG_WARNINGS, LOG_WIDTH = 35, 20, 170     # published on sensor.pe_diag_log
JOURNAL_HOURS = 48
KEEP_FILES = 5


class LogRing:
    """PowerEngine's own recent log lines, kept in memory for the export."""

    def __init__(self, size: int = LOG_LINES):
        self.lines: deque = deque(maxlen=size)

    def add(self, when: datetime, level: str, msg) -> None:
        self.lines.append({"t": when.isoformat(timespec="seconds"), "level": level or "INFO", "msg": str(msg)[:600]})
        self.changed = True

    changed = False

    def load(self, path: str) -> None:
        """Lines saved before a restart come first (the file is kept beside the config)."""
        try:
            with open(path, encoding="utf-8") as fh:
                old = json.load(fh)
        except (OSError, ValueError):
            return
        keep = list(self.lines)
        self.lines.clear()
        for x in (old if isinstance(old, list) else [])[-self.lines.maxlen:]:
            if isinstance(x, dict) and "t" in x:
                self.lines.append(x)
        self.lines.extend(keep)

    def save(self, path: str) -> None:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(list(self.lines), fh, separators=(",", ":"))
        os.replace(tmp, path)
        self.changed = False

    def published(self, recent: int = LOG_RECENT, warnings: int = LOG_WARNINGS, width: int = LOG_WIDTH) -> dict:
        """For the Health tab's log card, kept well under HA's 16 KB attribute limit: the latest lines of any level
        and the latest warnings, newest first."""
        def short(x):
            return {"t": x["t"], "l": x.get("level", "INFO")[:1], "m": str(x.get("msg", ""))[:width]}
        lines = list(self.lines)
        warn = [x for x in lines if x.get("level") in ("WARNING", "ERROR")]
        return {"recent": [short(x) for x in reversed(lines[-recent:])],
                "warnings": [short(x) for x in reversed(warn[-warnings:])],
                "warning_count": len(warn)}


def recent_journal(entries: list[dict], now: datetime, hours: int = JOURNAL_HOURS) -> list[dict]:
    cutoff = (now - timedelta(hours=hours)).isoformat(timespec="seconds")
    return [e for e in entries if e.get("t", "") >= cutoff]


V2_JOURNAL_ROWS = 300


def engine_v2_section(engine, in_use: str, now: datetime, timeline: dict | None = None) -> dict:
    """The export's `engine_v2` part: which engine runs, the engine's health, the last 300 journal rows and the latest
    timeline (what was last published on sensor.pe_v2_timeline). `engine` is None when v2 never ran this start."""
    if engine is None:
        return {"in_use": in_use, "health": None, "journal": [], "timeline": timeline}
    return {"in_use": in_use, "health": engine.health(now), "journal": engine.journal()[-V2_JOURNAL_ROWS:],
            "timeline": timeline}


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


# Home Assistant's recorder skips state attributes over 16 KiB (logging a warning each time), so history of that
# sensor is lost; the live state is unaffected. Published attributes are measured so this shows up before HA's log.
ATTR_LIMIT = 16384
ATTR_WARN = 15000


def track_attr_size(sizes: dict, key: str, size: int) -> bool:
    """Record a published attribute payload's size; True the first time `key` goes past ATTR_WARN."""
    was = sizes.get(key, {"last": 0, "peak": 0})
    sizes[key] = {"last": size, "peak": max(size, was["peak"])}
    return size > ATTR_WARN and was["peak"] <= ATTR_WARN


def largest_attrs(sizes: dict, n: int = 8) -> list[dict]:
    rows = sorted(sizes.items(), key=lambda kv: -kv[1]["peak"])[:n]
    return [{"sensor": k, "last_bytes": v["last"], "peak_bytes": v["peak"], "limit_bytes": ATTR_LIMIT} for k, v in rows]
