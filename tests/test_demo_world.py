"""The demo world (demo plan, step C2): a simulated home and battery behind the real adapters, and the gate that keeps
the demo away from the real system."""
import ast
import pathlib
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
import yaml

from pe_core.config import parse_config
from pe_core.demo.gate import DemoGate
from pe_core.demo.pack import load_pack
from pe_core.demo.world import CAPACITY_KWH, EFFICIENCY, FLOOR_PCT, IDS, LIMIT_W, DemoWorld
from pe_core.forecast import parse_history
from pe_core.rctest import find_entities
from pe_core.readings import read

TZ = ZoneInfo("Europe/London")
APP = pathlib.Path(__file__).resolve().parents[1] / "apps" / "powerengine"
PACK = load_pack()


def local(day, hh, mm=0):
    return datetime(2026, 10, day, hh, mm, tzinfo=TZ).astimezone(timezone.utc)


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t

    def run(self, world, minutes, resend=None):
        for _ in range(minutes):
            self.t += timedelta(minutes=1)
            if resend:
                resend()
            world.step()


def world_at(day="dull", hh=2, mm=0):
    clock = Clock(local(6, hh, mm))
    return DemoWorld(PACK, day, TZ, clock), clock


def command(world, option, watts=0):
    role = "rc_discharge_power" if option == "Force discharge" else "rc_charge_power"
    assert world.call_service("number/set_value", entity_id=IDS[role], value=watts)
    assert world.call_service("select/select_option", entity_id=IDS["rc_mode"], option=option)


def balance(world):
    f = world.flows
    return f["house_w"] + f["car_w"] - f["solar_w"] + f["charge_w"] - f["discharge_w"] - f["grid_w"]


# --- the entities ---------------------------------------------------------------------------------

def test_the_demo_config_maps_only_demo_entities():
    cfg = yaml.safe_load((APP / "demo" / "config.template").read_text())
    ids = {v["entity"] for v in cfg["inputs"].values() if "entity" in v}
    ids |= {p[k]["entity"] for p in cfg["solar_plants"] for k in ("power", "energy_today")}
    world, _ = world_at()
    assert ids and ids <= set(world.get_state())
    assert cfg["system"]["control_method"] == "ram_remote" and cfg["operation"]["mode"] == "active"
    for off in ("smart_charge_optimisation", "tariff_simulator", "cold_caution", "cold_learning"):
        assert cfg["features"][off] is False
    assert parse_config(cfg).mode == "active"


def test_remote_control_entities_are_found_by_the_real_discovery():
    world, _ = world_at()
    found = find_entities(world.get_state().keys())
    assert found == {"rc_mode": IDS["rc_mode"], "rc_charge_power": IDS["rc_charge_power"],
                     "rc_discharge_power": IDS["rc_discharge_power"]}
    assert world.get_state(IDS["rc_mode"], attribute="options") == ["Off", "Force charge", "Force discharge"]


def test_other_entities_are_not_the_worlds():
    world, _ = world_at()
    assert world.get_state("sensor.solis_battery_soc") is None
    assert world.get_state("zone.home", attribute="latitude", default="x") == "x"
    assert not world.is_demo("switch.pe_ctl_pause")


# --- the battery ------------------------------------------------------------------------------------

def test_force_charge_raises_soc_at_the_commanded_power_with_losses():
    world, clock = world_at("dull", 2)
    world.energy = 0.5 * CAPACITY_KWH
    soc0, before = world.soc, dict(world.counters)
    command(world, "Force charge", 3000)
    clock.run(world, 30, resend=lambda: world.call_service("select/select_option", entity_id=IDS["rc_mode"],
                                                            option="Force charge"))
    assert world.flows["charge_w"] == pytest.approx(3000)
    added_ac = world.counters["charge"] - before["charge"]
    assert added_ac == pytest.approx(1.5, abs=0.02)                                   # 3 kW for half an hour
    assert (world.soc - soc0) / 100 * CAPACITY_KWH == pytest.approx(added_ac * EFFICIENCY, abs=0.02)
    assert abs(balance(world)) < 1e-6 and world.flows["grid_w"] > 3000               # the grid pays for it


