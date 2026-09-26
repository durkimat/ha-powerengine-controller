"""The Simulator (#54, phase 1: tariffs): what your recorded days would have cost on other tariffs.

Each scenario replays the recorded half-hours (house, car, solar) day by day with the optimiser choosing the
battery's actions, carrying the charge from one day to the next. Only prices change between scenarios.

- "current": the prices you actually paid (smart slots included), the car at its recorded times. The fair
  baseline: same optimiser, your tariff.
- every other scenario: that tariff's published rates for each date (a fixed or time-of-use tariff launched
  later is applied by time of day, marked estimated), with the car's daily energy moved into the day's cheapest
  half-hours (as a smart or scheduled charger would).
Results are cached per scenario and day; a scenario is recomputed only when the battery settings change.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import date, datetime, timedelta

from .forecast import Slot
from .optimiser import optimise
from .planner import Params, PlanSlot, step

REGION = "A"                      # Eastern England (from the MPAN); a config setting later
MIN_DAY_SECONDS = 0.9 * 86400
NOTIFY_MIN_GBP_MONTH = 5.0        # a scenario is worth a look if it saves at least this a month...
NOTIFY_MIN_SHARE = 0.05           # ...and at least this share of the baseline
MIN_DAYS_TO_COMPARE = 14
MONTH_DAYS = 30.44

NOTES = (("INTELLI", "needs a compatible car or charger"), ("IOG", "needs a compatible car or charger"),
         ("COSY", "for homes with a heat pump"), ("HEAT_PUMP", "for homes with a heat pump"),
         ("FLUX", "needs solar and a battery; import and export with Octopus"),
         ("PODPOINT", "needs a Pod Point charger"), ("EXCLUSIVE", "may be for existing customers only"),
         ("AGILE", "prices change every half-hour"), ("DYNAMIC", "prices change every half-hour"),
         ("TRACKER", "price follows the wholesale market daily"))


def notes(code: str) -> str:
    seen, out = set(), []
    for word, text in NOTES:
        if word in code.upper() and text not in seen:
            seen.add(text)
            out.append(text)
    return "; ".join(out)


# --- scenarios ---------------------------------------------------------------------------

def scenarios(catalogue: list[dict]) -> list[dict]:
    """Import/export pairings from the fetched products (each product has 'tariff' filled in, or is skipped)."""
    usable = [p for p in catalogue if p.get("tariff")]
    exports = [p for p in usable if p["direction"] == "EXPORT"]
    out = [{"id": "current", "name": "Your tariff (prices you paid)", "import": None, "export": None,
            "notes": "the baseline: same optimiser, the car at its recorded times"}]

    def export_for(imp: dict) -> list[dict]:
        code = imp["code"]
        if imp["supplier"] == "edf":
            return [None]                                            # EDF: keep your current export rate
        if "FLUX" in code:
            return [e for e in exports if e["code"].startswith("FLUX-EXPORT")][:1] or [None]
        same = [e for e in exports if e.get("brand") == imp.get("brand")]
        outgoing = [e for e in same if e["code"].startswith("OUTGOING-VAR")]
        seg = [e for e in same if "SEG" in e["code"] and "-EO-" not in e["code"]]
        picks = outgoing[:1] or seg[:1]
        if code.startswith("AGILE"):
            picks += [e for e in same if e["code"].startswith("AGILE-OUTGOING")][:1]
        return picks or [None]

    for imp in usable:
        if imp["direction"] != "IMPORT":
            continue
        for exp in export_for(imp):
            sid = f"{imp['supplier']}:{imp['code']}" + (f"+{exp['code']}" if exp else "")
            name = imp["name"] + (f" + {exp['name']}" if exp else " (your export rate)")
            out.append({"id": sid, "name": name, "import": _ref(imp), "export": _ref(exp) if exp else None,
                        "notes": notes(imp["code"] + " " + (exp["code"] if exp else ""))})
    return out


def _ref(p: dict) -> dict:
    return {"supplier": p["supplier"], "product": p["code"], "tariff": p["tariff"], "name": p["name"]}


def rate_key(ref: dict, kind: str) -> str:
    return f"{ref['supplier']}__{ref['tariff']}__{kind}"


# --- recorded days ------------------------------------------------------------------------

def complete(records: list[dict]) -> bool:
    return len(records) >= 46 and sum(r.get("seconds") or 0 for r in records) >= MIN_DAY_SECONDS


def actual_cost(records: list[dict]) -> dict:
    imp = sum((r.get("grid_import") or 0) * (r.get("import_rate") or 0) for r in records)
    exp = sum((r.get("grid_export") or 0) * (r.get("export_rate") or 0) for r in records)
    standing = next((r.get("standing") for r in records if r.get("standing") is not None), 0.0) or 0.0
    return {"cost": round(imp - exp + standing, 4), "import_cost": round(imp, 4), "export_income": round(exp, 4),
            "standing": round(standing, 4),
            "import_kwh": round(sum(r.get("grid_import") or 0 for r in records), 3),
            "export_kwh": round(sum(r.get("grid_export") or 0 for r in records), 3)}


def _shift_car(prices: list[float], car: list[float], max_kwh: float) -> list[float]:
    """The day's car energy moved into its cheapest half-hours (earliest first on ties), up to the charger rate."""
    total = sum(car)
    out = [0.0] * len(car)
    for i in sorted(range(len(prices)), key=lambda k: (prices[k], k)):
        if total <= 1e-6:
            break
        take = min(max_kwh, total)
        out[i] = take
        total -= take
    return out


