"""The low-write study on your own recorded days: what the plan would earn, and how often it would change the inverter's
timed windows, if it were allowed to do less (docs/plans/low-write-mode.md, stage L1).

Nothing here changes what PowerEngine does. Each recorded day is planned again with the optimiser, knowing that day's
load, solar and prices (a hindsight plan, so forecast error is left out of the comparison), under a few profiles of what
the plan may do: plain self-use, the full plan at the timed windows' own price per change, and the two low-write tiers.
It reports the money, the window changes and the inverter writes the plan implies (counted as the app counts them) per
day. It answers "what would a low-write plan cost me on my tariff and my days?" before anyone has to switch it on.

Pure: records in, results out. `Study` keeps the per-day results in one small file so a night only plans the new day.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from .decide import EXPORT, HOLD, SELF_USE
from .forecast import Slot
from .optimiser import HOLD_K, KIND, NONE_K, optimise
from .planner import Params, PlanSlot, step
from .schedule import forecast_writes
from .simulator import complete, mark_overnight, rec_export_rate, rec_import_rate

METHOD = 1                                   # change when the evaluation changes, so cached results are replanned
LOW_WRITE_PRICE_P = 10.0                     # the price per window change the proposed budget settles at (pence)
BATTERY_VOLTS = 52.0                         # as the app converts power to the inverter's current settings
DEFAULT_DAYS = 14

# id, label, tier of what the plan may do, price per window change (None: the timed windows' own),
# arbitrage (None: as set)
PROFILES = (
    ("self_use", "Plain self-use (events honoured)", 0, None, None),
    ("full", "Full plan", 4, None, None),
    ("overnight_cycle", "Overnight cycle (low-write)", 2, LOW_WRITE_PRICE_P, True),
    ("overnight_charge", "Overnight charge only (low-write)", 1, LOW_WRITE_PRICE_P, False),
)
NOTE = ("Each day is planned again knowing that day's load, solar and prices, so the figures compare what each plan "
        "could do, not what it did. Writes are what the plan needs beyond leaving the inverter alone, counted as a "
        "timed-window inverter would.")


def profile_params(base: Params, profile: tuple, timed_price_p: float) -> Params:
    """`base` with the profile's tier, price per window change and arbitrage setting."""
    _id, _label, tier, price, arbitrage = profile
    price = timed_price_p if price is None else price
    return replace(base, plan_tier=tier, switch_cost_p=price,
                   overnight_switch_cost_p=max(base.overnight_switch_cost_p, price),
                   arbitrage=base.arbitrage if arbitrage is None else arbitrage)


def _slots(records: list[dict], shift_days: int = 0, lookahead: bool = False) -> list[Slot]:
    """Half-hour slots from recorded half-hours. For tomorrow's look-ahead only what is known the day before: the
    standard tariff (the fixed overnight window), no smart slots, no car, no events."""
    out = []
    for r in sorted(records, key=lambda x: x["start"]):
        v = r.get("v") or {}
        start = datetime.fromisoformat(r["start"]) + timedelta(days=shift_days)
        if lookahead:
            std = v.get("std")
            price = std if std is not None else rec_import_rate(r)
            out.append(Slot(start=start, price=price, export=rec_export_rate(r),
                            solar_kwh=r.get("solar") or 0.0, load_kwh=r.get("house") or 0.0))
            continue
        car = r.get("car") or 0.0
        out.append(Slot(start=start, price=rec_import_rate(r), export=rec_export_rate(r),
                        solar_kwh=r.get("solar") or 0.0, load_kwh=(r.get("house") or 0.0) + car, car_kw=car / 0.5,
                        smart_slot=bool(v.get("slot")), axle=bool(r.get("axle")), free=bool(r.get("free"))))
    return out


def usable(records: list[dict]) -> bool:
    """A complete recorded day with a price for every half-hour."""
    return bool(records) and complete(records) and all(rec_import_rate(r) is not None for r in records)


def evaluate_day(records: list[dict], next_records: list[dict] | None, p: Params, tz,
                 soc: float | None = None) -> dict:
    """One planned day under `p`: {"net": GBP (cost less the energy left, valued at the day's cheapest price),
    "changes": window changes, "current_only": of which hold <-> charge, "writes": counted inverter writes, "acts":
    one letter per half-hour}. `next_records`: the next recorded day, for the look-ahead; None uses this day's own
    pattern."""
    today = _slots(records)
    nxt = _slots(next_records, lookahead=True) if next_records else _slots(records, shift_days=1, lookahead=True)
    mark_overnight(today, nxt, tz)
    soc = soc if soc is not None else (records[0].get("soc_start") if records[0].get("soc_start") is not None else 50.0)
    res = optimise(today + nxt, soc, p, wear=p.wear_p / 100, prev_action=SELF_USE)
    acts = (res or {"actions": [SELF_USE] * (len(today) + len(nxt))})["actions"]
    lvl, cost, plan = soc, 0.0, []
    for i, (s, a) in enumerate(zip(today + nxt, acts, strict=False)):
        ps = PlanSlot(s, a, "", target_soc=100.0)
        if i < len(today):
            lvl = step(ps, lvl, p)
            cost += ps.cost
        plan.append(ps)
    prices = [s.price for s in today if s.price is not None]
    net = cost - lvl / 100 * p.capacity_kwh * (min(prices) if prices else 0.0)
    kinds = [NONE_K] + [KIND[a] for a in acts[:len(today)]]
    moves = [(a, b) for a, b in zip(kinds, kinds[1:], strict=False) if a != b]
    current_only = sum(1 for a, b in moves if {a, b} == {HOLD_K, 2})
    day_start = today[0].start
    args = (day_start, tz, {}, BATTERY_VOLTS, p.max_charge_kw * 1000, p.max_discharge_kw * 1000)
    idle = [PlanSlot(s, SELF_USE, "", target_soc=100.0) for s in today + nxt]
    blank = sum(forecast_writes(idle, *args, hours=24).values())      # setting up an inverter nothing is programmed on
    writes = max(0, sum(forecast_writes(plan, *args, hours=24).values()) - blank)
    letters = {SELF_USE: ".", HOLD: "h", EXPORT: "X"}
    return {"net": round(net, 3), "changes": len(moves), "current_only": current_only, "writes": writes,
            "acts": "".join(letters.get(a, "C" if KIND[a] == 2 else "A") for a in acts[:len(today)])}


