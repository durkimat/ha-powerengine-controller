"""Cost layers: what each feature saved, from the recorded energy flows.

Per half-hour (see energy.py for the flows, tariff.py for the rates, ledger.py for the battery):

S0      no solar, no battery: house kWh x standard rate + car kWh x overnight rate (+ standing charge per day)
solar   solar to house x standard + solar to car x overnight + solar exported or stored x export rate
smart   grid to house x (standard - actual) + grid to car x (overnight - actual) + grid to battery x (standard - actual)
battery value delivered by the battery (house x standard, car x overnight, export x export rate)
        minus the ledger basis of the energy used; split into S3a (a simulated plain self-use battery) and S3b (the
        rest: the app's timing). Grid-sourced energy exported goes to arbitrage instead.
stored  change in the ledger's value: energy bought or stored today for use later (adds to today's cost). The
        ledger is matched to the battery's real state of charge every half-hour; corrections (efficiency or sensor
        error) are part of the change and so show up in 'unexplained'.
actual  metered import x actual rate - metered export x export rate (+ standing charge)

S0 - solar - smart - battery - arbitrage + stored = actual, apart from what the meters don't account for
(losses, sensor mismatch), which is shown as 'unexplained'.

Axle and free-power half-hours are kept out of the everyday layers and recorded as events instead.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from .ledger import Ledger
from .tariff import Rates

METHOD_VERSION = 4        # 3: export rate falls back to your current one when history had none
                          # 4: Axle exports also earn the export rate (EDF pays it on top of Axle's £1)
                          # 5: adds day_scenarios() to each day summary (no change to the existing layers)
# daily energy totals (kWh) shown for checking against the inverter's own counters
ENERGY_KEYS = ("grid_import", "grid_export", "solar", "house", "car", "battery_in", "battery_out", "b_e",
               "unallocated_src", "unallocated_sink", "correction_kwh")
LAYERS = ("s0", "solar", "smart", "s3a", "s3b", "arbitrage", "stored", "actual", "standing", "unexplained")


@dataclass
class SimDefault:
    """Plain self-use with the same battery: charges only from surplus solar, covers house (and car) load."""
    kwh: float | None = None        # stored (after charging losses)

    def step(self, need: float, surplus: float, hours: float, cap: float, floor_kwh: float, max_kw: float,
             eff: float) -> float:
        """Advance one period; returns kWh delivered to the load."""
        if self.kwh is None:
            return 0.0
        into = min(surplus, max_kw * hours, max(0.0, cap - self.kwh) / eff)
        self.kwh += into * eff
        out = min(need, max_kw * hours, max(0.0, self.kwh - floor_kwh) * eff)
        self.kwh -= out / eff
        return out


def _basis(lots) -> float:
    return sum(x.kwh * x.basis for x in lots)


def process(rec: dict, rt: Rates, ledger: Ledger, sim: SimDefault, *, capacity: float, eff: float,
            floor_soc: float, max_kw: float, includes_ev: bool, axle_value: float = 1.0,
            axle_plus_export: bool = True) -> dict:
    """Value one half-hour. Updates the ledger and the default-battery simulation. Returns the record's values."""
    rte = eff * eff
    act, std, ovn, exp = rt.actual, rt.standard, rt.overnight, rt.export
    k = {f: rec.get(f, 0.0) or 0.0 for f in ("s_h", "s_c", "s_b", "s_e", "b_h", "b_c", "b_e", "g_h", "g_c", "g_b")}
    house = k["s_h"] + k["b_h"] + k["g_h"]
    car = k["s_c"] + k["b_c"] + k["g_c"]
    hours = max(rec.get("seconds") or 0.0, 1.0) / 3600

    soc0 = rec.get("soc_start")
    first = sim.kwh is None
    if first and soc0 is not None:
        sim.kwh = soc0 / 100 * capacity
    value_before = ledger.value
    correction_kwh = correction_value = 0.0
    if soc0 is not None:
        # keep the ledger matched to what the battery really holds. The first time this is the opening balance;
        # after that any difference is a correction (efficiency or sensor error), which ends up in 'unexplained'.
        kwh0, val0 = ledger.kwh, ledger.value
        ledger.reconcile(soc0 / 100 * capacity / eff, ovn)
        if first:
            value_before = ledger.value
        else:
            correction_kwh, correction_value = ledger.kwh - kwh0, ledger.value - val0

    # battery ledger: in first, then out (oldest first)
    ledger.add(k["s_b"], "solar", exp)
    ledger.add(k["g_b"], "grid", std)
    used_h = ledger.take(k["b_h"], rte, ovn)
    used_c = ledger.take(k["b_c"], rte, ovn)
    used_e = ledger.take(k["b_e"], rte, ovn)
    e_in = sum(x.kwh for x in used_e) or 1.0
    e_grid_share = sum(x.kwh for x in used_e if x.source == "grid") / e_in if k["b_e"] else 0.0
    e_solar_lots = [x for x in used_e if x.source == "solar"]
    e_grid_lots = [x for x in used_e if x.source == "grid"]

    battery = (k["b_h"] * std - _basis(used_h)) + (k["b_c"] * ovn - _basis(used_c)) \
        + (k["b_e"] * (1 - e_grid_share) * exp - _basis(e_solar_lots))
    arbitrage = k["b_e"] * e_grid_share * exp - _basis(e_grid_lots)

    # plain self-use simulation (S3a)
    load = house + (car if includes_ev else 0.0)
    solar = k["s_h"] + k["s_c"] + k["s_b"] + k["s_e"]
    need, surplus = max(0.0, load - solar), max(0.0, solar - load)
    delivered = sim.step(need, surplus, hours, capacity, floor_soc / 100 * capacity, max_kw, eff)
    car_part = min(delivered * (car / load), delivered) if (includes_ev and load > 0) else 0.0
    s3a = (delivered - car_part) * (std - exp / rte) + car_part * (ovn - exp / rte)

    out = {
        "act": act, "std": std, "ovn": ovn, "exp": exp, "slot": rt.smart_slot, "peak": rt.peak,
        "s0": house * std + car * ovn,
        "solar": k["s_h"] * std + k["s_c"] * ovn + (k["s_e"] + k["s_b"]) * exp,
        "smart": k["g_h"] * (std - act) + k["g_c"] * (ovn - act) + k["g_b"] * (std - act),
        "battery": battery,
        "s3a": s3a,
        "arbitrage": arbitrage,
        "stored": ledger.value - value_before,
        "actual": (rec.get("grid_import") or 0.0) * act - (rec.get("grid_export") or 0.0) * exp,
        "standing_part": (rec.get("standing") or 0.0) * hours / 24,
        "ledger_kwh": ledger.kwh, "ledger_value": ledger.value, "sim_kwh": sim.kwh,
        "correction_kwh": correction_kwh, "correction": correction_value,
    }
    event = "axle" if rec.get("axle") else ("free_power" if rec.get("free") else None)
    out["event"] = event
    if event == "axle":
        exported = k["b_e"] + k["s_e"]
        out["event_kwh"] = exported
        per_kwh = axle_value + (exp if axle_plus_export else 0.0)   # Axle's payment, plus the supplier's export rate
        out["event_gross"] = exported * per_kwh
        # what the event added: less the battery energy's cost, and the export rate the solar would have earned anyway
        out["event_net"] = exported * per_kwh - _basis(used_e) - k["s_e"] * exp
    elif event == "free_power":
        out["event_kwh"] = rec.get("grid_import") or 0.0
        out["event_gross"] = out["event_net"] = out["event_kwh"] * std
    return out


