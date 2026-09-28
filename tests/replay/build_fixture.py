"""Build a replay fixture from a PowerEngine diagnostics export (and the app's slots.json).

    python tests/replay/build_fixture.py EXPORT.json SLOTS.json START END OUT.json

Everything identifying is replaced: account numbers, meter IDs, serials and site IDs in entity names and attributes
become generic ones. What's kept: the numbers PowerEngine reads (battery, grid, house, car, solar), the tariff's
half-hourly rates, the solar forecast, the smart-charge slots and the inverter's control entities.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone

# identifying parts of entity ids -> generic
SCRUB = [
    (r"21l4582012", "acct"), (r"1012482848197", "import"), (r"1050002916538", "export"),
    (r"00000000_0009_4300_800e_000000000832", "dev"), (r"a_c1544bc6", "acct"), (r"zappi_20250458", "zappi"),
    (r"myenergi_67lr", "myenergi_hub"), (r"0382_d0fa_324a_2586", "site"),
]
KEEP_ATTRS = {"unit_of_measurement", "rates", "detailedForecast", "planned_dispatches", "completed_dispatches",
              "options", "min", "max", "step", "entries", "current_start", "current_end", "next_start", "next_end",
              "raw_time", "is_active"}
# history entity -> the input it stands for (PowerEngine's own published copy where the raw one isn't exported)
HISTORY_FOR = {
    "sensor.solis_meter_active_power": "sensor.solis_meter_active_power",
    "sensor.myenergi_67lr_power_grid": "sensor.myenergi_67lr_power_grid",
    "sensor.solis_battery_power": "sensor.solis_battery_power",
    "sensor.solis_house_load": "sensor.solis_house_load",
    "sensor.myenergi_67lr_power_charging": "sensor.myenergi_67lr_power_charging",
    "sensor.pe_state_battery_soc": "sensor.solis_battery_soc",
    "sensor.pe_state_solar_power": "sensor.solis_pv_total_power",
}


def scrub(text: str) -> str:
    for pat, rep in SCRUB:
        text = re.sub(pat, rep, text)
    return text


def shift_iso(s: str, days: int) -> str:
    return (datetime.fromisoformat(s) + timedelta(days=days)).isoformat()


def main(export_path, slots_path, start, end, out_path):
    d = json.load(open(export_path))
    slots = json.load(open(slots_path))
    t0, t1 = datetime.fromisoformat(start), datetime.fromisoformat(end)
    cfg = d["app"]["config"]
    states = d["states"]

    # entities the config reads, plus the controls it writes and the handover guards
    wanted = set()

    def walk(v):
        if isinstance(v, dict):
            if isinstance(v.get("entity"), str):
                wanted.add(v["entity"])
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(cfg)
    wanted |= {e for e in states if re.match(r"^(number|select|button|sensor)\.solis_.*(timed_|storage_control|"
                                               r"battery_control_override|update_charge|rtc)", e)}
    static = {}
    for e in sorted(wanted):
        if e not in states:
            continue
        a = {k: v for k, v in (states[e].get("attributes") or {}).items() if k in KEEP_ATTRS}
        static[e] = {"state": states[e]["state"], "attributes": a}

    # rates: the export has the current day (the day after `start`); earlier/later days are that day shifted
    rate_ent = cfg["inputs"]["import_rates_today"]["entity"]
    day_rates = static[rate_ent]["attributes"]["rates"]
    rates = []
    for shift in (-2, -1, 0, 1):
        rates += [{**r, "start": shift_iso(r["start"], shift), "end": shift_iso(r["end"], shift)} for r in day_rates]
    rates.sort(key=lambda r: r["start"])

    # solar forecast, likewise shifted so "today" is the replay's day
    solar = []
    for role in ("solar_forecast_today", "solar_forecast_tomorrow", "solar_forecast_day3"):
        solar += static[cfg["inputs"][role]["entity"]]["attributes"].get("detailedForecast", [])
    solar_all = []
    for shift in (-2, -1, 0):
        solar_all += [{**p, "period_start": shift_iso(p["period_start"], shift)} for p in solar]
    uniq = {}
    for p in solar_all:
        uniq.setdefault(p["period_start"], p)
    solar_all = [uniq[k] for k in sorted(uniq)]

    # timelines of the numbers PowerEngine reads
    timeline = {}
    for src, ent in HISTORY_FOR.items():
        rows = d["history"].get(src) or []
        pts = []
        for r in rows:
            t = datetime.fromtimestamp(r["lu"], timezone.utc)
            try:
                v = float(r["s"])
            except (TypeError, ValueError):
                continue
            pts.append((t, v))
        # keep the last value before the window, then everything inside it, thinned to one per 30 s
        before = [p for p in pts if p[0] <= t0][-1:]
        inside, last = [], None
        for t, v in pts:
            if t0 < t < t1 and (last is None or (t - last).total_seconds() >= 30):
                inside.append((t, v))
                last = t
        timeline[ent] = [[t.isoformat(timespec="seconds"), round(v, 1)] for t, v in before + inside]

    # smart-charge slots as EDF listed them (first seen .. ended)
    dispatches = []
    for rec in slots.values():
        seen = datetime.fromisoformat(rec["first_seen"])
        stop = datetime.fromisoformat(rec.get("ended") or rec["end"])
        if stop < t0 - timedelta(hours=1) or seen > t1:
            continue
        dispatches.append({"start": rec["start"], "end": rec["end"], "first_seen": rec["first_seen"],
                           "ended": rec.get("ended"), "kwh": rec.get("planned_kwh")})

    fixture = {"about": "Replay fixture built by build_fixture.py from a diagnostics export; identifiers scrubbed.",
               "tz": "Europe/London", "start": start, "end": end, "config": cfg, "static": static,
               "rates": rates, "solar_forecast": solar_all, "timeline": timeline, "dispatches": dispatches}
    text = scrub(json.dumps(fixture, indent=None, separators=(",", ":"), default=str))
    for leak in ("21l4582012", "1012482848197", "20250458", "67lr", "c1544bc6", "0382_d0fa"):
        assert leak not in text, leak
    open(out_path, "w").write(text)
    print(f"{out_path}: {len(text) // 1024} KB, {sum(len(v) for v in timeline.values())} points, "
          f"{len(dispatches)} dispatches")


if __name__ == "__main__":
    main(*sys.argv[1:6])
