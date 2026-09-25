"""Health: sanity checks on the recorded data, and how well the plan matched what happened.

Pure functions over the cost book's half-hour records, its measurements and the input checks, so they can be
tested offline. Each finding is {"level": "problem"|"warning", "title", "detail"}.
"""

from __future__ import annotations

from datetime import datetime

PROBLEM, WARNING = "problem", "warning"


def _sum(recs: list[dict], key: str) -> float:
    return sum(r.get(key) or 0.0 for r in recs)


def data_findings(recs: list[dict], day: str) -> list[dict]:
    """Checks on one complete day's half-hour records."""
    out: list[dict] = []
    if not recs:
        return [{"level": WARNING, "title": f"No data recorded for {day}",
                 "detail": "PowerEngine wasn't running and HA history couldn't fill the day."}]
    secs = _sum(recs, "seconds")
    if secs < 0.9 * 86400:
        out.append({"level": WARNING, "title": f"{day}: only {secs / 864:.0f}% of the day recorded",
                    "detail": "Restarts or missing sensor data leave gaps; that day's costs are partial."})
    b_in, b_out = _sum(recs, "battery_in"), _sum(recs, "battery_out")
    socs = [r.get("soc_end") for r in recs if r.get("soc_end") is not None]
    steps = zip(socs[:-1], socs[1:], strict=True)
    rose = len(socs) > 1 and max(socs) - min(socs) >= 10 and any(b > a + 3 for a, b in steps)
    if b_out > 2 and b_in < 0.05 and rose:
        out.append({"level": PROBLEM, "title": "Battery power never shows charging",
                    "detail": f"On {day} the battery charge rose but its power was never negative: the sensor is "
                              "probably unsigned. Map Battery charging power and Battery discharging power."})
    supplied = _sum(recs, "solar") + _sum(recs, "grid_import") + b_out
    used = _sum(recs, "house") + _sum(recs, "car") + _sum(recs, "grid_export") + b_in
    if supplied > 5:
        loss = supplied - used
        share = loss / supplied
        if share > 0.15:
            out.append({"level": WARNING, "title": f"{day}: {loss:.1f} kWh ({share:.0%}) unaccounted",
                        "detail": "More energy came in than went anywhere measured. Check the house load and grid "
                                  "sensors (and the solar plants) against the inverter's own daily counters."})
        elif share < -0.05:
            out.append({"level": WARNING, "title": f"{day}: {-loss:.1f} kWh more used than supplied",
                        "detail": "Destinations add up to more than the sources: a sensor may be double counting "
                                  "(e.g. the car included in house load but the setting says it isn't)."})
    corr = sum((r.get("v") or {}).get("correction_kwh", 0.0) for r in recs)
    if abs(corr) > 3:
        out.append({"level": WARNING, "title": f"{day}: battery ledger corrected by {corr:+.1f} kWh",
                    "detail": "The battery's state of charge moved differently from its measured power. Usually the "
                              "battery efficiency or capacity setting, or a slow battery-power sensor."})
    return out


def input_findings(checks: dict) -> list[dict]:
    """Inputs PowerEngine's own checks flag (from sensor.pe_map_config)."""
    out = []
    for key, c in sorted((checks or {}).items()):
        status = (c or {}).get("status")
        if status in (None, "ok", "unmapped"):
            continue
        level = PROBLEM if status in ("missing", "wrong_domain", "wrong_unit", "forbidden", "bad_static") else WARNING
        out.append({"level": level, "title": f"Input {key}: {status.replace('_', ' ')}",
                    "detail": c.get("message", "")})
    return out


def accuracy(recs: list[dict], snapshot: dict | None) -> dict | None:
    """How the plan made at the start of the day compared with what happened (per half-hour, that day)."""
    if not snapshot or not recs:
        return None
    pred = {s["start"]: s for s in snapshot.get("slots", [])}
    pairs = [(r, pred[r["start"]]) for r in recs if r.get("start") in pred and (r.get("seconds") or 0) >= 1200]
    if len(pairs) < 12:
        return None
    load_a = sum(r.get("house") or 0.0 for r, _ in pairs)
    load_p = sum(p.get("load_kwh") or 0.0 for _, p in pairs)
    solar_a = sum(r.get("solar") or 0.0 for r, _ in pairs)
    solar_p = sum(p.get("solar_kwh") or 0.0 for _, p in pairs)
    soc_err = [abs((r.get("soc_end") or 0) - (p.get("soc") or 0)) for r, p in pairs if r.get("soc_end") is not None]
    return {
        "half_hours": len(pairs), "made_at": snapshot.get("made_at"),
        "load_actual": round(load_a, 1), "load_forecast": round(load_p, 1),
        "load_mae": round(sum(abs((r.get("house") or 0) - (p.get("load_kwh") or 0)) for r, p in pairs) / len(pairs), 3),
        "solar_actual": round(solar_a, 1), "solar_forecast": round(solar_p, 1),
        "soc_mean_error": round(sum(soc_err) / len(soc_err), 1) if soc_err else None,
    }


def overall(findings: list[dict]) -> str:
    if any(f["level"] == PROBLEM for f in findings):
        return "problems"
    return "warnings" if findings else "ok"


def plan_snapshot(plan, day_start: datetime, day_end: datetime) -> dict:
    """The plan's predictions for one local day (half-hours from day_start to day_end)."""
    slots = []
    for ps in plan.slots:
        if day_start <= ps.slot.start < day_end:
            slots.append({"start": ps.slot.start.isoformat(), "soc": round(ps.soc_end, 1), "action": ps.action,
                          "load_kwh": round(ps.slot.load_kwh, 3), "solar_kwh": round(ps.slot.solar_kwh, 3)})
    return {"made_at": plan.made_at.isoformat(timespec="seconds"), "slots": slots}