def day_scenarios(records: list[dict], *, capacity: float, eff: float, floor_soc: float, max_kw: float = 5.0,
                  includes_ev: bool, standing: float = 0.0) -> dict:
    """Whole-day cost under five what-if scenarios, from the day's valued half-hours (adds to day_summary).

    none      every kWh from the grid: house at the standard rate, car at the overnight rate.
    solar     your solar covers the house (and the car, once the house is covered) first; the surplus is
              exported; no battery.
    tariff    the same, but grid energy is priced at the real half-hourly rate (smart slots, overnight, peak).
    self_use  solar plus a battery in the inverter's own default mode: a plain self-use simulation, seeded from
              this day's opening state of charge (independent of other days).
    actual    what the meters actually billed (as day_summary's 'actual', before standing).

    self_use and actual are then adjusted for the day's change in stored battery energy, valued at the day's
    median overnight rate: charging tonight for tomorrow doesn't make today look artificially dear. 'carry' is
    what that adjustment moved for actual; 'events_metered' is the metered cost of Axle/free-power half-hours
    (which are excluded from the scenarios above, same as day_summary's layers); 'paid' adds it back to actual.
    Every field is rounded to 2dp.
    """
    floor_kwh = floor_soc / 100 * capacity
    none = solar_s = tariff_s = actual_raw = events_metered = self_use = 0.0
    ovn_rates: list[float] = []
    sim_kwh = None
    first_soc = next((r.get("soc_start") for r in records if r.get("soc_start") is not None), None)
    if first_soc is not None:
        sim_kwh = first_soc / 100 * capacity
    sim_start_kwh = sim_kwh
    for r in records:
        v = r.get("v") or {}
        act, std, ovn, exp = v.get("act", 0.0), v.get("std", 0.0), v.get("ovn", 0.0), v.get("exp", 0.0)
        if v.get("event"):
            events_metered += (r.get("grid_import") or 0.0) * act - (r.get("grid_export") or 0.0) * exp
            continue
        ovn_rates.append(ovn)
        k = {f: r.get(f, 0.0) or 0.0 for f in ("s_h", "s_c", "s_b", "s_e", "b_h", "b_c", "b_e", "g_h", "g_c", "g_b")}
        house = k["s_h"] + k["b_h"] + k["g_h"]
        car = k["s_c"] + k["b_c"] + k["g_c"]
        solar = k["s_h"] + k["s_c"] + k["s_b"] + k["s_e"]
        hours = max(r.get("seconds") or 0.0, 1.0) / 3600
        actual_raw += (r.get("grid_import") or 0.0) * act - (r.get("grid_export") or 0.0) * exp

        # car left out of load: it's not part of what the battery/solar what-ifs manage (hold_for_car is off), so
        # it's costed the same, flat, in every scenario instead of being optimised against solar/the battery
        load = house + car if includes_ev else house
        car_extra = 0.0 if includes_ev else car * act
        need, surplus = max(0.0, load - solar), max(0.0, solar - load)
        car_need = min(car, need) if includes_ev else 0.0        # solar covers the house first

        none += house * std + (car * ovn if includes_ev else car_extra)
        solar_s += (need - car_need) * std + car_need * ovn - surplus * exp + car_extra
        tariff_s += need * act - surplus * exp + car_extra

        if sim_kwh is not None:
            into = min(surplus, max_kw * hours, max(0.0, capacity - sim_kwh) / eff)
            sim_kwh += into * eff
            out = min(need, max_kw * hours, max(0.0, sim_kwh - floor_kwh) * eff)
            sim_kwh -= out / eff
        else:
            into = out = 0.0
        self_use += (need - out) * act - (surplus - into) * exp + car_extra

    ref_rate = statistics.median(ovn_rates) if ovn_rates else 0.0
    sim_end_kwh = sim_kwh if sim_kwh is not None else sim_start_kwh

    def _real_kwh(rec, key):
        soc = rec.get(key) if rec else None
        return soc / 100 * capacity if soc is not None else None

    real_start_kwh = _real_kwh(records[0] if records else None, "soc_start")
    real_end_kwh = _real_kwh(records[-1] if records else None, "soc_end")

    sim_delta = (sim_end_kwh - sim_start_kwh) if (sim_start_kwh is not None and sim_end_kwh is not None) else 0.0
    self_use_adj = self_use - sim_delta * ref_rate / eff
    have_real = real_start_kwh is not None and real_end_kwh is not None
    real_delta = (real_end_kwh - real_start_kwh) if have_real else 0.0
    actual_adj = actual_raw - real_delta * ref_rate / eff
    carry = actual_raw - actual_adj

    r2 = lambda x: round(x, 2)  # noqa: E731
    return {
        "none": r2(none + standing), "solar": r2(solar_s + standing), "tariff": r2(tariff_s + standing),
        "self_use": r2(self_use + standing), "self_use_adj": r2(self_use_adj + standing),
        "actual": r2(actual_raw + standing), "actual_adj": r2(actual_adj + standing),
        "carry": r2(carry), "events_metered": r2(events_metered), "paid": r2(actual_raw + standing + events_metered),
        "ref_rate": r2(ref_rate),
    }


