"""The comparison's inputs: the snapshot reader, the world's snapshot feed and configurable battery, the replay settings
and the day built from cost records (docs/plans/engine-pages-and-comparison.md, 2.2 and 2.4)."""
import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
import yaml
from compare_support import DAY, TEMPLATE, make_save_dir, records_from_pack, snapshot_from_pack

from pe_core.compare import day as daymod
from pe_core.compare import settings
from pe_core.compare.snapfeed import FED_ROLES, Snapshot, SnapshotFeed, load_snapshot, profile_of, states_at
from pe_core.config import parse_config
from pe_core.demo import pack as packmod
from pe_core.demo.world import CAPACITY_KWH, EFFICIENCY, FLOOR_PCT, IDS, LIMIT_W, DemoWorld

LONDON = ZoneInfo("Europe/London")
PACK = packmod.load_pack()


def at(hh, mm=0, ss=0):
    return datetime(DAY.year, DAY.month, DAY.day, hh, mm, ss, tzinfo=LONDON)


def small_snapshot():
    return {"version": 1, "day": "2026-10-06", "tz": "Europe/London",
            "roles": {"export_rate": "sensor.real_export", "import_rate_now": "sensor.real_now", "x": 3},
            "entries": [
                {"at": "2026-10-06T00:00:12+01:00",
                 "states": {"sensor.real_export": {"state": "0.15", "attributes": {"a": 1}},
                            "sensor.real_now": {"state": "0.07", "attributes": {}}},
                 "profile": {"days": 14.0, "watts": {"0|0": 400.0, "1|0": 500.0}},
                 "first_seen": {"2026-10-06T01:30:00+01:00": "2026-10-05T19:02:00+01:00"}},
                {"at": "2026-10-06T00:30:00+01:00",
                 "states": {"sensor.real_now": {"state": "0.30", "attributes": {}}}},
                {"at": "2026-10-06T06:00:00+01:00",
                 "states": {"sensor.real_export": {"state": "0.12", "attributes": {}}},
                 "profile": {"days": 15.0, "watts": {"0|0": 410.0}},
                 "first_seen": {"2026-10-06T07:00:00+01:00": "2026-10-06T05:00:00+01:00"}},
            ]}


# --- the snapshot reader ----------------------------------------------------------------------------------------

def test_states_are_reconstructed_from_deltas_at_a_time():
    snap = Snapshot(small_snapshot())
    assert snap.states_at(at(0, 0, 5)) == {}                                         # before the first entry
    s = snap.states_at(at(0, 20))
    assert s["sensor.real_now"]["state"] == "0.07" and s["sensor.real_export"]["attributes"] == {"a": 1}
    s = snap.states_at(at(1))
    assert s["sensor.real_now"]["state"] == "0.30" and s["sensor.real_export"]["state"] == "0.15"   # carried on
    assert snap.states_at(at(6, 0))["sensor.real_export"]["state"] == "0.12"         # an entry at the instant counts
    assert states_at(small_snapshot(), at(1))["sensor.real_now"]["state"] == "0.30"  # the raw dict works too


def test_profile_and_first_seen_are_the_latest_that_changed():
    snap = Snapshot(small_snapshot())
    p = snap.profile_at(at(1))
    assert p.days == 14.0 and p.watts == {(False, 0): 400.0, (True, 0): 500.0}
    assert snap.profile_at(at(1)) is snap.profile_at(at(2))                          # one object until it changes
    assert snap.profile_at(at(7)).days == 15.0 and snap.profile_at(at(7)).watts == {(False, 0): 410.0}
    assert snap.profile_at(at(0, 0, 1)) is None
    assert snap.first_seen_at(at(1)) == {"2026-10-06T01:30:00+01:00": "2026-10-05T19:02:00+01:00"}
    assert len(snap.first_seen_at(at(7))) == 2                                       # merged, not replaced


def test_profile_keys_are_weekend_flag_and_half_hour():
    p = profile_of({"days": 3, "watts": {"0|5": 100, "1|47": 200, "bad": 1, "1|x": 2}})
    assert p.watts == {(False, 5): 100.0, (True, 47): 200.0} and p.days == 3.0


def test_a_snapshot_is_usable_only_with_an_entry_by_half_past_midnight_and_a_profile():
    snap = Snapshot(small_snapshot())
    assert snap.refusal(at(0)) is None
    late = small_snapshot()
    late["entries"] = late["entries"][2:]
    assert "no forecast record" in Snapshot(late).refusal(at(0))
    no_profile = small_snapshot()
    del no_profile["entries"][0]["profile"]
    assert "profile" in Snapshot(no_profile).refusal(at(0))


