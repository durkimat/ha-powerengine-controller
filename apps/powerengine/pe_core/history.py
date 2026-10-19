"""The Plan history tab (#58): one past, current or coming day, a plan made that day, and what actually happened.

Choices come from two dashboard selects: the day ("Today", "Yesterday", "2 days ago" ... "30 days ago") and the
plan ("As run" = the plan as it stood in each half-hour that ran, "Start of day" = the first plan of that day, or
the first plan made in a given hour). Plans are stored as
snapshots of their predictions for that day only (health.plan_snapshot); what happened comes from the half-hour
cost records.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from .planner import windows_within

HALF = timedelta(minutes=30)
HISTORY_WINDOWS_BUDGET = 9000          # bytes of windows; the series take about 5.4 KB of the 16 KB


def _imp(rec: dict):
    """Rates of a recorded half-hour; rebuilt ones keep them only in the valued rates ("v")."""
    v = rec.get("import_rate")
    return v if v is not None else (rec.get("v") or {}).get("act")


def _exp(rec: dict):
    v = rec.get("export_rate")
    return v if v is not None else (rec.get("v") or {}).get("exp")


PASSIVE_LABEL = "Start of day (engine v1 was passive)"


def chosen_plan(start_of_day: dict | None, ran: dict | None,
                in_control: str | None = None) -> tuple[str | None, dict | None]:
    """(label of the plan shown, snapshot): the plan that actually ran, half-hour by half-hour. A day with no as-run
    record (before it was kept) shows the plan made at the start of that day instead. On a day engine v2 was in
    control (`in_control` "v2") engine v1's plan did not run: its start-of-day plan is shown, labelled so."""
    if in_control == "v2":
        return (PASSIVE_LABEL, start_of_day) if start_of_day else (None, None)
    if ran and ran.get("slots"):
        return "As run", ran
    if start_of_day:
        return "Start of day", start_of_day
    return None, None