def test_force_discharge_lowers_soc_and_never_below_the_floor():
    world, clock = world_at("dull", 2)
    command(world, "Force discharge", 5000)
    seen = []

    def resend():
        world.call_service("select/select_option", entity_id=IDS["rc_mode"], option="Force discharge")
        seen.append(world.flows["discharge_w"])
    clock.run(world, 240, resend)
    assert world.soc == pytest.approx(FLOOR_PCT, abs=0.2)
    assert max(seen) <= LIMIT_W and world.flows["discharge_w"] < 1                       # nothing left to give
    assert world.flows["grid_w"] == pytest.approx(world.flows["house_w"] + world.flows["car_w"]
                                                  - world.flows["solar_w"], abs=1)


def test_force_charge_stops_at_the_ceiling_and_respects_the_power_limit():
    world, clock = world_at("dull", 2)
    command(world, "Force charge", 5000)
    peak = []

    def resend():
        world.call_service("select/select_option", entity_id=IDS["rc_mode"], option="Force charge")
        peak.append(world.flows["charge_w"])
    clock.run(world, 150, resend)
    assert world.soc == pytest.approx(100.0, abs=0.1) and max(peak) <= LIMIT_W
    assert world.flows["charge_w"] < 1


def test_hold_keeps_the_charge_and_self_use_covers_the_house():
    world, clock = world_at("dull", 2)
    command(world, "Force charge", 0)
    soc0 = world.soc
    clock.run(world, 20, lambda: world.call_service("select/select_option", entity_id=IDS["rc_mode"],
                                                     option="Force charge"))
    assert world.soc == pytest.approx(soc0, abs=0.01) and world.flows["discharge_w"] == 0
    assert world.flows["grid_w"] == pytest.approx(world.flows["house_w"], abs=1)      # the house runs on the grid
    world.call_service("select/select_option", entity_id=IDS["rc_mode"], option="Off")
    clock.run(world, 20)
    assert world.soc < soc0
    assert world.flows["grid_w"] == pytest.approx(0, abs=1) and world.flows["discharge_w"] > 0     # battery covers it


def test_self_use_charges_from_surplus_solar():
    world, clock = world_at("sunny", 10)
    world.energy = 0.5 * CAPACITY_KWH
    clock.run(world, 30)
    assert world.flows["solar_w"] > world.flows["house_w"] and world.flows["charge_w"] > 0
    assert world.soc > 50 and balance(world) == pytest.approx(0, abs=1e-6)


def test_the_grid_balance_holds_every_minute_and_counters_add_up():
    world, clock = world_at("car", 0, 10)
    for opt, w in (("Force charge", 5000), ("Force discharge", 2500), ("Force charge", 0), ("Off", 0)):
        command(world, opt, w)
        for _ in range(45):
            clock.run(world, 1, lambda o=opt: world.call_service("select/select_option", entity_id=IDS["rc_mode"],
                                                                 option=o))
            assert abs(balance(world)) < 1e-6
    c = world.counters
    assert c["import"] > 0 and c["charge"] > 0 and c["discharge"] > 0 and c["house"] > 0


def test_failsafe_reverts_to_self_use_unless_the_command_is_refreshed():
    world, clock = world_at("dull", 2)
    command(world, "Force charge", 4000)
    clock.run(world, 4)
    assert world.option == "Force charge"
    world.call_service("select/select_option", entity_id=IDS["rc_mode"], option="Force charge")    # a refresh
    clock.run(world, 4)
    assert world.option == "Force charge"
    clock.run(world, 3)
    assert world.option == "Off" and world.get_state(IDS["rc_mode"]) == "Off" and world.events
    assert world.flows["charge_w"] == 0


def test_a_new_day_starts_at_the_recorded_soc_for_that_time():
    world, _ = world_at("axle", 0, 0)
    assert world.soc == pytest.approx(PACK["days"]["axle"]["soc"][0])
    world, _ = world_at("car", 12, 0)
    assert world.soc == pytest.approx(PACK["days"]["car"]["soc"][24])


# --- what the world refuses ---------------------------------------------------------------------------

def test_only_the_remote_control_and_numbers_are_writable_and_the_rest_is_refused_and_recorded():
    world, _ = world_at()
    refused = [
        ("persistent_notification/create", {"title": "x", "message": "y"}),
        ("logbook/log", {"name": "PowerEngine", "message": "hi"}),
        ("select/select_option", {"entity_id": "select.solis_inverter_battery_control_override", "option": "Off"}),
        ("select/select_option", {"entity_id": IDS["rc_mode"], "option": "Bump"}),
        ("number/set_value", {"entity_id": IDS["rc_charge_power"], "value": 9000}),
        ("number/set_value", {"entity_id": IDS["rc_charge_power"], "value": "lots"}),
        ("button/press", {"entity_id": IDS["rc_mode"]}),
        ("switch/turn_off", {"entity_id": IDS["guard_read_only"]}),
        ("homeassistant/reload_config_entry", {"entity_id": IDS["rc_mode"]}),
    ]
    for service, data in refused:
        assert world.call_service(service, **data) is False
    assert len(world.refused) == len(refused) and world.option == "Off" and world.charge_w == 0
    assert world.call_service("number/set_value", entity_id=IDS["rc_charge_power"], value=1234)
    assert world.get_state(IDS["rc_charge_power"]) == 1234


