"""The Simulator's overnight job: refresh tariffs, fetch any missing rates, replay new days, rank.

Everything lives in one folder (next to config.yaml): catalogue.json (products and tariff codes), rates/ (one
cached file per tariff and kind, extended as needed) and results.json (per scenario, per day). `run()` is a
generator that yields after each small unit of work, so the app can spread a run over many short callbacks.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from . import kraken
from .heatpump import HeatPumpSettings, day_heat, hlc_kw_per_k, schedule
from .simhistory import History
from .simulator import (
    MONTH_DAYS,
    REGION,
    actual_cost,
    comparison_days,
    complete,
    day_slots,
    opportunities,
    price_fn,
    rate_key,
    run_day,
    scenarios,
    signature,
    sim_params,
    standing_for,
    summarise,
)
from .weather import Weather
from .weather import _get as weather_get

CATALOGUE_MAX_AGE = timedelta(hours=20)
WINDOW_DAYS = 30
SAVE_EVERY = 100


def _read(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, separators=(",", ":"))
    os.replace(tmp, path)


class SimStore:
    def __init__(self, folder: str):
        self.folder = folder
        self.catalogue = _read(self._p("catalogue.json"), {"fetched": None, "region": REGION, "products": [],
                                                            "seen": []})
        self.results = _read(self._p("results.json"), {})
        self.summary = _read(self._p("summary.json"), None)

    def _p(self, *parts):
        return os.path.join(self.folder, *parts)

    def save(self):
        _write(self._p("catalogue.json"), self.catalogue)
        _write(self._p("results.json"), self.results)
        if self.summary is not None:
            _write(self._p("summary.json"), self.summary)

    def rate_cache(self, key: str) -> dict:
        return _read(self._p("rates", key + ".json"), {"from": None, "to": None, "windows": []})

    def save_rate_cache(self, key: str, data: dict) -> None:
        _write(self._p("rates", key + ".json"), data)


def refresh_catalogue(store: SimStore, now: datetime, fetch=None, log=print) -> list[dict]:
    """Re-read the product lists (at most daily). Returns products never seen before (for a notification)."""
    fetch = fetch or kraken.fetch_json
    cat = store.catalogue
    fetched = datetime.fromisoformat(cat["fetched"]) if cat.get("fetched") else None
    if fetched and now - fetched < CATALOGUE_MAX_AGE and cat.get("products"):
        return []
    known = {(p["supplier"], p["code"]): p for p in cat.get("products", [])}
    first_time = not cat.get("seen")
    new, products = [], []
    for supplier in kraken.SUPPLIERS:
        try:
            listed = kraken.products(supplier, fetch)
        except Exception as err:                                   # keep yesterday's list for this supplier
            log(f"Simulator: couldn't list {supplier} tariffs ({err!r}); using the saved list")
            products += [p for p in cat.get("products", []) if p["supplier"] == supplier]
            continue
        for p in listed:
            old = known.get((supplier, p["code"]))
            if old and old.get("tariff"):
                p["tariff"], p["standing_p"] = old["tariff"], old.get("standing_p")
            else:
                try:
                    p["tariff"], p["standing_p"] = kraken.tariff_code(supplier, p["code"], cat.get("region", REGION),
                                                                     fetch)
                except Exception as err:
                    log(f"Simulator: no tariff code for {p['code']} ({err!r})")
                    p["tariff"], p["standing_p"] = None, None
            products.append(p)
            key = f"{supplier}:{p['code']}"
            if key not in cat["seen"]:
                cat["seen"].append(key)
                if not first_time and p.get("tariff"):
                    new.append(p)
    cat["products"], cat["fetched"] = products, now.isoformat(timespec="seconds")
    return new


def ensure_rates(store: SimStore, ref: dict, kind: str, start: datetime, end: datetime, fetch=None):
    """Extend the cached rate windows to cover [start, end); returns a RateTable."""
    fetch = fetch or kraken.fetch_json
    key = rate_key(ref, kind)
    cache = store.rate_cache(key)
    have_from = datetime.fromisoformat(cache["from"]) if cache.get("from") else None
    have_to = datetime.fromisoformat(cache["to"]) if cache.get("to") else None
    wanted = []
    if have_from is None:
        wanted.append((start, end))
    else:
        if start < have_from:
            wanted.append((start, have_from))
        if end > have_to:
            wanted.append((have_to, end))
    if wanted:
        seen = {w["from"] for w in cache["windows"]}
        for a, b in wanted:
            for w in kraken.rates(ref["supplier"], ref["product"], ref["tariff"], a, b, kind, fetch):
                if w["from"] not in seen:
                    cache["windows"].append(w)
                    seen.add(w["from"])
        cache["from"] = min([start] + ([have_from] if have_from else [])).isoformat()
        cache["to"] = max([end] + ([have_to] if have_to else [])).isoformat()
        store.save_rate_cache(key, cache)
    return kraken.RateTable(cache["windows"])


@dataclass
class SimContext:
    """Extras for the run: your tariff (for imported history days), imported history, heat pump."""
    current: dict | None = None            # {"supplier", "product", "tariff"} of your import tariff
    export_p: float | None = None          # your export rate, GBP/kWh
    history: History | None = None
    house_includes_car: bool = True
    hp: HeatPumpSettings | None = None
    weather: Weather | None = None
    weather_get: object = None


def _date_span(first: date, last: date) -> list[str]:
    out, d = [], first
    while d <= last:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _hp_sig(s: HeatPumpSettings) -> str:
    return hashlib.sha1(json.dumps(s.as_dict(), sort_keys=True).encode()).hexdigest()[:8]


def run(store: SimStore, days: list[str], load_day, params, tz, now: datetime, charger_kw: float,
        fetch=None, log=print, ctx: SimContext | None = None):
    """Generator: one step per fetch or simulated day. Yields progress dicts; the last one has "done": True."""
    fetch = fetch or kraken.fetch_json
    ctx = ctx or SimContext()
    new = refresh_catalogue(store, now, fetch, log)
    yield {"stage": "catalogue", "new_products": len(new)}
    scns = scenarios(store.catalogue["products"])
    names = {s["id"]: {"name": s["name"], "notes": s["notes"]} for s in scns}
    recorded = set(days)
    cache: dict[str, list] = {}

    # imported history: every day from the first imported month up to the day before the first recorded one
    hist_days: list[str] = []
    if ctx.history is not None and ctx.history.months():
        first = date.fromisoformat(ctx.history.months()[0] + "-01")
        last = (date.fromisoformat(min(days)) if days else now.astimezone(tz).date()) - timedelta(days=1)
        hist_days = [d for d in _date_span(first, last) if d not in recorded]
    all_days = sorted(set(days) | set(hist_days))
    if not all_days:
        store.summary = {"windows": {}, "note": "no complete days yet", "updated": now.isoformat(timespec="seconds")}
        yield {"done": True, "new_products": new, "opportunities": []}
        return
    rng_start = datetime.combine(date.fromisoformat(all_days[0]), datetime.min.time(), tzinfo=tz)
    rng_end = datetime.combine(date.fromisoformat(all_days[-1]) + timedelta(days=2), datetime.min.time(), tzinfo=tz)

    # your tariff's rates, to price imported history days (which have energy but no prices)
    cur_tables = {}
    if hist_days and ctx.current and ctx.current.get("tariff"):
        try:
            for kind in ("standard-unit-rates", "standing-charges"):
                cur_tables[kind] = ensure_rates(store, ctx.current, kind, rng_start, rng_end, fetch)
                yield {"stage": "rates", "scenario": "your tariff"}
        except Exception as err:
            log(f"Simulator: couldn't get your tariff's rates ({err!r}); imported days are left out")
            cur_tables = {}

    def recs(d: str) -> list[dict]:
        if d in cache:
            return cache[d]
        if d in recorded:
            out = load_day(d)
        elif cur_tables and ctx.history is not None:
            out = ctx.history.day_records(date.fromisoformat(d), tz, ctx.house_includes_car)
            unit, stand = cur_tables["standard-unit-rates"], cur_tables["standing-charges"]
            day_start = datetime.combine(date.fromisoformat(d), datetime.min.time(), tzinfo=tz)
            standing = stand.at(day_start.astimezone(timezone.utc)) or (stand.w[0][2] if stand.w else 0.0)
            for r in out:
                t = datetime.fromisoformat(r["start"])
                v = unit.at(t)
                r["import_rate"] = v if v is not None else unit.pattern_at(t, tz)
                r["export_rate"] = ctx.export_p or 0.0
                r["standing"] = standing
                r["priced_from"] = "your tariff today"
        else:
            out = []
        if len(cache) > 400:
            cache.clear()
        cache[d] = out
        return out

    use_days = []
    for d in all_days:
        rs = recs(d)
        if complete(rs) and all(r.get("import_rate") is not None for r in rs):
            use_days.append(d)
    if not use_days:
        store.summary = {"windows": {}, "note": "no complete days yet", "updated": now.isoformat(timespec="seconds")}
        yield {"done": True, "new_products": new, "opportunities": []}
        return
    p = sim_params(params)
    sig = signature(p)
    actual = {d: {**actual_cost(recs(d)), "estimated": d not in recorded} for d in use_days}
    steps = 0

    def replay(sid: str, scn: dict, tables: dict, run_sig: str, extra_load=None):
        nonlocal steps
        res = store.results.get(sid)
        if not res or res.get("sig") != run_sig:
            res = store.results[sid] = {"sig": run_sig, "days": {}}
        per_day = res["days"]
        fn = price_fn(scn, tables, tz)
        shift = scn.get("import") is not None
        first = next((i for i, d in enumerate(use_days)
                      if d not in per_day or (not per_day[d].get("lookahead") and i + 1 < len(use_days))), None)
        if first is None:
            return
        prev = use_days[first - 1] if first > 0 else None
        soc = per_day[prev]["end_soc"] if prev in per_day else (recs(use_days[first])[0].get("soc_start") or 50.0)
        prod = next((x for x in store.catalogue["products"]
                     if scn.get("import") and x["code"] == scn["import"]["product"]), None)

        def slots_for(d):
            sl, est = day_slots(recs(d), fn, shift, charger_kw)
            info = extra_load(d, sl) if extra_load else None
            return sl, est, info

        for i in range(first, len(use_days)):
            d = use_days[i]
            slots, est, info = slots_for(d)
            if info is False:
                break                                       # e.g. no weather for this day yet
            nxt = []
            if i + 1 < len(use_days) and date.fromisoformat(use_days[i + 1]) == date.fromisoformat(d) + timedelta(1):
                nxt, _, nxt_info = slots_for(use_days[i + 1])
                if nxt_info is False:
                    nxt = []
            day_start = datetime.combine(date.fromisoformat(d), datetime.min.time(), tzinfo=tz)
            standing = standing_for(scn, tables, day_start.astimezone(timezone.utc), recs(d),
                                    prod.get("standing_p") if prod else None)
            if any(sl.price is None for sl in slots):
                break                                       # no price for this day (e.g. not yet published)
            r = run_day(slots, nxt, soc, p, standing)
            r["estimated"] = est or d not in recorded
            r["lookahead"] = i + 1 < len(use_days)           # the next day was available (used if consecutive)
            if info:
                r.update(info)
            per_day[d] = r
            soc = r["end_soc"]
            steps += 1
            if steps % SAVE_EVERY == 0:
                store.save()
            yield {"stage": "day", "scenario": sid, "day": d}

    tables_by: dict[str, dict] = {}
    for scn in scns:
        tables = {}
        try:
            for ref in (scn.get("import"), scn.get("export")):
                if ref is None:
                    continue
                for kind in ("standard-unit-rates", "standing-charges"):
                    if kind == "standing-charges" and ref is scn.get("export"):
                        continue
                    tables[rate_key(ref, kind)] = ensure_rates(store, ref, kind, rng_start, rng_end, fetch)
                    yield {"stage": "rates", "scenario": scn["id"]}
        except Exception as err:
            log(f"Simulator: couldn't get rates for {scn['name']} ({err!r}); skipped tonight")
            continue
        tables_by[scn["id"]] = tables
        yield from replay(scn["id"], scn, tables, sig)

    summary = {"windows": {}, "updated": now.isoformat(timespec="seconds"), "all_days": len(use_days),
               "imported_days": len([d for d in use_days if d not in recorded]),
               "new_products": [{"name": x["name"], "supplier": x["supplier"], "code": x["code"]} for x in new]}
    for label, n in (("30", 30), ("year", 365)):
        window = comparison_days(store.results, use_days, n)
        if label == "year" and len(window) < 60:
            continue
        summary["windows"][label] = summarise(store.results, actual, names, window, prefix_skip="hp|")

    # heat pump: your tariff, heat-pump tariffs and the five best others, each with a heat pump added
    hp = ctx.hp
    if hp is not None and hp.enabled and not hp.problems() and ctx.weather is not None:
        try:
            today = now.astimezone(timezone.utc).date()
            first = date.fromisoformat(use_days[0]) - timedelta(days=1)
            ctx.weather.fill(min(first, today - timedelta(days=366)), today, today,
                             ctx.weather_get or weather_get)
            ctx.weather.save()
            yield {"stage": "weather"}
        except Exception as err:
            log(f"Simulator: couldn't get weather ({err!r}); heat pump skipped tonight")
            hp = None
    if hp is not None and hp.enabled and not hp.problems() and ctx.weather is not None:
        hlc = hlc_kw_per_k(hp, ctx.weather.last_year(now.astimezone(timezone.utc).date()))
        if hlc:
            short = summary["windows"].get("30", {}).get("ranking", [])
            picks = ["current"] + [s["id"] for s in scns if "heat pump" in s["notes"]]
            picks += [r["id"] for r in short if r["id"] not in picks][:5]
            hp_sig = sig + _hp_sig(hp) + f"{hlc:.4f}"

            def add_heat(d, slots):
                temps = [ctx.weather.at(sl.start) for sl in slots]
                if any(t is None for t in temps):
                    return False
                space, hw = day_heat([temps[k] for k in range(0, len(temps), 2)], hlc, hp)
                space = space[:len(slots)] + [0.0] * max(0, len(slots) - len(space))
                elec, heat = schedule([sl.price for sl in slots], temps, space, hw, hp)
                for sl, e in zip(slots, elec, strict=True):
                    sl.load_kwh += e
                return {"hp_kwh": round(sum(elec), 3), "heat_kwh": round(heat, 3)}

            for sid in picks:
                scn = next((s for s in scns if s["id"] == sid), None)
                if scn is None or sid not in tables_by:
                    continue
                yield from replay("hp|" + sid, scn, tables_by[sid], hp_sig, add_heat)
            summary["heat_pump"] = heat_pump_summary(store.results, summary, picks, names, hp, hlc)
    store.summary = summary
    store.save()
    long = summary["windows"].get("year")
    basis = long if long and long["days"] >= 90 else summary["windows"].get("30")
    yield {"done": True, "new_products": new, "opportunities": opportunities(basis) if basis else []}


def heat_pump_summary(results: dict, summary: dict, picks: list[str], names: dict, hp: HeatPumpSettings,
                      hlc: float) -> dict:
    """Each tariff with a heat pump against the same tariff without one plus gas, over the longest window."""
    win = summary["windows"].get("year") or summary["windows"].get("30")
    if not win:
        return {}
    days = [d for d in _date_span(date.fromisoformat(win["from"]), date.fromisoformat(win["to"]))
            if d in (results.get("current") or {}).get("days", {})]
    rows = []
    for sid in picks:
        with_hp = (results.get("hp|" + sid) or {}).get("days", {})
        without = (results.get(sid) or {}).get("days", {})
        common = [d for d in days if d in with_hp and d in without]
        if not common:
            continue
        n = len(common)
        heat = sum(with_hp[d].get("heat_kwh", 0) for d in common)
        hp_kwh = sum(with_hp[d].get("hp_kwh", 0) for d in common)
        elec_hp = sum(with_hp[d]["cost"] for d in common)
        elec = sum(without[d]["cost"] for d in common)
        gas = heat / (hp.boiler_efficiency / 100) * hp.gas_price_p / 100 + n * hp.gas_standing_p / 100
        saving = elec + gas - elec_hp
        per_month = MONTH_DAYS / n
        rows.append({"id": sid, "name": names.get(sid, {}).get("name", sid), "days": n,
                     "with_hp_month": round(elec_hp * per_month, 2), "without_month": round(elec * per_month, 2),
                     "gas_month": round(gas * per_month, 2), "saving_month": round(saving * per_month, 2),
                     "hp_kwh": round(hp_kwh, 0), "heat_kwh": round(heat, 0),
                     "scop": round(heat / hp_kwh, 2) if hp_kwh else None,
                     "payback_years": round(hp.install_cost / (saving * 365 / n), 1)
                     if hp.install_cost and saving > 0 and n >= 300 else None})
    rows.sort(key=lambda r: r["with_hp_month"])
    return {"hlc_w_per_k": round(hlc * 1000, 0), "rows": rows, "window_days": win["days"],
            "full_year": win["days"] >= 300}