def _utc(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone(timezone.utc).isoformat()


def add_forecast(label: str | None, snapshot: dict | None, live: dict | None,
                 now: datetime) -> tuple[str | None, dict | None]:
    """Fill the half-hours still to come from the live plan (`health.plan_snapshot` of the day), keeping what was
    stored for those already run. A day with nothing stored (tomorrow) is the live plan alone, labelled "Forecast"."""
    if not live or not live.get("slots"):
        return label, snapshot
    slots = {_utc(s["start"]): s for s in (snapshot or {}).get("slots", [])}
    cut = now.astimezone(timezone.utc)
    for s in live["slots"]:
        k = _utc(s["start"])
        if k not in slots and datetime.fromisoformat(k) + HALF > cut:
            slots[k] = {**s, "start": k}
    windows = {(w.get("start"), w.get("action")): w for w in live.get("windows", [])}
    windows.update({(w.get("start"), w.get("action")): w for w in (snapshot or {}).get("windows", [])})
    return (label or "Forecast"), {
        "made_at": (snapshot or live).get("made_at"),
        "slots": [slots[k] for k in sorted(slots)],
        "windows": sorted(windows.values(), key=lambda w: w.get("start") or ""),
    }


FLOW_BUCKET_MIN = 5


def real_flows(readings, tz, bucket_min: int = FLOW_BUCKET_MIN) -> dict | None:
    """Grid charging and exports as they really happened, from readings taken every few seconds (a replay of HA's
    history), averaged into `bucket_min`-minute buckets. The half-hour records only say how much moved in each half
    hour; this says when.

    Per reading (grid_power + is importing, battery_power + is discharging): the grid energy going into the battery
    is the smaller of the import and the charge; export is split into the battery's discharge (up to what it
    delivers) and the rest, solar. Values are kWh per half-hour at that power (kW x 0.5), the chart's own scale.
    Result: {"bucket": minutes, "m": local minute of day each bucket starts, "charge", "bat_export",
    "solar_export"}, or None when there was nothing to read. Buckets with no readings, or with nothing moving, are
    left out."""
    span = bucket_min * 60
    sums: dict[int, list[float]] = {}
    for r in readings:
        if r.grid_power is None:
            continue
        g, b = r.grid_power / 1000, (r.battery_power or 0) / 1000
        out = max(-g, 0.0)
        bat = min(out, max(b, 0.0))
        row = sums.setdefault(int(r.now.timestamp()) // span, [0, 0.0, 0.0, 0.0])
        row[0] += 1
        row[1] += min(max(g, 0.0), max(-b, 0.0))
        row[2] += bat
        row[3] += out - bat
    if not sums:
        return None
    ks = sorted(sums)
    out = {"bucket": bucket_min, "m": [], "charge": [], "bat_export": [], "solar_export": []}
    for k in ks:
        n = sums[k][0]
        vals = [round(sums[k][i] / n * 0.5, 2) for i in (1, 2, 3)]
        if not any(vals):                               # quiet: left out (the chart draws the gap as zero), to keep
            continue                                    # the attribute small
        local = datetime.fromtimestamp(k * span, timezone.utc).astimezone(tz)
        out["m"].append(local.hour * 60 + local.minute)
        for key, v in zip(("charge", "bat_export", "solar_export"), vals, strict=True):
            out[key].append(v)
    return out


def _r(v, n=2):
    return None if v is None else round(v, n)


def as_run_windows(windows: list[dict], slots: list[dict], tz) -> list[dict]:
    """The windows of an "as run" day without the ones a replan replaced.

    The as-run record keeps every window the plan showed while its half-hours ran, so a day with many replans held
    overlapping copies (52 windows, 17 KB, over HA's 16 KB limit on 4 Oct 2026). Each half-hour belongs to the window
    of its action that started last (the plan as it stood then); a window keeps only the span it owns."""
    by_start = sorted(slots, key=lambda x: x["start"])
    owned: dict[int, list[str]] = {}
    for sl in by_start:
        best = None
        for i, w in enumerate(windows):
            if w.get("action") == sl["action"] and w["start"] <= sl["start"] < w["end"] \
                    and (best is None or w["start"] >= windows[best]["start"]):
                best = i
        if best is not None:
            owned.setdefault(best, []).append(sl["start"])
    out = []
    for i, w in enumerate(windows):
        starts = owned.get(i)
        if not starts:
            continue
        last = datetime.fromisoformat(starts[-1]) + HALF
        first = datetime.fromisoformat(starts[0])
        if first.isoformat() == w["start"] and last.isoformat() == w["end"]:
            out.append(w)
            continue
        out.append({**w, "start": first.isoformat(), "end": last.isoformat(),
                    "from": first.astimezone(tz).strftime("%H:%M"), "to": last.astimezone(tz).strftime("%H:%M")})
    merged: list[dict] = []
    for w in sorted(out, key=lambda w: w["start"]):
        m = merged[-1] if merged else None
        if m and m["end"] == w["start"] and m["action"] == w["action"] \
                and (m.get("reason") == w.get("reason") or w["action"] == "grid_charge"):
            m.update(end=w["end"], to=w["to"], soc_end=w.get("soc_end"))
        else:
            merged.append(dict(w))
    return merged


def day_view(day: date, records: list[dict], snapshot: dict | None, plan_label: str | None, tz,
             now: datetime) -> dict:
    start = datetime(day.year, day.month, day.day, tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=tz)
    recs = {r["start"]: r for r in records if r.get("start")}
    plan = {s["start"]: s for s in (snapshot or {}).get("slots", [])}
    keys = ("t", "x", "plan_soc", "actual_soc", "price_p", "plan_charge", "actual_charge", "plan_bat_export",
            "actual_bat_export", "plan_solar_export", "actual_solar_export", "plan_load", "actual_load",
            "plan_solar", "actual_solar")
    ser: dict[str, list] = {k: [] for k in keys}
    windows = (snapshot or {}).get("windows", [])
    if plan_label == "As run":
        windows = as_run_windows(windows, (snapshot or {}).get("slots", []), tz)
    windows, _ = windows_within(windows, HISTORY_WINDOWS_BUDGET)      # the sensor's attributes stay under 16 KB
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
        rate = _imp(rec) if rec else None
        price = rate * 100 if rate is not None else (p or {}).get("price_p")
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
        imp = (rec.get("grid_import") or 0) * (_imp(rec) or 0)
        return imp - (rec.get("grid_export") or 0) * (_exp(rec) or 0)

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
        "plan": plan_label, "plan_made_at": (snapshot or {}).get("made_at"),
        "windows": windows, "series": ser, "summary": summary,
        # the chart's x axis is always "today": where now falls on it (viewing today only); is the day yet to come
        "now_x": int(now.timestamp() * 1000) if day == today else None, "forecast": day > today,
        "day_import_export_cost": _r(day_cost), "recorded_half_hours": len(records),
    }
