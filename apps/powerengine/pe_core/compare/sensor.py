"""`sensor.pe_cost_engines`: the last 7 days of the comparison for the Costs page (design 2.5), built from the result
files. Pure: the app reads the files and publishes what this returns. Attributes stay under 4 KB."""

from __future__ import annotations

from datetime import date, timedelta

from .results import tag_best

DAYS_SHOWN = 7
NOTE = ("Replays use your current settings and learned state, with the forecasts as they were. A rough guide: the "
        "simulated battery has no taper or BMS limits.")
WAITING = "waiting for tonight's run"
NO_SNAPSHOT = "no forecast record yet"


def _calib(result: dict) -> dict | None:
    c = result.get("calibration")
    if not isinstance(c, dict):
        return None
    return {"engine": c.get("engine"), "diff": c.get("diff"), "diff_pct": c.get("diff_pct")}


def day_row(day: date, result: dict | None, has_snapshot: bool) -> dict:
    """One day of the table. Savings are GBP against plain self-use (end level adjusted); positive is better."""
    row = {"date": day.isoformat(), "status": None, "reason": "", "in_control": None, "live": None, "v1": None,
           "v2": None, "bound": None, "best": None, "diff": None, "v1_flips": None, "v2_flips": None, "calib": None}
    if result is None:
        row["status"] = "pending"
        row["reason"] = WAITING if has_snapshot else NO_SNAPSHOT
        return row
    row["status"] = result.get("status") or "failed"
    row["reason"] = str(result.get("reason") or "")[:120]
    if row["status"] != "ok":
        return row
    v1, v2 = (result.get("v1") or {}), (result.get("v2") or {})
    row.update(in_control=result.get("in_control"), live=bool(result.get("live")), v1=v1.get("saving"),
               v2=v2.get("saving"), bound=(result.get("bound") or {}).get("saving"),
               v1_flips=v1.get("flips"), v2_flips=v2.get("flips"), calib=_calib(result))
    row["best"] = tag_best(row["v1"], row["v2"])
    if row["v1"] is not None and row["v2"] is not None:
        row["diff"] = round(row["v2"] - row["v1"], 3)
    return row


def totals(rows: list[dict]) -> dict:
    ok = [r for r in rows if r["status"] == "ok" and r["v1"] is not None and r["v2"] is not None]
    t = {"days": len(ok), "v1": round(sum(r["v1"] for r in ok), 3), "v2": round(sum(r["v2"] for r in ok), 3),
         "bound": round(sum(r["bound"] or 0.0 for r in ok), 3)}
    t["diff"] = round(t["v2"] - t["v1"], 3)
    return t


def calibration(rows: list[dict]) -> dict | None:
    """How far the engine that was in control replayed from its metered cost, over the days shown that have one."""
    cal = [r["calib"] for r in rows if r["status"] == "ok" and r["calib"] and r["calib"].get("diff") is not None]
    if not cal:
        return None
    engines = {c["engine"] for c in cal}
    pct = [abs(c["diff_pct"]) for c in cal if c.get("diff_pct") is not None]
    return {"engine": engines.pop() if len(engines) == 1 else "mixed", "days": len(cal),
            "mean_abs_diff": round(sum(abs(c["diff"]) for c in cal) / len(cal), 3),
            "mean_abs_pct": round(sum(pct) / len(pct), 1) if pct else None}


def last_run_of(results: dict) -> dict | None:
    """The most recently made result of those given, as the sensor's `last_run`."""
    done = [r for r in results.values() if r and r.get("made_at")]
    if not done:
        return None
    r = max(done, key=lambda x: x["made_at"])
    return {"at": r["made_at"], "day": r.get("day"), "status": r.get("status"), "took_s": r.get("took_s"),
            "message": str(r.get("reason") or "")[:160]}


def state_of(feature_on: bool, running: bool, last_run: dict | None, rows: list[dict]) -> str:
    if not feature_on:
        return "off"
    if running:
        return "running"
    if last_run and last_run.get("status") == "failed":
        return "error"
    return "ok" if any(r["status"] == "ok" for r in rows) else "waiting"


def build(today: date, results: dict[str, dict | None], snapshot_days: set[str], *, feature_on: bool = True,
          running: dict | None = None, next_run: str | None = None) -> tuple[str, dict]:
    """(state, attributes) of the sensor. `results` maps ISO dates to result files (None: no file) for the days that
    matter, `snapshot_days` holds the ISO dates that have a forecast record."""
    rows = []
    for n in range(1, DAYS_SHOWN + 1):
        d = today - timedelta(days=n)
        rows.append(day_row(d, results.get(d.isoformat()), d.isoformat() in snapshot_days))
    last = last_run_of(results)
    attrs = {"days": rows, "totals": totals(rows), "calib": calibration(rows), "last_run": last,
             "running": running, "next_run": next_run, "note": NOTE}
    return state_of(feature_on, bool(running), last, rows), attrs