def test_load_snapshot_reads_the_days_file_or_nothing(tmp_path):
    assert load_snapshot(str(tmp_path), DAY) is None
    d = tmp_path / "snapshots"
    d.mkdir()
    (d / "2026-10-06.json").write_text(json.dumps(small_snapshot()))
    assert load_snapshot(str(tmp_path), DAY)["day"] == "2026-10-06"
    (d / "2026-10-06.json").write_text("{not json")
    assert load_snapshot(str(tmp_path), DAY) is None


# --- the world: a configurable battery, a snapshot feed ---------------------------------------------------------

def world(**kw):
    t = [at(2).astimezone(timezone.utc)]
    return DemoWorld(PACK, "dull", LONDON, lambda: t[0], **kw), t


def test_the_worlds_defaults_are_the_demos():
    w, _ = world()
    assert (w.capacity_kwh, w.efficiency, w.charge_limit_w, w.discharge_limit_w, w.floor_pct, w.feed) == \
        (CAPACITY_KWH, EFFICIENCY, LIMIT_W, LIMIT_W, FLOOR_PCT, None)


def test_the_battery_comes_from_the_arguments():
    w, t = world(capacity_kwh=9.0, efficiency=0.9, charge_limit_w=2000, discharge_limit_w=3000, floor_pct=20)
    w.energy = 0.5 * 9.0
    assert w.soc == pytest.approx(50)
    assert w.call_service("number/set_value", entity_id=IDS["rc_charge_power"], value=5000)
    assert w.call_service("select/select_option", entity_id=IDS["rc_mode"], option="Force charge")
    w.step(t[0] + timedelta(seconds=60))
    assert w.flows["charge_w"] == 2000                                       # the charge limit, not the command
    added = w.energy - 4.5
    assert added == pytest.approx(2000 / 1000 / 60 * 0.9, rel=0.05)         # 60 s at 2 kW, 90 % in
    assert w.call_service("select/select_option", entity_id=IDS["rc_mode"], option="Force discharge")
    assert w.call_service("number/set_value", entity_id=IDS["rc_discharge_power"], value=5000)
    t[0] = t[0] + timedelta(seconds=60)
    w.step(t[0] + timedelta(seconds=60))
    assert w.flows["discharge_w"] == 3000
    w.energy = 0.0
    w.step(t[0] + timedelta(seconds=300))
    assert w.flows["discharge_w"] == 0                                       # nothing above the floor (20% of 9 kWh)
    assert w.min_soc == 20


def test_the_feed_replaces_the_fed_roles_and_nothing_else():
    class Feed:
        def get(self, role, when):
            return ("0.99", {"x": 1}) if role == "export_rate" else None
    w, _ = world(feed=Feed())
    assert w.get_state(IDS["export_rate"]) == "0.99" and w.get_state(IDS["export_rate"], attribute="x") == 1
    plain, _ = world()
    assert w.get_state(IDS["import_rate_now"]) == plain.get_state(IDS["import_rate_now"])
    assert w.get_state(IDS["battery_soc"]) == plain.get_state(IDS["battery_soc"])


def test_a_snapshot_feed_serves_the_snapshots_states_at_world_time():
    snap = Snapshot(snapshot_from_pack(PACK, "dull", DAY))
    feed = SnapshotFeed(snap)
    assert set(feed.entity) == set(FED_ROLES)
    t = [at(0).astimezone(timezone.utc)]
    w = DemoWorld(PACK, "dull", LONDON, lambda: t[0], feed=feed)
    state, attrs = w.get_state(IDS["solar_forecast_today"]), w.get_state(IDS["solar_forecast_today"], attribute="all")
    plain = DemoWorld(PACK, "dull", LONDON, lambda: t[0])
    assert attrs["attributes"]["detailedForecast"] == plain.get_state(IDS["solar_forecast_today"],
                                                                      attribute="detailedForecast")
    assert float(state) == pytest.approx(float(plain.get_state(IDS["solar_forecast_today"])), abs=0.01)
    # a later half-hour: the rate the snapshot knew then
    t[0] = at(12, 40).astimezone(timezone.utc)
    w.step(t[0])
    plain.step(t[0])
    assert float(w.get_state(IDS["import_rate_now"])) == pytest.approx(float(plain.get_state(IDS["import_rate_now"])))
    # a feed that differs from the world's own wins
    snap.data["entries"].append({"at": at(12, 35).isoformat(), "states": {
        snap.roles["import_rate_now"]: {"state": "0.5555", "attributes": {}}}})
    w2 = DemoWorld(PACK, "dull", LONDON, lambda: t[0], feed=SnapshotFeed(Snapshot(snap.data)))
    assert w2.get_state(IDS["import_rate_now"]) == "0.5555"


