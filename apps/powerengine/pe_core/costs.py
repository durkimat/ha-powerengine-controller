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

from dataclasses import dataclass

from .ledger import Ledger
from .tariff import Rates

METHOD_VERSION = 2
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
            floor_soc: float, max_kw: float, includes_ev: bool, axle_value: float = 1.0) -> dict:
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
        out["event_gross"] = exported * axle_value
        out["event_net"] = exported * axle_value - _basis(used_e) - k["s_e"] * exp
    elif event == "free_power":
        out["event_kwh"] = rec.get("grid_import") or 0.0
        out["event_gross"] = out["event_net"] = out["event_kwh"] * std
    return out


def day_summary(records: list[dict], standing_per_day: float | None = None, complete: bool = True) -> dict:
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
    return {
        "s0": r2(s0), "solar": r2(tot["solar"]), "smart": r2(tot["smart"]), "s3a": r2(tot["s3a"]), "s3b": r2(s3b),
        "arbitrage": r2(tot["arbitrage"]), "stored": r2(tot["stored"]), "actual": r2(actual),
        "standing": r2(standing), "unexplained": r2(unexplained),
        "events": {k: {kk: round(vv, 2) for kk, vv in e.items()} for k, e in events.items()},
        "half_hours": len(records), "complete": complete, "method": METHOD_VERSION,
        "energy": {k: round(v, 1) for k, v in energy.items()},
        "coverage": round(sum(r.get("seconds") or 0.0 for r in records) / 86400, 3),
    }


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
