"""Result files of the nightly comparison: `<costs dir>/compare/YYYY-MM-DD.json` (design 2.5 in
docs/plans/engine-pages-and-comparison.md), written by the runner (or by the app for a run that never finished),
kept 30 days."""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone

VERSION = 1
KEEP_DAYS = 30
STATUSES = ("ok", "no_snapshot", "incomplete", "failed")
TIE_GBP = 0.01                          # within 1p: a tie


def compare_dir(costs_dir: str) -> str:
    return os.path.join(costs_dir, "compare")


def result_path(costs_dir: str, day) -> str:
    return os.path.join(compare_dir(costs_dir), f"{day if isinstance(day, str) else day.isoformat()}.json")


def write_result(costs_dir: str, day, result: dict) -> str:
    path = result_path(costs_dir, day)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(result, fh, separators=(",", ":"))
    os.replace(tmp, path)
    return path


def read_result(costs_dir: str, day) -> dict | None:
    try:
        with open(result_path(costs_dir, day), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("day") else None


def has_result(costs_dir: str, day) -> bool:
    return os.path.exists(result_path(costs_dir, day))


def prune(costs_dir: str, today: date) -> None:
    cutoff = (today - timedelta(days=KEEP_DAYS)).isoformat()
    folder = compare_dir(costs_dir)
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        if name[:4].isdigit() and name.endswith(".json") and name[:10] < cutoff:
            try:
                os.remove(os.path.join(folder, name))
            except OSError:
                pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def refused_result(day, status: str, reason: str, took_s: float = 0.0, made_at: str | None = None) -> dict:
    """A day that was not compared: why."""
    return {"version": VERSION, "day": day if isinstance(day, str) else day.isoformat(), "status": status,
            "reason": reason[:300], "took_s": round(took_s), "made_at": made_at or now_iso()}


def _r(x, places=3):
    return None if x is None else round(float(x), places)


def build_result(day_input, outcome: dict, took_s: float, made_at: str | None = None) -> dict:
    """The result file from a day's inputs and `replay.compare_day`'s outcome."""
    runs = outcome["runs"]

    def engine(name):
        run = runs[name]
        return {"cost": _r(run["cost_gbp"]), "end_soc": _r(run["end_soc"], 1), "saving": _r(run["adj_save_gbp"]),
                "flips": run.get("flip_flops"), "modes": run.get("mode_changes")}
    su, bound = runs["selfuse"], runs["bound"]
    out = {"version": VERSION, "day": day_input.day.isoformat(), "status": "ok", "reason": "", "took_s": round(took_s),
           "made_at": made_at or now_iso(), "in_control": day_input.in_control, "live": day_input.live,
           "selfuse": {"cost": _r(su["cost_gbp"]), "end_soc": _r(su["end_soc"], 1)},
           "v1": engine("v1"), "v2": engine("v2"),
           "bound": {"cost": _r(bound["cost_gbp"]), "saving": _r(bound["adj_save_gbp"])},
           "metered": {"cost": _r(day_input.metered_gbp)}, "calibration": None}
    if day_input.live and day_input.in_control in ("v1", "v2"):
        replay = out[day_input.in_control]["cost"]
        metered = out["metered"]["cost"]
        diff = replay - metered
        out["calibration"] = {"engine": day_input.in_control, "replay": replay, "metered": metered,
                              "diff": _r(diff), "diff_pct": _r(100 * diff / abs(metered), 1) if abs(metered) >= 0.05
                              else None}
    return out


def tag_best(v1: float | None, v2: float | None) -> str | None:
    """The better engine by saving: "v1", "v2" or "tie" (within 1p)."""
    if v1 is None or v2 is None:
        return None
    if abs(v2 - v1) < TIE_GBP:
        return "tie"
    return "v2" if v2 > v1 else "v1"