# --- the real readers accept the world ---------------------------------------------------------------

def _read(world, clock):
    cfg = parse_config(yaml.safe_load((APP / "demo" / "config.template").read_text()))
    r = read(cfg, lambda eid: world.get_state(eid, attribute="all"), clock())
    return cfg, r


def test_the_real_readers_accept_the_worlds_states():
    world, clock = world_at("car", 20, 15)
    cfg, r = _read(world, clock)
    assert r.problems == []
    assert r.battery_soc == pytest.approx(world.soc, abs=0.1)
    assert len(r.rates) == 96 and r.import_rate is not None and r.export_rate == 0.15 and r.standing_charge > 0
    assert r.forecast_today_kwh > 0 and r.forecast_tomorrow_kwh > 0
    assert r.ev_state() == "charging" and r.ev_plug == "Charging" and r.ev_power > 1000
    assert r.house_power == pytest.approx(world.flows["house_w"], abs=1)              # the car is taken out of the load
    assert r.grid_power == pytest.approx(world.flows["grid_w"], abs=1)               # + importing
    assert r.dispatches and all(d.value is not None for d in r.dispatches)
    assert r.solar_power == pytest.approx(world.flows["solar_w"], abs=1)


def test_the_axle_event_reads_as_active_during_it_and_upcoming_before_it():
    world, clock = world_at("axle", 5, 0)
    _, r = _read(world, clock)
    assert not r.axle_active and r.axle_start == local(6, 6, 30) and r.axle_end == local(6, 8, 0)
    world, clock = world_at("axle", 7, 0)
    _, r = _read(world, clock)
    assert r.axle_active and r.axle_end == local(6, 8, 0)


def test_the_rc_power_signs_match_the_apps_convention():
    world, clock = world_at("dull", 2)
    command(world, "Force discharge", 2000)
    clock.run(world, 2, lambda: world.call_service("select/select_option", entity_id=IDS["rc_mode"],
                                                    option="Force discharge"))
    _, r = _read(world, clock)
    assert r.battery_power == pytest.approx(2000, abs=1)                              # + discharging
    assert r.grid_power < 0                                                          # exporting: - out


# --- history -------------------------------------------------------------------------------------------

def test_the_days_loop_backwards_from_the_chosen_one():
    world, _ = world_at("axle")
    today = datetime(2026, 10, 6).date()
    order = world.order
    assert world.name_for(today) == "axle" and world.name_for(today + timedelta(days=1)) == "axle"
    for back in range(1, 9):
        assert world.name_for(today - timedelta(days=back)) == order[(order.index("axle") - back) % 4]


def test_history_gives_fourteen_days_the_load_profile_can_use():
    world, clock = world_at("sunny", 14)
    end = clock()
    rows = world.get_history(entity_id=IDS["house_load_power"], start_time=end - timedelta(days=14), end_time=end,
                             minimal_response=True, no_attributes=True)
    assert len(rows) == 1 and len(rows[0]) >= 14 * 48
    parsed = parse_history(rows, unit="W")
    assert len(parsed) == len(rows[0]) and all(v >= 0 for _, v in parsed)
    assert world.get_history(entity_id="sensor.not_ours", start_time=end - timedelta(days=1), end_time=end) == [[]]
    rates = world.get_history(entity_id=IDS["import_rates_today"], start_time=end - timedelta(days=3), end_time=end)[0]
    assert len(rates) >= 3 and len(rates[0]["attributes"]["rates"]) == 48


# --- the gate ------------------------------------------------------------------------------------------

class Real:
    def __init__(self):
        self.calls = []
        self.states = {"switch.pe_ctl_pause": "off", "sensor.kitchen": "1", "zone.home": {"latitude": 51}}

    def get_state(self, entity_id=None, attribute=None, default=None, **kw):
        self.calls.append(("get_state", entity_id))
        if entity_id is None:
            return {k: {"state": v} for k, v in self.states.items()}
        return self.states.get(entity_id, default)

    def set_state(self, entity_id, **kw):
        self.calls.append(("set_state", entity_id))

    def fire_event(self, event, **kw):
        self.calls.append(("fire_event", event))