def price_fn(scn: dict, tables: dict, tz):
    """(start, record) -> (import GBP/kWh, export GBP/kWh, estimated) for a scenario. `tables` maps rate_key ->
    kraken.RateTable. Dates before a tariff's first published rate use its time-of-day pattern (estimated)."""
    imp, exp = scn.get("import"), scn.get("export")
    if imp is None:
        return lambda t, r: (r.get("import_rate"), r.get("export_rate"), False)
    it = tables.get(rate_key(imp, "standard-unit-rates"))
    et = tables.get(rate_key(exp, "standard-unit-rates")) if exp else None

    def look(table, t):
        v = table.at(t) if table else None
        if v is not None:
            return v, False
        v = table.pattern_at(t, tz) if table else None
        return v, v is not None

    def f(t, r):
        i, est = look(it, t)
        if exp is None:
            e, est_e = r.get("export_rate"), False
        else:
            e, est_e = look(et, t)
        return i, e, est or est_e
    return f


def standing_for(scn: dict, tables: dict, day_start: datetime, records: list[dict], catalogue_p: float | None) -> float:
    """GBP/day."""
    imp = scn.get("import")
    if imp is None:
        return next((r.get("standing") for r in records if r.get("standing") is not None), 0.0) or 0.0
    t = tables.get(rate_key(imp, "standing-charges"))
    v = t.at(day_start) if t else None
    if v is None and t is not None and t.w:
        v = t.w[0][2]                              # before its first published charge: the earliest one
    if v is None and catalogue_p is not None:
        v = catalogue_p / 100
    return v or 0.0


def day_slots(records: list[dict], price_fn, shift_car: bool, charger_kw: float) -> tuple[list[Slot], bool]:
    """Slots for one recorded day at a scenario's prices. price_fn(start, record) -> (import, export, estimated)."""
    rows = sorted(records, key=lambda r: r["start"])
    starts = [datetime.fromisoformat(r["start"]) for r in rows]
    priced = [price_fn(t, r) for t, r in zip(starts, rows, strict=True)]
    estimated = any(p[2] for p in priced)
    car = [r.get("car") or 0.0 for r in rows]
    if shift_car:
        car = _shift_car([p[0] if p[0] is not None else 9.99 for p in priced], car, charger_kw * 0.5)
    slots = []
    for t, r, (imp, exp, _), c in zip(starts, rows, priced, car, strict=True):
        slots.append(Slot(start=t, price=imp, export=exp, solar_kwh=r.get("solar") or 0.0,
                          load_kwh=(r.get("house") or 0.0) + c, car_kw=c / 0.5))
    return slots, estimated


def sim_params(p: Params) -> Params:
    """The optimiser's freedom in the Simulator: any battery action; selling from the battery only if the
    Arbitrage feature is on (as in real control)."""
    return replace(p, hold_for_car=False, axle_enabled=False, free_enabled=False)


