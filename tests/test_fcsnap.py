"""Forecast snapshots (pe_core/fcsnap.py): deltas, the 5-minute limit, the midnight full entry, reconstruction at a
time, the size bound with realistic payloads, and the pure helpers the comparison runner uses."""
import json
import os
import random
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from pe_core import fcsnap
from pe_core.forecast import LoadProfile

LON = ZoneInfo("Europe/London")


def t(h, m=0, s=0, day=7):
    return datetime(2026, 10, day, h, m, s, tzinfo=LON)


def st(state, **attrs):
    return {"state": state, "attributes": attrs}


def read(path):
    return json.load(open(path, encoding="utf-8"))


def test_snapshot_roles_only_mapped_entities():
    cfg = SimpleNamespace(inputs={"solar_forecast_today": {"entity": "sensor.solcast_today"},
                                  "import_rates_today": {"entity": "event.rates"},
                                  "export_rate": {"value": 0.15},                 # a fixed value: no entity
                                  "battery_soc": {"entity": "sensor.soc"}})       # not future information
    assert fcsnap.snapshot_roles(cfg) == {"solar_forecast_today": "sensor.solcast_today",
                                          "import_rates_today": "event.rates"}
    assert fcsnap.snapshot_roles(None) == {}


def test_first_cycle_is_full_then_only_changes(tmp_path):
    w = fcsnap.SnapshotWriter(str(tmp_path), LON)
    roles = {"a": "sensor.a", "b": "sensor.b"}
    assert w.observe(t(0, 0, 12), roles, {"sensor.a": st("1", x=1), "sensor.b": st("2")})
    assert not w.observe(t(0, 0, 42), roles, {"sensor.a": st("1", x=1), "sensor.b": st("2")})      # nothing changed
    assert w.observe(t(0, 6), roles, {"sensor.a": st("1", x=2), "sensor.b": st("2")})
    data = read(tmp_path / "2026-10-07.json")
    assert data["version"] == 1 and data["day"] == "2026-10-07" and data["tz"] == "Europe/London"
    assert data["roles"] == roles
    assert len(data["entries"]) == 2
    assert set(data["entries"][0]["states"]) == {"sensor.a", "sensor.b"}
    assert data["entries"][0]["at"] == "2026-10-07T00:00:12+01:00"
    assert data["entries"][1]["states"] == {"sensor.a": st("1", x=2)}                            # a delta only


def test_an_entity_is_written_at_most_once_in_five_minutes(tmp_path):
    w = fcsnap.SnapshotWriter(str(tmp_path), LON)
    roles = {"a": "sensor.a"}
    wrote = []
    for minute in range(0, 31):                                       # changes every minute for half an hour
        wrote.append(w.observe(t(1, minute), roles, {"sensor.a": st(str(minute))}))
    entries = read(tmp_path / "2026-10-07.json")["entries"]
    assert [e["at"][11:16] for e in entries] == ["01:00", "01:05", "01:10", "01:15", "01:20", "01:25", "01:30"]
    # the value that was held back is written once the gap has passed, not lost
    assert entries[-1]["states"]["sensor.a"]["state"] == "30"


def test_midnight_writes_everything_again(tmp_path):
    w = fcsnap.SnapshotWriter(str(tmp_path), LON)
    roles = {"a": "sensor.a", "b": "sensor.b"}
    same = {"sensor.a": st("1"), "sensor.b": st("2")}
    w.observe(t(23, 58, day=7), roles, same)
    assert not w.observe(t(23, 59, day=7), roles, same)
    assert w.observe(t(0, 0, 20, day=8), roles, same)                 # unchanged, but a new local day
    new = read(tmp_path / "2026-10-08.json")
    assert set(new["entries"][0]["states"]) == {"sensor.a", "sensor.b"}
    assert new["day"] == "2026-10-08"


def test_a_restart_carries_on_without_a_duplicate_full_entry(tmp_path):
    roles = {"a": "sensor.a"}
    w = fcsnap.SnapshotWriter(str(tmp_path), LON)
    prof = LoadProfile({(False, 0): 400.0}, 14.0)
    w.observe(t(0, 1), roles, {"sensor.a": st("1")}, prof, {"2026-10-07T01:30:00+01:00": "2026-10-06T19:00:00+01:00"})
    w2 = fcsnap.SnapshotWriter(str(tmp_path), LON)                    # AppDaemon restarted at 10:00
    assert not w2.observe(t(10, 0), roles, {"sensor.a": st("1")}, prof,
                          {"2026-10-07T01:30:00+01:00": "2026-10-06T19:00:00+01:00"})
    assert w2.observe(t(10, 1), roles, {"sensor.a": st("2")}, prof)
    assert len(read(tmp_path / "2026-10-07.json")["entries"]) == 2