def make_gate():
    real, logs = Real(), []
    world, _ = world_at()
    gate = DemoGate(lambda: world, real.get_state, real.set_state, real.fire_event, logs.append,
                    allowed_events=("pe_config_result",))
    return gate, real, world, logs


def test_gate_sends_every_service_call_to_the_world_and_never_to_home_assistant():
    gate, real, world, logs = make_gate()
    gate.call_service("number/set_value", entity_id=IDS["rc_charge_power"], value=2000)
    gate.call_service("persistent_notification/create", title="a", message="b")
    gate.call_service("persistent_notification/create", title="c", message="d")
    gate.call_service("switch/turn_off", entity_id="switch.kitchen_light")
    assert world.charge_w == 2000
    assert [c for c in real.calls if c[0] != "get_state"] == []
    assert len(gate.dropped) == 3
    assert sum("persistent_notification" in m for m in logs) == 1                     # said once, not every time


def test_gate_reads_demo_entities_from_the_world_and_only_pe_entities_from_home_assistant():
    gate, real, world, _ = make_gate()
    assert gate.get_state(IDS["battery_soc"]) == world.get_state(IDS["battery_soc"])
    assert gate.get_state("switch.pe_ctl_pause") == "off"
    assert gate.get_state("sensor.kitchen") is None and gate.get_state("zone.home", attribute="latitude") is None
    assert IDS["battery_soc"] in gate.get_state() and "switch.pe_ctl_pause" in gate.get_state()
    assert "sensor.kitchen" not in gate.get_state()
    assert gate.get_history(entity_id="sensor.kitchen", start_time=None, end_time=None) == [[]]


def test_gate_lets_only_pe_states_and_the_cards_answers_out():
    gate, real, _, _ = make_gate()
    gate.set_state("sensor.pe_diag_version", state="1")
    gate.set_state("light.kitchen", state="on")
    gate.fire_event("pe_config_result", ok=True)
    gate.fire_event("pe_update_installed", running="1", installed="2")
    assert [c for c in real.calls if c[0] != "get_state"] == [("set_state", "sensor.pe_diag_version"),
                                                              ("fire_event", "pe_config_result")]
    assert [d[0] for d in gate.dropped] == ["state write", "event"]


# --- nothing bypasses the gate ---------------------------------------------------------------------------

WRITERS = {"call_service", "fire_event", "set_state", "turn_on", "turn_off", "toggle", "select_option", "set_value",
           "notify", "persistent_notification", "mqtt_publish", "run_command", "remove_entity", "listen_state_write"}
# the only places that may call one, and on what: the app's own methods (which demo mode replaces), the MQTT
# publisher (never built in demo mode) and the gate itself
ALLOWED = {("powerengine.py", "self"), ("pe_core/adapters/publish.py", "self.api"),
           ("pe_core/demo/gate.py", "self._world()")}


def test_no_direct_call_to_home_assistant_bypasses_the_gate():
    """Every call that could change Home Assistant is `self.<method>(...)` in powerengine.py, so demo mode's gate (which
    replaces those methods on the instance) sees it. A call on anything else (an adapter's `ha`, `hass.Hass...`,
    `super()`) fails this test: send it through the app instead."""
    offenders = []
    for path in sorted(APP.rglob("*.py")):
        rel = path.relative_to(APP).as_posix()
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in WRITERS:
                receiver = ast.unparse(node.func.value)
                if (rel, receiver) not in ALLOWED:
                    offenders.append(f"{rel}:{node.lineno} {receiver}.{node.func.attr}(...)")
    assert offenders == [], "\n".join(offenders)


def test_demo_mode_replaces_every_method_the_app_uses_to_reach_home_assistant():
    src = (APP / "powerengine.py").read_text()
    for name in ("get_state", "get_history", "call_service", "set_state", "fire_event"):
        assert f"self.{name}" in src.split("def _demo_setup", 1)[1].split("def _demo_world", 1)[0]
    used = {n.func.attr for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute) and n.func.attr in WRITERS and ast.unparse(n.func.value) == "self"}
    assert used <= {"call_service", "fire_event", "mqtt_publish"} | {"set_state"}, used
