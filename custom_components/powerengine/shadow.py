"""A shadow run: the engine as the integration will run it, beside the live PowerEngine, changing nothing.

Used to see, over a day or two, that the engine under this integration plans as the live one does, before anything is
switched over. A shadow engine:

  * works on its own copy of the live data folder (`seed`), so what it learns and records is its own;
  * is held to Passive by the engine itself (engine_main --shadow), and this integration also refuses every service call
    and event it asks for (and writes them down in `Ledger`, so what it would have said can be read), so it cannot
    reach the inverter, the notifications or the live card whatever it does;
  * publishes its entities under other ids (`sensor.pe_shadow_<key>` for the engine's `sensor.pe_<key>`), with other
    unique ids and on its own device, and reads its own entities from those, never the live ones.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
from collections import Counter, deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

SHADOW_PREFIX = "pe_shadow_"
# the engine's own entities: PowerEngine's catalogue groups (pe_core/entities.py GROUPS), not another integration's
# entities that happen to start with pe_ (someone else's pe_ entity)
_KEY = r"((?:cfg|ctl|state|plan|map|diag|cost|event|ui|v2)(?:_[a-z0-9_]+)?)"
OWN = re.compile(r"^(sensor|binary_sensor|switch)\.pe_" + _KEY + "$")
SHADOW = re.compile(r"^(sensor|binary_sensor|switch)\." + SHADOW_PREFIX + _KEY + "$")
SKIP = ("*.tgz", "config.yaml.bak*", "__pycache__", "diagnostics", "dashboard.yaml", "*.tmp", "shadow_ledger.jsonl")


def to_ha(engine_id: str) -> str:
    """The id Home Assistant has for what the engine calls `engine_id` (its own entities are the shadow's)."""
    match = OWN.match(engine_id)
    return f"{match.group(1)}.{SHADOW_PREFIX}{match.group(2)}" if match else engine_id


def from_ha(ha_id: str) -> str | None:
    """What the engine calls `ha_id`; None for the live engine's entities, which the shadow must not see."""
    match = SHADOW.match(ha_id)
    if match:
        return f"{match.group(1)}.pe_{match.group(2)}"
    return None if OWN.match(ha_id) else ha_id


class Ledger:
    """What the shadow engine asked for and was refused, in order, kept in the shadow folder and counted."""

    def __init__(self, path: Path | None = None, keep: int = 200, notify: Callable[[], None] | None = None) -> None:
        self.path = path
        self._notify = notify                          # called (from any thread) after each entry is added
        self.counts: Counter[str] = Counter()
        self.recent: deque[dict[str, Any]] = deque(maxlen=keep)
        self._lock = threading.Lock()

    def add(self, kind: str, name: str, data: Any) -> None:
        row = {"t": time.time(), "kind": kind, "name": name, "data": _short(data)}
        with self._lock:
            self.counts[f"{kind} {name}"] += 1
            self.recent.append(row)
            if self.path is not None:
                try:
                    with self.path.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps(row, default=str) + "\n")
                except OSError:
                    pass
        if self._notify is not None:
            self._notify()

    def summary(self) -> dict[str, Any]:
        with self._lock:
            return {"refused": sum(self.counts.values()), "by_kind": dict(self.counts.most_common(12))}


def _short(data: Any, limit: int = 400) -> Any:
    text = json.dumps(data, default=str)
    return data if len(text) <= limit else text[:limit] + "..."


def seed(live: Path, shadow: Path) -> int:
    """Give the shadow its own copy of the live data folder (once: not over a folder that has a config). The number of
    files copied; 0 if the shadow already has its config, or the live folder has none."""
    if (shadow / "config.yaml").exists() or not (live / "config.yaml").is_file():
        return 0
    shadow.mkdir(parents=True, exist_ok=True)
    count = 0
    for source in sorted(live.rglob("*")):
        relative = source.relative_to(live)
        if any(part in ("__pycache__", "diagnostics") for part in relative.parts) or any(
                relative.match(pattern) for pattern in SKIP):
            continue
        target = shadow / relative
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            count += 1
    return count


def clear(shadow: Path) -> None:
    """Remove the shadow's folder (to be seeded afresh)."""
    if shadow.exists():
        shutil.rmtree(shadow)