def day_summary(records: list[dict], standing_per_day: float | None = None, complete: bool = True, *,
                capacity: float | None = None, eff: float | None = None, floor_soc: float | None = None,
                max_kw: float = 5.0, includes_ev: bool | None = None) -> dict:
    """Sum a day's valued half-hours into layers (GBP). Event half-hours are left out of the layers."""
    tot = dict.fromkeys(("s0", "solar", "smart", "battery", "s3a", "arbitrage", "stored", "actual"), 0.0)
    energy = dict.fromkeys(ENERGY_KEYS, 0.0)
    standing = 0.0
    events: dict[str, dict] = {}
    for r in records:
        v = r.get("v") or {}
        standing += v.get("standing_part", 0.0)
        for key in ENERGY_KEYS:
            energy[key] += (v.get("correction_kwh", 0.0) if key == "correction_kwh" else (r.get(key) or 0.0))
        if v.get("event"):
            e = events.setdefault(v["event"], {"kwh": 0.0, "gross": 0.0, "net": 0.0, "metered": 0.0})
            e["kwh"] += v.get("event_kwh", 0.0)
            e["gross"] += v.get("event_gross", 0.0)
            e["net"] += v.get("event_net", 0.0)
            e["metered"] += v.get("actual", 0.0)
            continue
        for key in tot:
            tot[key] += v.get(key, 0.0)
    if complete and standing_per_day is not None:
        standing = standing_per_day
    s3b = tot["battery"] - tot["s3a"]
    s0 = tot["s0"] + standing
    actual = tot["actual"] + standing
    unexplained = actual - (s0 - tot["solar"] - tot["smart"] - tot["battery"] - tot["arbitrage"] + tot["stored"])
    r2 = lambda x: round(x, 2)  # noqa: E731
    out = {
        "s0": r2(s0), "solar": r2(tot["solar"]), "smart": r2(tot["smart"]), "s3a": r2(tot["s3a"]), "s3b": r2(s3b),
        "arbitrage": r2(tot["arbitrage"]), "stored": r2(tot["stored"]), "actual": r2(actual),
        "standing": r2(standing), "unexplained": r2(unexplained),
        "events": {k: {kk: round(vv, 2) for kk, vv in e.items()} for k, e in events.items()},
        "half_hours": len(records), "complete": complete, "method": METHOD_VERSION,
        "energy": {k: round(v, 1) for k, v in energy.items()},
        "coverage": round(sum(r.get("seconds") or 0.0 for r in records) / 86400, 3),
    }
    if capacity is not None:
        out["scenarios"] = day_scenarios(records, capacity=capacity, eff=eff, floor_soc=floor_soc, max_kw=max_kw,
                                          includes_ev=includes_ev, standing=standing)
    return out