def test_before_the_first_entry_the_world_serves_its_own():
    snap = Snapshot(snapshot_from_pack(PACK, "dull", DAY))
    t = [(at(0) - timedelta(hours=1)).astimezone(timezone.utc)]
    w = DemoWorld(PACK, "dull", LONDON, lambda: t[0], feed=SnapshotFeed(snap))
    assert w.get_state(IDS["export_rate"]) is not None


# --- the settings a replay runs with ----------------------------------------------------------------------------

def owner_config():
    cfg = yaml.safe_load(TEMPLATE.read_text())
    cfg["inputs"] = {"battery_soc": {"entity": "sensor.solis_real_soc", "invert": False},
                     "grid_power": {"entity": "sensor.solis_grid", "invert": True},
                     "battery_capacity": {"value": 14.3}, "battery_round_trip": {"value": 88.0},
                     "battery_max_charge_power": {"value": 3500}, "battery_max_discharge_power": {"value": 4200}}
    cfg["site"] = {"inverter": "solis", "tariff": "auto"}
    cfg["features"] = {**cfg["features"], "arbitrage": False, "smart_charge_optimisation": True,
                       "tariff_simulator": True, "engine_compare": True}
    cfg["safety"] = {**cfg["safety"], "min_reserve_soc": 20, "cheap_threshold_p": 9.5}
    cfg["system"] = {**cfg["system"], "control_method": "timed", "engine": "v2", "house_load_includes_ev": False}
    cfg["operation"] = {"mode": "passive"}
    cfg["engine_v2"] = {"sample_s": 15, "preview_when_v1": True}
    cfg["solar_plants"] = [{"id": "main", "name": "Roof", "power": {"entity": "sensor.real_pv"},
                            "energy_today": {"entity": "sensor.real_pv_e"}, "forecast": "solcast_site"},
                           {"id": "garage", "name": "Garage", "power": {"entity": "sensor.g"},
                            "energy_today": {"entity": "sensor.ge"}}]
    return cfg


def test_the_replay_settings_keep_the_owners_and_point_the_inputs_at_the_world():
    template = yaml.safe_load(TEMPLATE.read_text())
    out = {e: yaml.safe_load(settings.replay_config(owner_config(), template, e)) for e in ("v1", "v2")}
    for cfg in out.values():
        parse_config(cfg)                                                            # still a valid config
        assert cfg["inputs"]["battery_soc"] == {"entity": "sensor.demo_inverter_battery_soc"}   # the world's entity
        assert cfg["inputs"]["grid_power"] == {"entity": "sensor.demo_inverter_meter_power"}    # no invert
        assert cfg["inputs"]["battery_capacity"] == {"value": 14.3}                  # the owner's fixed figures
        assert cfg["inputs"]["battery_round_trip"] == {"value": 88.0}
        assert cfg["safety"]["min_reserve_soc"] == 20 and cfg["safety"]["cheap_threshold_p"] == 9.5
        assert cfg["features"]["arbitrage"] is False                                 # the owner's features stay...
        for off in ("smart_charge_optimisation", "tariff_simulator", "engine_compare"):
            assert cfg["features"][off] is False                                     # ...but not the internet ones
        assert cfg["operation"]["mode"] == "active" and cfg["system"]["control_method"] == "ram_remote"
        assert cfg["system"]["publisher"] == "direct" and cfg["system"]["house_load_includes_ev"] is True
        assert "site" not in cfg and [p["id"] for p in cfg["solar_plants"]] == ["main"]
        assert cfg["solar_plants"][0]["power"] == {"entity": "sensor.demo_solar_power"}
        assert cfg["engine_v2"]["sample_s"] == 15
    assert out["v1"]["system"]["engine"] == "v1" and out["v2"]["system"]["engine"] == "v2"
    assert out["v1"]["engine_v2"]["preview_when_v1"] is False                       # v1 doesn't also run the preview
    assert out["v2"]["engine_v2"]["preview_when_v1"] is True


def test_the_demo_template_comes_through_as_it_is_and_gives_the_demo_battery():
    template = yaml.safe_load(TEMPLATE.read_text())
    text = settings.replay_config(None, template, "v1")
    cfg = yaml.safe_load(text)
    assert cfg["inputs"] == template["inputs"] and cfg["safety"] == template["safety"]
    b = settings.battery_of(text)
    assert b["capacity_kwh"] == CAPACITY_KWH and b["efficiency"] == pytest.approx(EFFICIENCY, abs=1e-4)
    assert b["charge_limit_w"] == b["discharge_limit_w"] == LIMIT_W and b["floor_pct"] == FLOOR_PCT


