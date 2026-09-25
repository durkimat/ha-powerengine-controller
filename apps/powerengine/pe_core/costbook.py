"""PowerEngine's cost book: valued half-hours on disk, the battery ledger, and the daily summaries.

Files live in <config dir>/powerengine/costs/:
    YYYY-MM-DD.json   that local day's half-hours (flows, rates and values)
    state.json        the ledger, the default-battery simulation and the overnight-window history
Everything can be recalculated from the day files if the method changes.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta

from .costs import METHOD_VERSION, SimDefault, day_summary, process, steps
from .energy import FLOW_VERSION, HalfHour
from .ledger import Ledger
from .readings import Readings
from .tariff import cheap_tods, overnight_window, rates_at, reclassify

KEEP_DAYS = 400
WINDOW_DAYS = 14
SHOW_DAYS = 14
MEASURE_DAYS = 30
MIN_MEASURE_DAYS = 14


def _write_json(path: str, data) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, separators=(",", ":"))
    os.replace(tmp, path)


def _read_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


class CostBook:
    def __init__(self, folder: str, tz=None):
        self.folder, self.tz = folder, tz
        os.makedirs(folder, exist_ok=True)
        st = _read_json(os.path.join(folder, "state.json"), {})
        self.ledger = Ledger(st.get("ledger"))
        self.sim = SimDefault(st.get("sim_kwh"))
        self.cheap_history: dict[str, list[int]] = st.get("cheap_tods", {})
        self.last_event: dict | None = st.get("last_event")
        self.flow_id: str = str(FLOW_VERSION)          # set by the app from the current input mapping
        # records valued by an older method are re-valued on start-up
        self.needs_revalue = bool(st) and st.get("method") != METHOD_VERSION

    # --- files ------------------------------------------------------------------------
    def _day_path(self, day: date | str) -> str:
        return os.path.join(self.folder, f"{day if isinstance(day, str) else day.isoformat()}.json")

    def _local_day(self, t: datetime) -> date:
        return (t.astimezone(self.tz) if self.tz else t).date()

    def day_records(self, day: date) -> list[dict]:
        return _read_json(self._day_path(day), [])

    def _save_state(self) -> None:
        _write_json(os.path.join(self.folder, "state.json"), {
            "method": METHOD_VERSION, "ledger": self.ledger.to_list(), "sim_kwh": self.sim.kwh,
            "cheap_tods": self.cheap_history, "last_event": self.last_event})

    def prune(self, today: date) -> None:
        cutoff = (today - timedelta(days=KEEP_DAYS)).isoformat()
        for name in os.listdir(self.folder):
            day = name[5:15] if name.startswith("plan-") else name[:10]
            if day < cutoff and name.endswith(".json") and day[:4].isdigit():
                os.remove(os.path.join(self.folder, name))

    # --- recording ----------------------------------------------------------------------
    def learn_rates(self, r: Readings) -> None:
        for day, tods in cheap_tods(r.rates, self.tz).items():
            self.cheap_history[day] = sorted(tods)
        for day in sorted(self.cheap_history)[:-WINDOW_DAYS]:
            del self.cheap_history[day]

    def add(self, hh: HalfHour, r: Readings, *, capacity: float, eff: float, floor_soc: float, max_kw: float,
            includes_ev: bool, axle_value: float = 1.0, keep_existing: bool = False) -> dict | None:
        """Value a completed half-hour and store it. Returns the stored record, or None if it can't be valued.

        keep_existing: don't replace a half-hour already fully recorded (a backfill never overwrites a full live
        record, but does fill in one cut short by a restart).
        """
        self.learn_rates(r)
        window = overnight_window(self.cheap_history)
        export = hh.export_rate if hh.export_rate is not None else (r.export_rate or 0.0)
        rt = rates_at(hh.start, r.rates, window, export, hh.import_rate, self.tz)
        if rt is None or hh.seconds < 60:
            return None
        rec = hh.as_dict()
        rec["v"] = process(rec, rt, self.ledger, self.sim, capacity=capacity, eff=eff, floor_soc=floor_soc,
                           max_kw=max_kw, includes_ev=includes_ev, axle_value=axle_value)
        day = self._local_day(hh.start)
        records = self.day_records(day)
        rec["fv"] = self.flow_id
        if keep_existing and any(x.get("start") == rec["start"] and x.get("fv") == self.flow_id
                                 and (x.get("seconds") or 0) >= 0.8 * 1800 for x in records):
            return None                     # a full live half-hour wins; a partial one (restart) is replaced
        rec["source"] = "history" if keep_existing else "live"
        records = sorted([x for x in records if x.get("start") != rec["start"]] + [rec], key=lambda x: x["start"])
        _write_json(self._day_path(day), records)
        self._note_event(rec)
        self._save_state()
        return rec

    def days_to_backfill(self, today: date, days: int) -> list[date]:
        """Recent days with less than 90% recorded (by the current flow method), oldest first; today is always
        included so half-hours before PowerEngine started (or during a restart) are filled in."""
        out = []
        for i in range(days, -1, -1):
            d = today - timedelta(days=i)
            recs = [x for x in self.day_records(d) if x.get("fv") == self.flow_id]
            if i == 0 or sum(x.get("seconds") or 0.0 for x in recs) < 0.9 * 86400:
                out.append(d)
        return out

    def revalue(self, *, capacity: float, eff: float, floor_soc: float, max_kw: float, includes_ev: bool,
                axle_value: float = 1.0) -> int:
        """Re-value every stored half-hour in time order with a fresh ledger and simulation.

        Needed after a backfill (older half-hours arrived after newer ones) or a change of method. Smart slots are
        re-decided with the current overnight window. Returns the number of half-hours valued.
        """
        window = overnight_window(self.cheap_history)
        self.ledger, self.sim, self.last_event = Ledger(), SimDefault(), None
        n = 0
        names = sorted(x for x in os.listdir(self.folder) if x[:4].isdigit() and x.endswith(".json"))
        for name in names:
            path = os.path.join(self.folder, name)
            records = sorted(_read_json(path, []), key=lambda x: x["start"])
            for rec in records:
                v = rec.get("v") or {}
                if "act" not in v:
                    continue
                start = datetime.fromisoformat(rec["start"])
                rt = reclassify(start, v, window, self.tz)
                rec["v"] = process(rec, rt, self.ledger, self.sim, capacity=capacity, eff=eff, floor_soc=floor_soc,
                                   max_kw=max_kw, includes_ev=includes_ev, axle_value=axle_value)
                self._note_event(rec)
                n += 1
            _write_json(path, records)
        self._save_state()
        return n

    def _note_event(self, rec: dict) -> None:
        v = rec["v"]
        if v.get("event") and v.get("event_kwh", 0) >= 0.1:           # ignore a flag with next to no energy
            start = datetime.fromisoformat(rec["start"])
            local = start.astimezone(self.tz) if self.tz else start
            self.last_event = {"type": v["event"], "date": local.date().isoformat(), "time": local.strftime("%H:%M"),
                               "kwh": round(v["event_kwh"], 2), "gross": round(v["event_gross"], 2),
                               "net": round(v["event_net"], 2)}

    # --- plan snapshots (for plan-vs-actual) ------------------------------------------------
    def save_plan_snapshot(self, day: date, snapshot: dict) -> None:
        _write_json(os.path.join(self.folder, f"plan-{day.isoformat()}.json"), snapshot)

    def plan_snapshot(self, day: date) -> dict | None:
        return _read_json(os.path.join(self.folder, f"plan-{day.isoformat()}.json"), None)

    def health(self, today: date, checks: dict | None, days: int = SHOW_DAYS) -> dict:
        """Findings (inputs + yesterday's data) and plan-vs-actual accuracy for recent days."""
        from .health import accuracy, data_findings, input_findings, overall
        yesterday = today - timedelta(days=1)
        findings = input_findings(checks or {})
        findings += data_findings([x for x in self.day_records(yesterday) if x.get("fv") == self.flow_id],
                                  yesterday.strftime("%a %d %b"))
        acc = []
        for i in range(days, 0, -1):
            d = today - timedelta(days=i)
            a = accuracy(self.day_records(d), self.plan_snapshot(d))
            if a:
                acc.append({"date": d.isoformat(), **a})
        return {"state": overall(findings), "findings": findings, "accuracy": acc}

    # --- measured losses ------------------------------------------------------------------
    def measure(self, today: date, capacity: float, days: int = MEASURE_DAYS) -> dict:
        """Battery round-trip efficiency and system losses from the recorded flows (complete days only).

        Battery: energy in (charging) and out (discharging) plus the change in stored energy over the window give
        the one-way efficiency e from  in*e - out/e = dE  (charge and discharge losses assumed equal); the round
        trip is e squared. Needs MIN_MEASURE_DAYS of data and a reasonable amount of cycling.
        System losses per day: everything supplied (solar, grid, battery) minus everything used (house, car,
        battery charging, export): inverter conversion, standby and heat.
        """
        b_in = b_out = 0.0
        soc_first = soc_last = None
        losses: list[tuple[str, float]] = []
        n_days = 0
        halves: list[dict] = []
        for i in range(days, 0, -1):
            d = today - timedelta(days=i)
            recs = sorted((x for x in self.day_records(d) if x.get("fv") == self.flow_id), key=lambda x: x["start"])
            if not recs or sum(x.get("seconds") or 0.0 for x in recs) < 0.9 * 86400:
                continue
            n_days += 1
            halves += recs
            day_in = sum(x.get("battery_in") or 0.0 for x in recs)
            day_out = sum(x.get("battery_out") or 0.0 for x in recs)
            b_in, b_out = b_in + day_in, b_out + day_out
            if soc_first is None:
                soc_first = next((x["soc_start"] for x in recs if x.get("soc_start") is not None), None)
            soc_last = next((x["soc_end"] for x in reversed(recs) if x.get("soc_end") is not None), soc_last)
            supplied = sum((x.get("solar") or 0.0) + (x.get("grid_import") or 0.0) for x in recs) + day_out
            used = sum((x.get("house") or 0.0) + (x.get("car") or 0.0) + (x.get("grid_export") or 0.0)
                       for x in recs) + day_in
            losses.append((d.isoformat(), round(supplied - used, 2)))
        out: dict = {"days": n_days, "battery_in": round(b_in, 1), "battery_out": round(b_out, 1),
                     "losses": losses, "measured": False, "rte": None, "efficiency": None}
        if losses:
            yesterday = (today - timedelta(days=1)).isoformat()
            out["losses_yesterday"] = losses[-1][1] if losses[-1][0] == yesterday else None
            out["losses_avg"] = round(sum(v for _, v in losses) / len(losses), 2)
        if n_days >= MIN_MEASURE_DAYS and b_in >= 20 and b_out >= 20 and soc_first is not None and soc_last is not None:
            d_e = (soc_last - soc_first) / 100 * capacity
            e = (d_e + (d_e * d_e + 4 * b_in * b_out) ** 0.5) / (2 * b_in)
            if 0.8 <= e <= 1.0:
                out.update(measured=True, efficiency=round(e, 4), rte=round(e * e, 4))
        out.update(battery_parameters(halves, out["efficiency"] or 0.95, capacity, n_days >= MIN_MEASURE_DAYS))
        return out

    # --- reporting ----------------------------------------------------------------------
    def summary(self, day: date, today: date) -> dict | None:
        records = self.day_records(day)
        if not records:
            return None
        complete = day < today
        standing = next((x.get("standing") for x in reversed(records) if x.get("standing") is not None), None)
        s = day_summary(records, standing_per_day=standing, complete=complete)
        s["date"] = day.isoformat()
        s["steps"] = [[name, round(val, 2)] for name, val in steps(s)]
        return s

    def recent(self, today: date, days: int = SHOW_DAYS) -> list[dict]:
        out = []
        for i in range(days, -1, -1):
            s = self.summary(today - timedelta(days=i), today)
            if s:
                out.append(s)
        return out

    def months(self, today: date, months: int = 12) -> list[dict]:
        """Everyday totals and event totals per calendar month (rolling 12 months)."""
        by: dict[str, dict] = {}
        y, m = today.year, today.month - (months - 1)
        while m <= 0:
            m, y = m + 12, y - 1
        d = date(y, m, 1)
        while d <= today:
            s = self.summary(d, today)
            if s:
                key = d.strftime("%Y-%m")
                mo = by.setdefault(key, {"month": key, "actual": 0.0, "s0": 0.0, "axle_net": 0.0,
                                         "free_power_net": 0.0, "event_metered": 0.0, "days": 0})
                mo["actual"] += s["actual"]
                mo["s0"] += s["s0"]
                mo["days"] += 1
                for kind, e in s["events"].items():
                    mo[f"{kind}_net"] = mo.get(f"{kind}_net", 0.0) + e["net"]
                    mo["event_metered"] += e["metered"]
            d += timedelta(days=1)
        return [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in m.items()} for m in by.values()]