def test_reconstruction_of_every_role_at_a_time(tmp_path):
    w = fcsnap.SnapshotWriter(str(tmp_path), LON)
    roles = {"solar_forecast_today": "sensor.solcast", "import_rates_today": "event.rates",
             "free_power_next_start": "sensor.free"}
    w.observe(t(0, 0), roles, {"sensor.solcast": st("40.0", detailedForecast=[1]), "event.rates": st("a", rates=[1]),
                               "sensor.free": st("unknown")})
    w.observe(t(6, 0), roles, {"sensor.solcast": st("42.0", detailedForecast=[2]), "event.rates": st("a", rates=[1]),
                               "sensor.free": st("unknown")})
    w.observe(t(16, 0), roles, {"sensor.solcast": st("42.0", detailedForecast=[2]),
                                "event.rates": st("b", rates=[1, 2]),
                                "sensor.free": st("2026-10-07T18:00:00+01:00")})
    snap = read(tmp_path / "2026-10-07.json")
    assert fcsnap.states_at(snap, t(0, 30)) == {"sensor.solcast": st("40.0", detailedForecast=[1]),
                                                "event.rates": st("a", rates=[1]), "sensor.free": st("unknown")}
    mid = fcsnap.states_at(snap, t(12, 0).isoformat())                  # ISO text works too
    assert mid["sensor.solcast"] == st("42.0", detailedForecast=[2]) and mid["event.rates"]["state"] == "a"
    late = fcsnap.states_at(snap, t(23, 0).astimezone(timezone.utc))      # another zone: same instant logic
    assert late["event.rates"] == st("b", rates=[1, 2]) and late["sensor.free"]["state"].startswith("2026-10-07T18")
    assert fcsnap.states_at(snap, t(0, 0) - timedelta(seconds=1)) == {}   # before the first entry: nothing known
    assert set(late) == {"sensor.solcast", "event.rates", "sensor.free"}


def test_profile_and_first_seen_at_a_time(tmp_path):
    w = fcsnap.SnapshotWriter(str(tmp_path), LON)
    roles = {"a": "sensor.a"}
    p1, p2 = LoadProfile({(False, 0): 400.0, (True, 47): 650.58}, 14.0), LoadProfile({(False, 0): 410.0}, 15.0)
    w.observe(t(0, 0), roles, {"sensor.a": st("1")}, p1, {})
    assert "profile" in read(tmp_path / "2026-10-07.json")["entries"][0]
    assert "first_seen" not in read(tmp_path / "2026-10-07.json")["entries"][0]       # empty and nothing before
    w.observe(t(3, 0), roles, {"sensor.a": st("1")}, p1, {"2026-10-07T04:30:00+00:00": "2026-10-07T03:00:00+01:00"})
    w.observe(t(5, 0), roles, {"sensor.a": st("1")}, p2,
              {"2026-10-07T04:30:00+00:00": "2026-10-07T03:00:00+01:00",
               "2026-10-07T17:00:00+00:00": "2026-10-07T05:00:00+01:00"})
    assert not w.observe(t(5, 1), roles, {"sensor.a": st("1")}, p2,
                         {"2026-10-07T04:30:00+00:00": "2026-10-07T03:00:00+01:00",
                          "2026-10-07T17:00:00+00:00": "2026-10-07T05:00:00+01:00"})
    snap = read(tmp_path / "2026-10-07.json")
    assert fcsnap.profile_at(snap, t(1, 0)) == {"days": 14.0, "watts": {"0|0": 400.0, "1|47": 650.6}}
    assert fcsnap.profile_at(snap, t(6, 0))["days"] == 15.0
    assert fcsnap.profile_at(snap, t(0, 0) - timedelta(minutes=1)) is None
    lp = fcsnap.to_load_profile(fcsnap.profile_at(snap, t(1, 0)))
    assert lp.watts == {(False, 0): 400.0, (True, 47): 650.6} and lp.days == 14.0
    assert fcsnap.first_seen_at(snap, t(1, 0)) == {}
    assert fcsnap.first_seen_at(snap, t(4, 0)) == {"2026-10-07T04:30:00+00:00": "2026-10-07T03:00:00+01:00"}
    assert len(fcsnap.first_seen_at(snap, t(23, 0))) == 2
    # a slot announced after `when` is not known yet, even in a later entry's map
    assert fcsnap.first_seen_at(snap, t(5, 0, 0))["2026-10-07T17:00:00+00:00"]
    assert "2026-10-07T17:00:00+00:00" not in fcsnap.first_seen_at(snap, t(4, 59))