def test_the_battery_is_the_owners():
    template = yaml.safe_load(TEMPLATE.read_text())
    b = settings.battery_of(settings.replay_config(owner_config(), template, "v2"))
    assert b["capacity_kwh"] == 14.3 and b["efficiency"] == pytest.approx(0.88 ** 0.5, abs=1e-4)
    assert b["charge_limit_w"] == 3500 and b["discharge_limit_w"] == 4200


# --- the day ------------------------------------------------------------------------------------------------------

def test_day_from_records_matches_the_pack_builders_conversion():
    recs = records_from_pack(PACK, "car", DAY)
    d = packmod.day_from_records(recs, "T", "why", 1.0, LONDON)
    src = PACK["days"]["car"]
    for col in ("house", "car", "solar", "act", "std", "ovn", "exp", "standing", "slot", "axle", "free", "soc"):
        assert d[col] == src[col], col
    assert d["recorded"] == "2026-10-06" and packmod.day_complete(recs, LONDON) is None
    assert packmod.day_complete(recs[:47], LONDON) == "47 records, not 48"


def test_a_missing_level_takes_the_previous_one():
    recs = records_from_pack(PACK, "car", DAY)
    recs[0]["soc_start"] = None
    recs[5]["soc_start"] = None
    d = packmod.day_from_records(recs, "", "", 1.0, LONDON)
    assert d["soc"][0] == 50.0 and d["soc"][5] == d["soc"][4]


def test_engine_of_the_day_and_old_records():
    recs = records_from_pack(PACK, "car", DAY)
    assert daymod.day_engine(recs) == ("v1", False)                      # no tag: v1, and not known to be live
    assert daymod.day_engine([]) == (None, False)
    for r in recs:
        r["engine"], r["live"] = "v2", False
    assert daymod.day_engine(recs) == ("v2", False)
    for r in recs[:10]:
        r["live"] = True
    assert daymod.day_engine(recs) == ("v2", True)
    for r in recs[10:20]:
        r["live"], r["engine"] = True, "v1"
    assert daymod.day_engine(recs) == ("mixed", True)


def test_the_metered_cost_prices_events_at_their_own_rate():
    recs = records_from_pack(PACK, "axle", DAY)
    plain = sum(r["grid_import"] * r["v"]["act"] - r["grid_export"] * r["v"]["exp"] for r in recs)
    metered = daymod.metered_cost(recs)
    ev = [r for r in recs if r["axle"]]
    assert ev and metered < plain                                                    # the event pay is more than export
    assert metered == pytest.approx(plain - sum(r["v"]["event_gross"] - r["v"]["event_kwh"] * r["v"]["exp"]
                                                for r in ev), abs=1e-6)


def test_load_day_refuses_with_a_reason(tmp_path):
    costs = str(tmp_path / "pe" / "costs")
    make_save_dir(tmp_path / "pe", PACK, "car", snapshot=False)
    with pytest.raises(daymod.Refused) as e:
        daymod.load_day(costs, DAY)
    assert e.value.status == "no_snapshot" and "forecast record" in e.value.reason
    make_save_dir(tmp_path / "pe2", PACK, "car", records=False)
    with pytest.raises(daymod.Refused) as e:
        daymod.load_day(str(tmp_path / "pe2" / "costs"), DAY)
    assert e.value.status == "incomplete" and "no cost records" in e.value.reason
    make_save_dir(tmp_path / "pe3", PACK, "car")
    path = tmp_path / "pe3" / "costs" / "2026-10-06.json"
    path.write_text(json.dumps(json.loads(path.read_text())[:40]))
    with pytest.raises(daymod.Refused) as e:
        daymod.load_day(str(tmp_path / "pe3" / "costs"), DAY)
    assert e.value.status == "incomplete" and "40 records" in e.value.reason


def test_load_day_gives_a_one_day_pack_the_world_can_play(tmp_path):
    make_save_dir(tmp_path / "pe", PACK, "car", engine="v2", live=True)
    di = daymod.load_day(str(tmp_path / "pe" / "costs"), DAY)
    assert (di.in_control, di.live) == ("v2", True) and di.start == at(0)
    assert list(di.pack["days"]) == [daymod.KEY] and di.pack["tz"] == "Europe/London"
    assert DemoWorld(di.pack, daymod.KEY, di.tz, lambda: di.start).soc == pytest.approx(PACK["days"]["car"]["soc"][0])
    assert di.snapshot.profile_at(di.start + timedelta(minutes=5)) is not None
    assert isinstance(date.fromisoformat(di.pack["days"][daymod.KEY]["recorded"]), date)
