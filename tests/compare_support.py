"""A save folder for the engine comparison: a real-looking day built from a demo pack day (cost records, forecast
snapshot, owner's config), so the runner can be tried without private data."""
import json
import pathlib
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import yaml

from pe_core.compare.snapfeed import FED_ROLES
from pe_core.demo import world as worldmod

ROOT = pathlib.Path(__file__).resolve().parents[1]
LONDON = ZoneInfo("Europe/London")
TEMPLATE = ROOT / "apps" / "powerengine" / "demo" / "config.template"
DAY = date(2026, 10, 6)


def records_from_pack(pack: dict, name: str, day: date, tz=LONDON, engine=None, live=None) -> list[dict]:
    """Cost records of the pack day played on `day` (what the app would have recorded)."""
    d = pack["days"][name]
    rec = d["as_recorded"]
    out = []
    for i in range(48):
        start = datetime.combine(day, time(0), tzinfo=tz) + timedelta(minutes=30 * i)
        r = {"start": start.astimezone(timezone.utc).isoformat(), "house": d["house"][i], "car": d["car"][i],
             "solar": d["solar"][i], "grid_import": rec["grid_import"][i], "grid_export": rec["grid_export"][i],
             "battery_in": rec["battery_in"][i], "battery_out": rec["battery_out"][i], "soc_start": d["soc"][i],
             "soc_end": d["soc"][min(i + 1, 47)], "seconds": 1800.0, "axle": bool(d["axle"][i]),
             "free": bool(d["free"][i]), "import_rate": d["act"][i], "export_rate": d["exp"][i],
             "standing": d["standing"][i],
             "v": {"act": d["act"][i], "std": d["std"][i], "ovn": d["ovn"][i], "exp": d["exp"][i],
                   "slot": bool(d["slot"][i]), "event": "axle" if d["axle"][i] else None,
                   "event_kwh": rec["grid_export"][i] if d["axle"][i] else 0.0,
                   "event_gross": rec["grid_export"][i] * (1.0 + d["exp"][i]) if d["axle"][i] else 0.0}}
        if engine:
            r["engine"] = engine
        if live is not None:
            r["live"] = live
        out.append(r)
    return out


def snapshot_from_pack(pack: dict, name: str, day: date, profile: dict | None = None, tz=LONDON,
                       entity=lambda role: f"sensor.real_{role}") -> dict:
    """A snapshot (design 2.2) of the fed roles, taken from a demo world at each half-hour of the day and written as
    deltas: an entry only where something changed. `profile` is the house profile ({"days", "watts"}); default: the
    pack day's own house load."""
    now = [datetime.combine(day, time(0), tzinfo=tz).astimezone(timezone.utc)]
    world = worldmod.DemoWorld(pack, name, tz, lambda: now[0])
    roles = {role: entity(role) for role in FED_ROLES}
    entries, last = [], {}
    for i in range(48):
        t = datetime.combine(day, time(0), tzinfo=tz) + timedelta(minutes=30 * i)
        now[0] = t.astimezone(timezone.utc)
        world.t = now[0]
        world._flows_at(world.t, 0.0)
        states = {}
        for role in FED_ROLES:
            state, attrs = world._state(worldmod.IDS[role])
            cur = {"state": str(state), "attributes": json.loads(json.dumps(attrs, default=str))}
            if last.get(roles[role]) != cur:
                last[roles[role]] = cur
                states[roles[role]] = cur
        entry = {"at": t.isoformat(), "states": states}
        if i == 0:
            entry["profile"] = profile or {"days": 14.0, "watts": {
                f"{we}|{hh}": pack["days"][name]["house"][hh] * 2000.0 for we in (0, 1) for hh in range(48)}}
            entry["first_seen"] = {}
        if states or i == 0:
            entries.append(entry)
    return {"version": 1, "day": day.isoformat(), "tz": str(tz.key), "roles": roles, "entries": entries}


def make_save_dir(folder: pathlib.Path, pack: dict, name: str, day: date = DAY, *, snapshot=True, records=True,
                  owner_config: dict | None = None, profile=None, engine=None, live=None) -> pathlib.Path:
    """<folder>/config.yaml (the demo template as the owner's unless given), costs/<day>.json, costs/snapshots/."""
    folder.mkdir(parents=True, exist_ok=True)
    cfg = owner_config if owner_config is not None else yaml.safe_load(TEMPLATE.read_text())
    (folder / "config.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    costs = folder / "costs"
    (costs / "snapshots").mkdir(parents=True, exist_ok=True)
    if records:
        (costs / f"{day.isoformat()}.json").write_text(json.dumps(records_from_pack(pack, name, day, engine=engine,
                                                                                    live=live)))
    if snapshot:
        (costs / "snapshots" / f"{day.isoformat()}.json").write_text(
            json.dumps(snapshot_from_pack(pack, name, day, profile)))
    return folder