def cost_entity_states(book: CostBook, today: date, months: list[dict] | None = None) -> dict:
    """key -> (state, attributes) for the cost_* and event_* entities."""
    days = book.recent(today)
    today_s = next((d for d in days if d["date"] == today.isoformat()), None)
    past = [d for d in days if d["complete"]]
    yesterday = past[-1] if past else None
    month_key = today.strftime("%Y-%m")
    this_month = next((m for m in months or [] if m["month"] == month_key), None)
    ev = book.last_event
    tm = this_month or {}
    month_events = round(tm.get("axle_net", 0.0) + tm.get("free_power_net", 0.0), 2)
    return {
        "cost_today": (today_s["actual"] if today_s else "unknown", today_s or {}),
        "cost_days": (yesterday["actual"] if yesterday else "unknown",
                      {"days": days, "method": METHOD_VERSION,
                       "ledger_kwh": round(book.ledger.kwh, 2), "ledger_value": round(book.ledger.value, 2)}),
        "event_last": (ev["net"] if ev else "unknown", ev or {}),
        "event_months": (month_events, {"months": months or []}),
    }


def battery_parameters(halves: list[dict], eff: float, configured_kwh: float, enough_days: bool) -> dict:
    """Usable capacity, highest charge/discharge rates seen and lowest state of charge, from recorded half-hours.

    Capacity: each half-hour's energy into storage (in x e - out / e) against its change in state of charge,
    fitted through zero (least squares). Only half-hours that moved the charge by 2% or more count, so the 1%
    steps of the SoC reading average out. Rates: the 98th percentile of half-hourly power, so a single spike
    doesn't count; they show what the battery has done, not necessarily its limit.
    """
    full = [h for h in halves if (h.get("seconds") or 0) >= 1500 and h.get("soc_start") is not None
            and h.get("soc_end") is not None]
    out: dict = {"capacity_kwh": None, "capacity_measured": False, "capacity_samples": 0,
                 "max_charge_kw": None, "max_discharge_kw": None, "min_soc": None}
    num = den = 0.0
    n = 0
    for h in full:
        ds = (h["soc_end"] - h["soc_start"]) / 100
        if abs(ds) < 0.02:
            continue
        de = (h.get("battery_in") or 0.0) * eff - (h.get("battery_out") or 0.0) / eff
        num, den, n = num + de * ds, den + ds * ds, n + 1
    out["capacity_samples"] = n
    if n and den:
        cap = num / den
        out["capacity_kwh"] = round(cap, 2)
        plausible = 0.7 * configured_kwh <= cap <= 1.3 * configured_kwh
        out["capacity_measured"] = bool(enough_days and n >= 100 and plausible)

    def p98(values: list[float]) -> float | None:
        v = sorted(x for x in values if x > 0.05)
        return round(v[min(len(v) - 1, int(0.98 * len(v)))], 2) if v else None
    out["max_charge_kw"] = p98([(h.get("battery_in") or 0.0) * 3600 / h["seconds"] for h in full])
    out["max_discharge_kw"] = p98([(h.get("battery_out") or 0.0) * 3600 / h["seconds"] for h in full])
    socs = [h["soc_end"] for h in full]
    out["min_soc"] = min(socs) if socs else None
    return out
