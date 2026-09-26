"""The Simulator's overnight job: refresh tariffs, fetch any missing rates, replay new days, rank.

Everything lives in one folder (next to config.yaml): catalogue.json (products and tariff codes), rates/ (one
cached file per tariff and kind, extended as needed) and results.json (per scenario, per day). `run()` is a
generator that yields after each small unit of work, so the app can spread a run over many short callbacks.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone

from . import kraken
from .simulator import (
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


def run(store: SimStore, days: list[str], load_day, params, tz, now: datetime, charger_kw: float,
        fetch=None, log=print):
    """Generator: one step per fetch or simulated day. Yields progress dicts; the last one has "done": True."""
    fetch = fetch or kraken.fetch_json
    new = refresh_catalogue(store, now, fetch, log)
    yield {"stage": "catalogue", "new_products": len(new)}
    scns = scenarios(store.catalogue["products"])
    names = {s["id"]: {"name": s["name"], "notes": s["notes"]} for s in scns}
    days = [d for d in days if complete(load_day(d))]
    if not days:
        store.summary = {"days": 0, "ranking": [], "actual_total": None, "note": "no complete recorded days yet"}
        yield {"done": True, "new_products": new, "opportunities": []}
        return
    rng_start = datetime.combine(date.fromisoformat(days[0]), datetime.min.time(), tzinfo=tz)
    rng_end = datetime.combine(date.fromisoformat(days[-1]) + timedelta(days=2), datetime.min.time(), tzinfo=tz)
    p = sim_params(params)
    sig = signature(p)
    cache: dict[str, list] = {}

    def recs(d):
        if d not in cache:
            cache[d] = load_day(d)
        return cache[d]

    actual = {d: actual_cost(recs(d)) for d in days}
    steps = 0
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
        res = store.results.get(scn["id"])
        if not res or res.get("sig") != sig:
            res = store.results[scn["id"]] = {"sig": sig, "days": {}}
        per_day = res["days"]
        fn = price_fn(scn, tables, tz)
        shift = scn.get("import") is not None
        # recompute from the first day that is missing or was run without a look-ahead day now available
        first = next((i for i, d in enumerate(days)
                      if d not in per_day or (not per_day[d].get("lookahead") and i + 1 < len(days))), None)
        if first is None:
            continue
        soc = per_day[days[first - 1]]["end_soc"] if first > 0 and days[first - 1] in per_day else \
            (recs(days[first])[0].get("soc_start") or 50.0)
        prod = next((x for x in store.catalogue["products"]
                     if scn.get("import") and x["code"] == scn["import"]["product"]), None)
        for i in range(first, len(days)):
            d = days[i]
            slots, est = day_slots(recs(d), fn, shift, charger_kw)
            nxt = []
            if i + 1 < len(days):
                nxt, _ = day_slots(recs(days[i + 1]), fn, shift, charger_kw)
            day_start = datetime.combine(date.fromisoformat(d), datetime.min.time(), tzinfo=tz)
            standing = standing_for(scn, tables, day_start.astimezone(timezone.utc), recs(d),
                                    prod.get("standing_p") if prod else None)
            if any(s.price is None for s in slots):
                break                                           # no price for this day (e.g. not yet published)
            r = run_day(slots, nxt, soc, p, standing)
            r["estimated"], r["lookahead"] = est, bool(nxt)
            per_day[d] = r
            soc = r["end_soc"]
            steps += 1
            if steps % SAVE_EVERY == 0:
                store.save()
            yield {"stage": "day", "scenario": scn["id"], "day": d}
    window = comparison_days(store.results, days, WINDOW_DAYS)
    store.summary = summarise(store.results, actual, names, window)
    store.summary["all_days"] = len(days)
    store.summary["updated"] = now.isoformat(timespec="seconds")
    store.summary["new_products"] = [{"name": x["name"], "supplier": x["supplier"], "code": x["code"]} for x in new]
    store.save()
    yield {"done": True, "new_products": new, "opportunities": opportunities(store.summary)}