def run_day(slots: list[Slot], lookahead: list[Slot], soc: float, p: Params, standing: float) -> dict:
    """Best achievable cost for one day (next day as look-ahead), and the charge carried into the next."""
    n = len(slots)
    opt = optimise(slots + lookahead, soc, p, wear=p.wear_p / 100) or {"actions": []}
    lvl, imp_c, exp_i, imp_k, exp_k = soc, 0.0, 0.0, 0.0, 0.0
    for s, a in zip(slots, opt["actions"][:n], strict=True):
        ps = PlanSlot(s, a, "", target_soc=100.0)
        lvl = step(ps, lvl, p)
        imp_k += ps.grid_import
        exp_k += ps.grid_export
        imp_c += ps.grid_import * (s.price or 0.0)
        exp_i += ps.grid_export * (s.export or 0.0)
    return {"cost": round(imp_c - exp_i + standing, 4), "import_cost": round(imp_c, 4),
            "export_income": round(exp_i, 4), "standing": round(standing, 4), "import_kwh": round(imp_k, 3),
            "export_kwh": round(exp_k, 3), "end_soc": round(lvl, 2)}


def signature(p: Params) -> str:
    keys = ("capacity_kwh", "max_charge_kw", "max_discharge_kw", "efficiency", "min_reserve_soc", "export_limit_kw",
            "fuse_kw", "arbitrage", "wear_p")
    blob = json.dumps({k: getattr(p, k) for k in keys}, sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:10]


# --- results -----------------------------------------------------------------------------------

def summarise(results: dict[str, dict], actual: dict[str, dict], names: dict[str, dict], days: list[str]) -> dict:
    """Ranking over `days` (scenarios missing any of them are left out). `results`: id -> {"sig", "days": {day:
    result}}. Costs are also given per 30.44 days so periods compare."""
    rows = []
    n = len(days)
    for sid, res in results.items():
        per_day = res.get("days", {})
        have = [per_day[d] for d in days if d in per_day]
        if n == 0 or len(have) < n:
            continue
        total = sum(x["cost"] for x in have)
        meta = names.get(sid, {})
        rows.append({"id": sid, "name": meta.get("name", sid), "notes": meta.get("notes", ""),
                     "total": round(total, 2), "per_month": round(total / n * MONTH_DAYS, 2),
                     "import_kwh": round(sum(x["import_kwh"] for x in have), 1),
                     "export_kwh": round(sum(x["export_kwh"] for x in have), 1),
                     "export_income": round(sum(x["export_income"] for x in have), 2),
                     "standing": round(sum(x["standing"] for x in have), 2),
                     "estimated": any(x.get("estimated") for x in have)})
    rows.sort(key=lambda r: r["total"])
    base = next((r for r in rows if r["id"] == "current"), None)
    act = [actual[d] for d in days if d in actual]
    actual_total = round(sum(x["cost"] for x in act), 2) if len(act) == n and n else None
    for r in rows:
        if base:
            r["vs_current_month"] = round(r["per_month"] - base["per_month"], 2)
        if actual_total is not None:
            r["vs_actual_month"] = round((r["total"] - actual_total) / n * MONTH_DAYS, 2)
    return {"days": n, "from": days[0] if days else None, "to": days[-1] if days else None,
            "actual_total": actual_total,
            "actual_per_month": round(actual_total / n * MONTH_DAYS, 2) if actual_total is not None else None,
            "ranking": rows}


def opportunities(summary: dict) -> list[dict]:
    """Scenarios that beat your tariff (same optimiser) by enough to be worth considering."""
    if summary["days"] < MIN_DAYS_TO_COMPARE:
        return []
    base = next((r for r in summary["ranking"] if r["id"] == "current"), None)
    if base is None:
        return []
    out = []
    for r in summary["ranking"]:
        saving = base["per_month"] - r["per_month"]
        enough = saving >= NOTIFY_MIN_GBP_MONTH and saving >= NOTIFY_MIN_SHARE * abs(base["per_month"])
        if r["id"] != "current" and enough:
            out.append({**r, "saving_month": round(saving, 2)})
    return out


def comparison_days(results: dict[str, dict], recorded: list[str], window: int) -> list[str]:
    """The latest `window` recorded days that the baseline has results for."""
    base = (results.get("current") or {}).get("days", {})
    return [d for d in recorded if d in base][-window:]


def date_range(days: list[str]) -> tuple[date, date] | None:
    if not days:
        return None
    return date.fromisoformat(days[0]), date.fromisoformat(days[-1]) + timedelta(days=1)