def summarise(per_day: dict[str, dict[str, dict]], days: list[str]) -> dict:
    """Averages over `days` of the per-day results: per profile, the saving against plain self-use, the share of the
    full plan's saving it keeps, the window changes and the estimated writes a day."""
    rows = []
    n = len(days)
    if not n:
        return {"days": 0, "profiles": [], "note": NOTE}

    def mean(pid: str, key: str) -> float:
        return sum(per_day[d][pid][key] for d in days) / n
    base = mean("self_use", "net")
    full_saving = base - mean("full", "net")
    for pid, label, tier, price, _arb in PROFILES:
        saving = base - mean(pid, "net")
        rows.append({"id": pid, "label": label, "tier": tier, "price_p": price, "saving_gbp_day": round(saving, 2),
                     "kept_pct": (round(100 * saving / full_saving)
                                  if full_saving > 0.005 and pid != "self_use" else None),
                     "changes_day": round(mean(pid, "changes"), 1), "writes_day": round(mean(pid, "writes"), 1)})
    return {"days": n, "from": days[0], "to": days[-1], "profiles": rows, "note": NOTE}


def _signature(base: Params, timed_price_p: float) -> str:
    keys = ("capacity_kwh", "max_charge_kw", "max_discharge_kw", "efficiency", "min_reserve_soc", "target_soc",
            "cheap_cap_p", "axle_enabled", "free_enabled", "hold_for_car", "fill_when_cheap", "arbitrage",
            "deep_overnight", "arbitrage_min_soc", "arbitrage_max_soc", "arbitrage_band_penalty_p", "wear_p",
            "min_margin_p", "export_limit_kw")
    blob = json.dumps([METHOD, timed_price_p, [list(x) for x in PROFILES], {k: getattr(base, k) for k in keys}],
                      sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()[:10]


class Study:
    """The study's results on disk (`lowwrite.json` in `folder`): per recorded day and profile, so a night only plans
    the days it has not seen. A change of settings that matter to the plan replans everything."""

    def __init__(self, folder: str):
        self.path = os.path.join(folder, "lowwrite.json")
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = {}
        self.sig: str = data.get("sig", "")
        self.days: dict[str, dict[str, dict]] = data.get("days", {})
        self.summary: dict = data.get("summary", {})

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"sig": self.sig, "days": self.days, "summary": self.summary}, fh, separators=(",", ":"))
        os.replace(tmp, self.path)

    def run(self, recorded_days: list[str], day_records, base: Params, timed_price_p: float, tz, now: datetime,
            max_days: int = DEFAULT_DAYS, limit: int | None = None) -> dict:
        """Plan the newest `max_days` usable recorded days that have no result yet, and return the summary.
        `day_records`: ISO date -> that day's records. Today (not yet complete) is never included.
        `limit` plans at most that many new days per call (the summary's `pending` says how many are left)."""
        sig = _signature(base, timed_price_p)
        if sig != self.sig:
            self.sig, self.days = sig, {}
        today = now.astimezone(tz).date().isoformat() if tz else now.date().isoformat()
        candidates = [d for d in recorded_days if d < today][-(max_days + 1):]
        use = [d for d in candidates if usable(day_records(d))][-max_days:]
        done, pending = 0, 0
        for d in use:
            if d in self.days and set(self.days[d]) == {x[0] for x in PROFILES}:
                continue
            if limit is not None and done >= limit:
                pending += 1
                continue
            done += 1
            recs = day_records(d)
            nxt_day = (datetime.fromisoformat(d) + timedelta(days=1)).date().isoformat()
            nxt = day_records(nxt_day) if nxt_day in candidates and usable(day_records(nxt_day)) else None
            self.days[d] = {prof[0]: evaluate_day(recs, nxt, profile_params(base, prof, timed_price_p), tz)
                            for prof in PROFILES}
        self.days = {d: r for d, r in self.days.items() if d in use}
        self.summary = summarise(self.days, [d for d in use if d in self.days])
        self.summary["pending"] = pending
        self.summary["updated"] = now.astimezone(timezone.utc).isoformat(timespec="seconds")
        self.save()
        return self.summary


__all__ = ["PROFILES", "Study", "evaluate_day", "profile_params", "summarise", "usable"]