def steps(summary: dict) -> list[tuple[str, float]]:
    """Cost after each step: S0, +solar, +smart, +battery default, +battery app, +arbitrage, +stored, actual."""
    s = summary
    s1 = s["s0"] - s["solar"]
    s2 = s1 - s["smart"]
    s3a = s2 - s["s3a"]
    s3b = s3a - s["s3b"]
    s4 = s3b - s["arbitrage"]
    return [("S0", s["s0"]), ("S1", s1), ("S2", s2), ("S3a", s3a), ("S3b", s3b), ("S4", s4),
            ("Stored", s4 + s["stored"]), ("Actual", s["actual"])]


def waterfall(days_summaries: list[dict], period: str) -> dict:
    """Sum day_scenarios() over a period into a savings waterfall's steps (GBP, running totals chain exactly).

    days_summaries: day_summary() dicts with a 'scenarios' field (see day_scenarios), oldest first, as
    CostBook.recent() returns them. period: 'yesterday', 'week' (last 7 complete days), 'month' (this calendar
    month's complete days so far, the month of the last day in days_summaries) or 'days30' (last 30 complete
    days). Steps: total 'No solar or battery', then 'Solar', 'EDF tariff', 'Battery on self-use', 'PowerEngine'
    to the subtotal 'Everyday cost', then 'Battery carry-over' and (only if it moved money) 'Axle & free power'
    to the total 'You paid'. Negative steps are savings.
    """
    complete = [d for d in days_summaries if d.get("complete") and d.get("scenarios")]
    if period == "yesterday":
        chosen = complete[-1:]
    elif period == "week":
        chosen = complete[-7:]
    elif period == "days30":
        chosen = complete[-30:]
    elif period == "month":
        this_month = (days_summaries[-1]["date"] if days_summaries else "")[:7]
        chosen = [d for d in complete if d["date"].startswith(this_month)]
    else:
        raise ValueError(f"unknown period {period!r}")

    fields = ("none", "solar", "tariff", "self_use_adj", "actual_adj", "carry", "events_metered")
    sums = dict.fromkeys(fields, 0.0)
    for d in chosen:
        sc = d["scenarios"]
        for key in fields:
            sums[key] += sc.get(key, 0.0)

    # each running total is rounded once; every step is the difference of two rounded totals, so steps and
    # totals always chain exactly to the penny (no separate rounding-remainder allocation needed).
    r2 = lambda x: round(x, 2)  # noqa: E731
    r0 = r2(sums["none"])
    r1 = r2(sums["solar"])
    r2v = r2(sums["tariff"])
    r3 = r2(sums["self_use_adj"])
    r4 = r2(sums["actual_adj"])
    r5 = r2(sums["actual_adj"] + sums["carry"])
    r6 = r2(sums["actual_adj"] + sums["carry"] + sums["events_metered"])

    steps_list = [
        {"label": "No solar or battery", "kind": "total", "value": r0},
        {"label": "Solar", "kind": "step", "value": r2(r1 - r0)},
        {"label": "EDF tariff", "kind": "step", "value": r2(r2v - r1)},
        {"label": "Battery on self-use", "kind": "step", "value": r2(r3 - r2v)},
        {"label": "PowerEngine", "kind": "step", "value": r2(r4 - r3)},
        {"label": "Everyday cost", "kind": "subtotal", "value": r4},
        {"label": "Battery carry-over", "kind": "step", "value": r2(r5 - r4)},
    ]
    if round(sums["events_metered"], 2) != 0:
        steps_list.append({"label": "Axle & free power", "kind": "step", "value": r2(r6 - r5)})
    steps_list.append({"label": "You paid", "kind": "total", "value": r6})

    return {"period": period, "days": len(chosen), "from": chosen[0]["date"] if chosen else None,
            "to": chosen[-1]["date"] if chosen else None, "steps": steps_list}
