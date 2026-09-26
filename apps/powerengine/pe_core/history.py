"""The Plan history tab (#58): one past (or today's) day, a plan made that day, and what actually happened.

Choices come from two dashboard selects: the day ("Today", "Yesterday", "2 days ago" ... "30 days ago") and the
plan ("Start of day" = the first plan of that day, or the first plan made in a given hour). Plans are stored as
snapshots of their predictions for that day only (health.plan_snapshot); what happened comes from the half-hour
cost records.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

DAYS_BACK = 30
DAY_OPTIONS = ["Today", "Yesterday"] + [f"{n} days ago" for n in range(2, DAYS_BACK + 1)]
PLAN_OPTIONS = ["Start of day"] + [f"{h:02d}:00" for h in range(24)]
HALF = timedelta(minutes=30)


def chosen_day(option: str | None, today: date) -> date:
    if option == "Today":
        return today
    if option in (None, "", "Yesterday") or option not in DAY_OPTIONS:
        return today - timedelta(days=1)
    return today - timedelta(days=int(option.split()[0]))


def chosen_plan(option: str | None, start_of_day: dict | None,
                hourly: dict[str, dict]) -> tuple[str | None, dict | None]:
    """(label of the plan used, snapshot). An hour with no stored plan falls back to the latest earlier one."""
    if option in (None, "", "Start of day") or option not in PLAN_OPTIONS:
        if start_of_day:
            return "Start of day", start_of_day
        option = "23:00"
        if not hourly:
            return None, None
    earlier = sorted(h for h in hourly if h <= option)
    if earlier:
        return earlier[-1], hourly[earlier[-1]]
    if start_of_day:
        return "Start of day", start_of_day
    return None, None


def _r(v, n=2):
    return None if v is None else round(v, n)


def day_view(day: date, records: list[dict], snapshot: dict | None, plan_label: str | None, tz,
             now: datetime, available: list[str]) -> dict:
    start = datetime(day.year, day.month, day.day, tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=tz)
    recs = {r["start"]: r for r in records if r.get("start")}
    plan = {s["start"]: s for s in (snapshot or {}).get("slots", [])}
    keys = ("t", "x", "plan_soc", "actual_soc", "price_p", "plan_charge", "actual_charge", "plan_bat_export",
            "actual_bat_export", "plan_solar_export", "actual_solar_export", "plan_load", "actual_load",
            "plan_solar", "actual_solar")
    ser: dict[str, list] = {k: [] for k in keys}
    today = now.astimezone(tz).date()
    both = []
    t, stop = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    while t < stop:
        k = t.isoformat()                               # records and snapshots are keyed by UTC start
        rec, p = recs.get(k), plan.get(k)
        ser["t"].append(k)
        local = t.astimezone(tz)                        # the chart spans today: same time of day, today's date
        ser["x"].append(int(datetime.combine(today, local.time(), tzinfo=tz).timestamp() * 1000))
        ser["plan_soc"].append(p["soc"] if p else None)
        ser["actual_soc"].append(rec.get("soc_end") if rec else None)
        price = rec["import_rate"] * 100 if rec and rec.get("import_rate") is not None else (p or {}).get("price_p")
        ser["price_p"].append(_r(price))
        for key, pk, rk in (("charge", "charge_kwh", "g_b"), ("bat_export", "bat_export_kwh", "b_e"),
                            ("solar_export", "solar_export_kwh", "s_e"), ("load", "load_kwh", "house"),
                            ("solar", "solar_kwh", "solar")):
            ser[f"plan_{key}"].append(p.get(pk) if p else None)
            ser[f"actual_{key}"].append(_r(rec.get(rk)) if rec else None)
        if p and rec and (rec.get("seconds") or 0) >= 1200:
            both.append((p, rec))
        t += HALF

    def cost(rec):
        imp = (rec.get("grid_import") or 0) * (rec.get("import_rate") or 0)
        return imp - (rec.get("grid_export") or 0) * (rec.get("export_rate") or 0)

    summary = None
    if both:
        summary = {
            "half_hours": len(both),
            "plan_cost": round(sum(p.get("cost") or 0 for p, _ in both), 2),
            "actual_cost": round(sum(cost(r) for _, r in both), 2),
            "plan_load": round(sum(p.get("load_kwh") or 0 for p, _ in both), 1),
            "actual_load": round(sum(r.get("house") or 0 for _, r in both), 1),
            "plan_solar": round(sum(p.get("solar_kwh") or 0 for p, _ in both), 1),
            "actual_solar": round(sum(r.get("solar") or 0 for _, r in both), 1),
            "soc_gap": round(sum(abs((r.get("soc_end") or 0) - (p.get("soc") or 0)) for p, r in both) / len(both), 1),
        }
    day_cost = sum(cost(r) for r in records) if records else None
    return {
        "date": day.isoformat(), "label": start.strftime("%A %d %B %Y"),
        "plan": plan_label, "plan_made_at": (snapshot or {}).get("made_at"), "available_plans": available,
        "windows": (snapshot or {}).get("windows", []), "series": ser, "summary": summary,
        "day_import_export_cost": _r(day_cost), "recorded_half_hours": len(records),
    }
