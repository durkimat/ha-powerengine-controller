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
from .energy import HalfHour
from .ledger import Ledger
from .readings import Readings
from .tariff import cheap_tods, overnight_window, rates_at

KEEP_DAYS = 400
WINDOW_DAYS = 14
SHOW_DAYS = 14


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
            if name[:10] < cutoff and name.endswith(".json") and name[:4].isdigit():
                os.remove(os.path.join(self.folder, name))

    # --- recording ----------------------------------------------------------------------
    def learn_rates(self, r: Readings) -> None:
        for day, tods in cheap_tods(r.rates, self.tz).items():
            self.cheap_history[day] = sorted(tods)
        for day in sorted(self.cheap_history)[:-WINDOW_DAYS]:
            del self.cheap_history[day]

    def add(self, hh: HalfHour, r: Readings, *, capacity: float, eff: float, floor_soc: float, max_kw: float,
            includes_ev: bool, axle_value: float = 1.0) -> dict | None:
        """Value a completed half-hour and store it. Returns the stored record, or None if it can't be valued."""
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
        records = [x for x in records if x.get("start") != rec["start"]] + [rec]
        _write_json(self._day_path(day), records)
        v = rec["v"]
        if v.get("event") and v.get("event_kwh", 0) > 0.01:
            local = hh.start.astimezone(self.tz) if self.tz else hh.start
            self.last_event = {"type": v["event"], "date": local.date().isoformat(), "time": local.strftime("%H:%M"),
                               "kwh": round(v["event_kwh"], 2), "gross": round(v["event_gross"], 2),
                               "net": round(v["event_net"], 2)}
        self._save_state()
        return rec

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