def test_slot_first_seen_keeps_only_slots_near_the_day():
    slots = {"2026-10-07T01:30:00+01:00": {"first_seen": "2026-10-06T19:02:00+01:00"},
             "2026-10-01T01:30:00+01:00": {"first_seen": "2026-09-30T19:02:00+01:00"},
             "2026-10-20T01:30:00+01:00": {"first_seen": "2026-10-19T19:02:00+01:00"},
             "2026-10-07T02:00:00+01:00": {}}
    assert fcsnap.slot_first_seen(slots, date(2026, 10, 7), LON) == {
        "2026-10-07T01:30:00+01:00": "2026-10-06T19:02:00+01:00"}


def _solcast(day, scale=1.0):
    items = []
    for i in range(48):
        s = datetime(2026, 10, day, i // 2, 30 * (i % 2), tzinfo=timezone.utc)
        kw = max(0.0, 3.5 * scale * (1 - abs(i - 24) / 14)) if 10 <= i <= 38 else 0.0
        items.append({"period_start": s.isoformat(), "pv_estimate": round(kw, 4),
                      "pv_estimate10": round(kw * 0.6, 4), "pv_estimate90": round(kw * 1.3, 4)})
    hourly = [{"period_start": it["period_start"], "pv_estimate": it["pv_estimate"] * 2} for it in items[::2]]
    return {"detailedForecast": items, "detailedHourly": hourly, "estimate": 21.4, "estimate10": 12.1,
            "estimate90": 27.9, "friendly_name": "Solcast PV Forecast Forecast Today", "unit_of_measurement": "kWh",
            "attribution": "Data retrieved from Solcast"}


def _rates(day):
    out = []
    for i in range(48):
        s = datetime(2026, 10, day, i // 2, 30 * (i % 2), tzinfo=timezone.utc)
        out.append({"start": s.isoformat(), "end": (s + timedelta(minutes=30)).isoformat(),
                    "value_inc_vat": 0.0699 if i < 10 else 0.3028, "is_capped": False,
                    "is_intelligent_adjusted": False})
    return {"rates": out, "start": out[0]["start"], "end": out[-1]["end"], "event_types": ["rates_changed"],
            "friendly_name": "Electricity Current Day Rates"}


def test_a_day_with_realistic_payloads_and_24_forecast_updates_stays_under_1mb(tmp_path):
    """Three Solcast entities (each rewritten 24 times a day as the forecast is revised), two Kraken rate events, the
    dispatch list, free power and grid events: the day's file stays under 1 MB (the plan's bound)."""
    rng = random.Random(7)
    w = fcsnap.SnapshotWriter(str(tmp_path), LON)
    roles = {"solar_forecast_today": "sensor.solcast_today", "solar_forecast_tomorrow": "sensor.solcast_tomorrow",
             "solar_forecast_day3": "sensor.solcast_day3", "import_rates_today": "event.rates_today",
             "import_rates_tomorrow": "event.rates_tomorrow", "import_rate_now": "sensor.rate_now",
             "export_rate": "sensor.export", "standing_charge": "sensor.standing",
             "smart_dispatches": "binary.dispatch",
             "free_power_next_start": "sensor.free_start", "axle_event_start": "sensor.axle_start"}
    prof = LoadProfile({(we, hh): 300.0 + hh * 7.5 for we in (False, True) for hh in range(48)}, 21.0)
    wrote, cur = 0, {}
    for step in range(24 * 12 + 1):                                   # every 5 minutes, all day
        now = t(0, 0) + timedelta(minutes=5 * step) if step < 288 else t(23, 59)
        hour = now.hour
        if not cur or now.minute == 10:                               # a forecast update every hour (24 a day)
            scale = 0.7 + 0.01 * hour + 0.02 * rng.random()
            cur = {}
            for k, day in (("today", 7), ("tomorrow", 8), ("day3", 9)):
                a = _solcast(day, scale)
                a["estimate"] = round(20 + hour * 0.1 + rng.random(), 2)
                a["detailedForecast"][12]["pv_estimate"] = round(rng.random() * 3, 4)
                cur[f"sensor.solcast_{k}"] = st("21.4", **a)
        states = {
            **cur,
            "event.rates_today": st("2026-10-07T00:00:00+00:00", **_rates(7)),
            "event.rates_tomorrow": st("2026-10-07T15:01:00+00:00", **_rates(8)) if hour >= 16 else st("unknown"),
            "sensor.rate_now": st("0.0699" if now.hour < 5 else "0.3028", unit_of_measurement="GBP/kWh"),
            "sensor.export": st("0.15"), "sensor.standing": st("0.4597"),
            "binary.dispatch": st("on" if hour in (2, 3) else "off", planned_dispatches=[
                {"start": "2026-10-07T01:30:00+01:00", "end": "2026-10-07T03:30:00+01:00", "charge_in_kwh": -4.2}] * 3,
                completed_dispatches=[]),
            "sensor.free_start": st("unknown"), "sensor.axle_start": st("unknown"),
        }
        wrote += bool(w.observe(now, roles, states, prof, {}))
    path = tmp_path / "2026-10-07.json"
    size = os.path.getsize(path)
    assert size < 1_000_000, size
    assert w.skipped == 0                                              # nothing had to be left out
    snap = read(path)
    assert len(snap["entries"]) >= 24                                  # the updates are all there
    got = fcsnap.states_at(snap, t(23, 59))
    assert got["event.rates_tomorrow"]["state"] == "2026-10-07T15:01:00+00:00"
    assert fcsnap.states_at(snap, t(0, 30))["event.rates_tomorrow"]["state"] == "unknown"
    assert set(got) == set(roles.values())


def test_past_the_size_limits_the_writer_slows_then_stops(tmp_path, monkeypatch):
    monkeypatch.setattr(fcsnap, "SOFT_BYTES", 2000)
    monkeypatch.setattr(fcsnap, "HARD_BYTES", 6000)
    w = fcsnap.SnapshotWriter(str(tmp_path), LON)
    roles = {"a": "sensor.a"}
    big = "x" * 1500
    results = [w.observe(t(1, m), roles, {"sensor.a": st(str(m), blob=big)}) for m in range(0, 60, 5)]
    entries = read(tmp_path / "2026-10-07.json")["entries"]
    assert results[0] and len(entries) < 12
    assert w.skipped > 0 or len(entries) < 12                        # slowed to 30 minutes, then stopped
    assert os.path.getsize(tmp_path / "2026-10-07.json") < 6000 + 2500


def test_a_failed_write_raises_and_leaves_the_file_whole(tmp_path, monkeypatch):
    w = fcsnap.SnapshotWriter(str(tmp_path), LON)
    roles = {"a": "sensor.a"}
    w.observe(t(1, 0), roles, {"sensor.a": st("1")})
    before = (tmp_path / "2026-10-07.json").read_text()

    def boom(path, data):
        raise OSError("disk full")
    monkeypatch.setattr(fcsnap, "_write_json", boom)
    try:
        w.observe(t(1, 10), roles, {"sensor.a": st("2")})
    except OSError:
        pass
    else:
        raise AssertionError("expected an OSError")
    monkeypatch.undo()
    assert (tmp_path / "2026-10-07.json").read_text() == before
    assert w.observe(t(1, 11), roles, {"sensor.a": st("2")})          # tries again, and the entry list is intact
    assert len(read(tmp_path / "2026-10-07.json")["entries"]) == 2
    assert not list(tmp_path.glob("*.tmp"))


def test_prune_keeps_fourteen_days_and_summary_counts(tmp_path):
    w = fcsnap.SnapshotWriter(str(tmp_path), LON)
    for d in range(1, 8):
        (tmp_path / f"2026-09-{d:02d}.json").write_text(json.dumps({"entries": [1, 2]}))
    (tmp_path / "2026-10-06.json").write_text(json.dumps({"entries": [1]}))
    assert w.prune(date(2026, 10, 7)) == 7                           # 2026-09-23 and older would go; all of Sept 1-7
    assert sorted(os.listdir(tmp_path)) == ["2026-10-06.json"]
    s = fcsnap.summary(str(tmp_path))
    assert s["days_kept"] == 1 and s["entries"] == 1 and s["bytes"] > 0
    assert fcsnap.summary(str(tmp_path / "nope")) == {"days_kept": 0, "entries": 0, "bytes": 0, "days": []}
