"""How much of each day the cost records really measured, and what the missing time lacked.

A cost record's `seconds` counts only the time all three of grid, battery and house power were readable; the rest is
in `miss`, by why (`grid_power`, `battery_power`, `house_power` missing, `gap` when the app was not reading for over
five minutes, `late_start` when it began part-way through the half-hour). `summary` turns the last days' records into
the figures for the diagnostics file: coverage per day, the time lost by why, and the half-hours that lost most. Time
lost that the records do not explain (days recorded before `miss` existed) is `unexplained`.
"""

from __future__ import annotations

import json
import os
from datetime import date, timedelta

HALF_S = 1800.0
WORST = 6


def day_coverage(records: list[dict]) -> dict:
    lost: dict[str, float] = {}
    worst = []
    covered = 0.0
    for r in records:
        sec = float(r.get("seconds") or 0.0)
        covered += sec
        gap = max(0.0, HALF_S - sec)
        explained = 0.0
        for why, v in (r.get("miss") or {}).items():
            lost[why] = lost.get(why, 0.0) + float(v)
            explained += float(v)
        if gap - explained > 5:
            lost["unexplained"] = lost.get("unexplained", 0.0) + (gap - explained)
        if gap > 60:
            worst.append((round(gap), str(r.get("start", ""))[11:16], dict(r.get("miss") or {})))
    worst.sort(key=lambda x: (-x[0], x[1]))
    return {"records": len(records), "covered_pct": round(100 * covered / 86400, 1),
            "lost_min": {k: round(v / 60, 1) for k, v in sorted(lost.items(), key=lambda kv: -kv[1])},
            "worst": [{"lost_s": s, "at": t, "why": w} for s, t, w in worst[:WORST]]}


def summary(costs_dir: str, today: date, days: int = 14) -> dict:
    out = {}
    for n in range(days, 0, -1):
        day = (today - timedelta(days=n)).isoformat()
        try:
            with open(os.path.join(costs_dir, f"{day}.json"), encoding="utf-8") as fh:
                records = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(records, list):
            out[day] = day_coverage(records)
    return out
