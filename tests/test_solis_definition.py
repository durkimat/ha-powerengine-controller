"""Step 7: Solis as a definition file. The parity test freezes what the hand-written SolisInverter produced (recorded
from it, before the refactor, into tests/golden/solis_parity.json) for every protocol method, across a spread of
inputs, and demands the definition-driven class produce exactly the same. To re-record (only ever for a deliberate
change of behaviour): PE_PARITY_RECORD=1 pytest tests/test_solis_definition.py."""

from __future__ import annotations

import dataclasses
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from pe_core.adapters import InverterAdapter, get
from pe_core.adapters.solis import SlotContext, SolisInverter
from pe_core.control import Write
from pe_core.decide import EXPORT, FORCE_DISCHARGE, GRID_CHARGE, HOLD, SELF_USE, Decision
from pe_core.schedule import Period

GOLDEN = Path(__file__).parent / "golden" / "solis_parity.json"
TZ = ZoneInfo("Europe/London")
NOW = datetime(2026, 9, 26, 23, 10, tzinfo=TZ)
UTC_NOW = datetime(2026, 9, 29, 22, 40, tzinfo=timezone.utc)

# his entities (SolaX Modbus names on the S5-EH1P6K-L)
MAP = {
    "timed_charge_start_hour": "number.solis_timed_charge_start_hours",
    "timed_charge_start_minute": "number.solis_timed_charge_start_minutes",
    "timed_charge_end_hour": "number.solis_timed_charge_end_hours",
    "timed_charge_end_minute": "number.solis_timed_charge_end_minutes",
    "timed_discharge_start_hour": "number.solis_timed_discharge_start_hours",
    "timed_discharge_start_minute": "number.solis_timed_discharge_start_minutes",
    "timed_discharge_end_hour": "number.solis_timed_discharge_end_hours",
    "timed_discharge_end_minute": "number.solis_timed_discharge_end_minutes",
    "timed_update_button": "button.solis_update_charge_discharge_times",
    "timed_charge_current": "number.solis_timed_charge_current",
    "timed_discharge_current": "number.solis_timed_discharge_current",
    "storage_mode": "select.solis_energy_storage_control_switch",
    "inverter_clock": "sensor.solis_rtc",
    "inverter_clock_sync": "button.solis_sync_rtc",
}
FIRST = ("timed_charge_start_hour", "timed_charge_start_minute", "timed_charge_end_hour", "timed_charge_end_minute",
         "timed_discharge_start_hour", "timed_discharge_start_minute", "timed_discharge_end_hour",
         "timed_discharge_end_minute", "timed_update_button")
RC_IDS = ["select.solis_inverter_battery_control_override",
          "number.solis_inverter_battery_control_override_charge_power",
          "number.solis_inverter_battery_control_override_discharge_power"]


class FakeHA:
    def __init__(self, states=None, attrs=None, boom=False):
        self.states, self.attrs, self.boom = dict(states or {}), attrs or {}, boom
        self.calls = []

    def get_state(self, entity_id=None, attribute=None):
        if entity_id is None:
            if self.boom:
                raise RuntimeError("no states")
            return dict(self.states)
        if attribute == "all":
            return self.attrs.get(entity_id)
        if attribute:
            return (self.attrs.get(entity_id) or {}).get(attribute)
        return self.states.get(entity_id)

    def call_service(self, service, **data):
        self.calls.append((service, data))


def slot_states(extra=None, with_23=True):
    states = {eid: "0" for role, eid in MAP.items() if role in FIRST}
    if with_23:
        for n in (2, 3):
            states.update({f"{MAP[r]}_{n}": "0" for r in FIRST})
    states.update(extra or {})
    return states


def norm(x):
    """Anything the adapter returns, as JSON-safe plain data."""
    if dataclasses.is_dataclass(x) and not isinstance(x, type):
        if type(x).__name__ == "Command":
            return {"Command": [x.option, x.watts, x.text(), norm(x.writes()), x.power_role]}
        return {type(x).__name__: {f.name: norm(getattr(x, f.name)) for f in dataclasses.fields(x)}}
    if isinstance(x, dict):
        return {str(k): norm(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [norm(v) for v in x]
    if isinstance(x, (set, frozenset)):
        return sorted(norm(v) for v in x)
    if isinstance(x, datetime):
        return x.isoformat()
    return x


def D(action, power=None):
    return Decision(action, "plan", "x", power_w=power)


def per(kind, h0, m0, h1, m1, first, power=None, day=26):
    return Period(kind, datetime(2026, 9, day, h0, m0, tzinfo=TZ), datetime(2026, 9, day, h1, m1, tzinfo=TZ), first,
                  power)


def collect(make) -> dict:
    """Every protocol method, over a spread of inputs. `make(ha, role_entity, **kw)` builds the adapter."""
    out: dict = {}
    full = lambda role: MAP.get(role)                                         # noqa: E731
    no_button = lambda role: None if role == "timed_update_button" else MAP.get(role)    # noqa: E731
    nothing = lambda role: None                                              # noqa: E731

    # --- slot_map / slot_keys / read
    inv = make(FakeHA(slot_states()), full)
    m, warn = inv.slot_map(NOW)
    out["slot_map_found"] = [m, warn]
    out["slot_map_found_again"] = list(inv.slot_map(NOW + timedelta(seconds=5)))
    out["slot_keys"] = inv.slot_keys(m)
    inv = make(FakeHA(slot_states(with_23=False)), full)
    out["slot_map_missing_23"] = [list(inv.slot_map(NOW + timedelta(seconds=s))) for s in (0, 1, 599, 601)]
    ha = FakeHA(slot_states(with_23=False))
    inv = make(ha, full)
    inv.slot_map(NOW)
    ha.states.update(slot_states())                                           # they appear later
    out["slot_map_appears"] = list(inv.slot_map(NOW + timedelta(seconds=601)))
    out["slot_map_no_entities"] = list(make(FakeHA(), nothing).slot_map(NOW))
    out["slot_map_no_button"] = list(make(FakeHA(slot_states()), no_button).slot_map(NOW))
    ha = FakeHA(slot_states())
    inv = make(ha, full)
    inv.slot_map(NOW)
    out["slot_map_cache_key_change"] = list(make(ha, lambda r: MAP.get(r) + "x" if r == FIRST[0] else MAP.get(r))
                                            .slot_map(NOW))
    out["read"] = make(FakeHA({"number.a": "5", "number.b": 7, "c": "unavailable"}), nothing).read(
        {"a": "number.a", "b": "number.b", "timed_update_button#1": "button.x", "c": "c", "missing": None,
         "update_button_x": "number.a", "nope": "number.zz"})

    # --- RAM remote control
    inv = make(FakeHA({e: "Off" for e in RC_IDS}), full)
    out["rc_entities"] = inv.rc_entities(UTC_NOW)
    both = RC_IDS + ["select.other_battery_control_override", "number.a_battery_control_override_charge_power",
                     "select.zz_battery_control_override", "number.solis2_battery_control_override_discharge_power"]
    out["rc_entities_prefers_solis"] = make(FakeHA({e: 1 for e in both}), full).rc_entities(UTC_NOW)
    out["rc_entities_no_solis"] = make(FakeHA({e: 1 for e in ("select.b_battery_control_override",
                                                              "select.a_battery_control_override")}),
                                       full).rc_entities(UTC_NOW)
    out["rc_entities_partial"] = make(FakeHA({RC_IDS[0]: "Off", "sensor.battery_control_override": 1}),
                                      full).rc_entities(UTC_NOW)
    out["rc_entities_error"] = make(FakeHA(boom=True), full).rc_entities(UTC_NOW)
    ha = FakeHA({RC_IDS[0]: "Off"})                                          # partial: looked up again after 10 min
    inv = make(ha, full)
    first = inv.rc_entities(UTC_NOW)
    ha.states.update({e: 1 for e in RC_IDS})
    out["rc_entities_cache"] = [first, inv.rc_entities(UTC_NOW + timedelta(minutes=1)),
                                inv.rc_entities(UTC_NOW + timedelta(minutes=10, seconds=1))]
    ha = FakeHA({e: 1 for e in RC_IDS})
    inv = make(ha, full)
    inv.rc_entities(UTC_NOW)
    ha.states.clear()
    out["rc_entities_complete_is_kept"] = inv.rc_entities(UTC_NOW + timedelta(minutes=5))
    inv = make(FakeHA(), full)
    rcs = [{}, {"rc_mode": "a"}, {"rc_mode": "a", "rc_charge_power": "b"}, dict(zip(
        ("rc_mode", "rc_charge_power", "rc_discharge_power"), "abc", strict=True)),
        {"rc_mode": "", "rc_charge_power": None, "rc_discharge_power": "c"}]
    out["rc_missing"] = [inv.rc_missing(r) for r in rcs]
    cmds = []
    for action in (GRID_CHARGE, HOLD, FORCE_DISCHARGE, EXPORT, SELF_USE, "none"):
        for power in (None, 0, 1234, 3000, 9000):
            for limits, cap in (((6000, 6000), None), ((4800, 5200), 5000), ((4800, 4800), 3000), ((6000, 6000), 0)):
                cmds.append([action, power, limits, cap, inv.ram_command(action, power, *limits, cap)])
    out["ram_command"] = cmds
    out["ram_off_command"] = inv.ram_off_command()
    svc = []
    for w in (Write("rc_mode", "Force charge", "select"), Write("rc_mode", "Off", "select"),
              Write("rc_charge_power", 3000, "number"), Write("rc_discharge_power", 0, "number"),
              Write("storage_mode", "Self-Use", "select"), Write("timed_charge_start_hour#2", 23, "number"),
              Write("timed_update_button#1", None, "button"), {"role": "rc_mode", "value": "Force discharge",
                                                               "kind": "select"},
              {"role": "x", "value": 5, "kind": "number"}):
        svc.append(inv.service_for(w))
    out["service_for"] = svc

    # --- supervised tests
    out["test_roles"] = inv.test_roles()
    invw = make(FakeHA(), full, volts=52.0)
    tw = []
    for action in ("hold", "charge", "discharge", "self_use"):
        for power in (None, 2000, 4800):
            for minutes in (1, 5, 10):
                for when in (NOW, datetime(2026, 9, 26, 23, 55, tzinfo=TZ)):
                    req = {"action": action, "minutes": minutes, "power_w": power}
                    tw.append([req, when, invw.test_window(req, when, 6000, 5000)])
    out["test_window"] = tw
    tw = []
    for volts in (48.0, 52.0, 57.6):
        tw.append(make(FakeHA(), full, volts=volts).test_window(
            {"action": "charge", "minutes": 5, "power_w": None}, NOW, 6000, 6000))
    out["test_window_volts"] = tw
    probs = []
    for rc in ({}, {"rc_mode": RC_IDS[0]}, {"rc_mode": RC_IDS[0], "rc_charge_power": RC_IDS[1]},
               {"rc_mode": RC_IDS[0], "rc_charge_power": RC_IDS[1], "rc_discharge_power": RC_IDS[2]}):
        for action in ("rc_charge", "rc_discharge", "rc_hold", "rc_failsafe"):
            for options in (None, ["Off", "Force charge", "Force discharge"], ["Off", "Force charge"], ["Off"], "x"):
                st = {RC_IDS[0]: "Off"}
                attrs = {RC_IDS[0]: {"options": options}} if options is not None else {}
                probs.append([rc, action, options, make(FakeHA(st, attrs), full).rc_test_problem(rc, action)])
    out["rc_test_problem"] = probs
    out["rc_test_writes"] = [[t, p, inv.rc_test_writes(t, p)] for t in ("rc_charge", "rc_discharge", "rc_hold",
                                                                          "rc_failsafe") for p in (0, 2000, 4800.5)]

    # --- clock
    out["clock_entities"] = [inv.clock_entities(), make(FakeHA(), nothing).clock_entities()]
    clocks = []
    for state, read_at, synced in (
            ("2026-09-29 23:40:40", "2026-09-29T22:40:30+00:00", "2026-09-28T03:00:00+00:00"),
            ("2026-09-29 23:41:55", "2026-09-29T22:40:30+00:00", "2026-09-29T20:00:00+00:00"),
            ("2026-09-29 23:41:55", "2026-09-29T22:40:30+00:00", "2026-09-20T20:00:00+00:00"),
            ("2026-09-29 23:40:32", "2026-09-29T22:40:30+00:00", "unknown"),
            ("unavailable", "2026-09-29T22:40:30+00:00", "2026-09-29T20:00:00+00:00"),
            ("2026-09-29T23:40:40+01:00", "2026-09-29T22:40:30+00:00", "2026-09-29T20:00:00+00:00")):
        ha = FakeHA({MAP["inverter_clock"]: state, MAP["inverter_clock_sync"]: synced},
                    {MAP["inverter_clock"]: {"state": state, "last_updated": read_at}})
        clocks.append([state, synced, make(ha, full).clock_status(UTC_NOW, TZ)])
    out["clock_status"] = clocks
    out["clock_status_nothing"] = make(FakeHA(), nothing).clock_status(UTC_NOW, TZ)
    out["clock_status_no_tz"] = make(FakeHA({MAP["inverter_clock"]: "2026-09-29 23:40:40"},
                                            {MAP["inverter_clock"]: {"state": "2026-09-29 23:40:40",
                                                                     "last_changed": "2026-09-29T22:40:30+00:00"}}),
                                     full).clock_status(UTC_NOW, None)
    out["clock_sync_write"] = inv.clock_sync_write()

    # --- three-slot strategy
    ha0 = {f"{r}#{n}": "0" for r in FIRST[:8] for n in (1, 2, 3)} | {"timed_charge_current": "0",
                                                                        "timed_discharge_current": "0",
                                                                        "storage_mode": "Self-Use"}
    have_some = dict(ha0, **{"timed_charge_start_hour#1": "23", "timed_charge_start_minute#1": "9",
                            "timed_charge_end_hour#1": "23", "timed_charge_end_minute#1": "45",
                            "timed_charge_current": "90", "storage_mode": "Self-Use - No Export"})
    sw = []
    cases = [
        ([per("charge", 23, 9, 23, 45, GRID_CHARGE)], D(GRID_CHARGE, 3000)),
        ([per("charge", 23, 9, 23, 45, HOLD)], D(HOLD)),
        ([per("charge", 23, 9, 23, 45, GRID_CHARGE), per("charge", 23, 50, 23, 59, HOLD)], D(GRID_CHARGE)),
        ([per("discharge", 23, 9, 23, 50, EXPORT, 2500)], D(EXPORT, 2500)),
        ([per("charge", 23, 9, 23, 30, GRID_CHARGE), per("discharge", 23, 30, 23, 59, EXPORT)], D(GRID_CHARGE, None)),
        ([per("charge", 23, 30, 23, 59, GRID_CHARGE)], D(SELF_USE)),
        ([], D(SELF_USE)),
        ([per("charge", 18, 0, 18, 30, GRID_CHARGE), per("charge", 19, 0, 19, 30, GRID_CHARGE),
          per("charge", 20, 0, 20, 30, GRID_CHARGE), per("charge", 21, 0, 21, 30, GRID_CHARGE)], D(SELF_USE)),
    ]
    for pers, dec in cases:
        for have in (ha0, have_some, {}):
            for limits in ((6000, 6000), (4800, 3000)):
                inv_s = make(FakeHA(), full, volts=52.0)
                sw.append([dec.action, dec.power_w, limits, have is ha0,
                           inv_s.slot_writes(pers, have, NOW, dec.action, dec.power_w, *limits)])
    out["slot_writes"] = sw
    out["writes_for"] = [
        make(FakeHA(), full).writes_for(dec, NOW, SlotContext(pers, have_some, NOW, 6000, 6000))
        for pers, dec in cases[:4]]
    entities = {k: f"e.{k}" for k in ha0 if k not in ("storage_mode",)} | {
        "storage_mode": "select.x", "timed_update_button#1": "button.u1", "timed_update_button#2": "button.u2"}
    out["release_slots"] = [inv.release_slots(entities, h) for h in (ha0, have_some, {})]
    out["release_slots_small"] = inv.release_slots({"timed_charge_start_hour#1": "e1", "timed_charge_current": "e2",
                                                    "storage_mode": "e3", "timed_update_button#1": "e4"},
                                                   {"timed_charge_start_hour#1": 23, "timed_charge_current": 90,
                                                    "storage_mode": "Self-Use - No Export"})

    # --- rolling single window
    rl = []
    for action in (GRID_CHARGE, HOLD, FORCE_DISCHARGE, EXPORT, SELF_USE, "none"):
        for strategy in ("rolling", "block"):
            for cur_end in (None, NOW + timedelta(minutes=3), NOW + timedelta(minutes=20)):
                for block_end in (None, NOW + timedelta(hours=2), NOW - timedelta(minutes=5)):
                    rl.append([action, strategy, cur_end, block_end, make(FakeHA(), full, volts=52.0).rolling(
                        D(action, 3100 if action != SELF_USE else None), NOW, cur_end, block_end, strategy,
                        6000, 5000)])
    out["rolling"] = rl
    late = datetime(2026, 9, 26, 23, 50, tzinfo=TZ)
    out["rolling_late"] = [inv.rolling(D(GRID_CHARGE), late, None, None, "rolling", 6000, 6000),
                           inv.rolling(D(GRID_CHARGE), late, None, late + timedelta(hours=3), "block", 6000, 6000)]
    out["release_rolling"] = inv.release_rolling()
    out["release"] = inv.release()

    # --- confirming writes
    cf = []
    for state in ("5", "5.0", 5, 5.0, None, "unavailable", "Self-Use", "50"):
        for value in (5, "5", 5.0, "Self-Use", None):
            cf.append([state, value, make(FakeHA({"e": state}), nothing).confirmed("e", value)])
    out["confirmed"] = cf
    ha = FakeHA({"number.a": "5.0", "number.b": "7", "select.c": "Self-Use", "number.d": "3"})
    wr = [Write("a", 5, "number"), Write("b", 8, "number"), Write("c", "Self-Use", "select"),
          Write("c", "Backup", "select"), Write("btn", None, "button"), Write("gone", 1, "number"),
          {"role": "d", "value": 3, "kind": "number"}, {"role": "a", "value": 6, "kind": "number"}]
    ents = {"a": "number.a", "b": "number.b", "c": "select.c", "d": "number.d", "btn": "button.x", "gone": None}
    out["verify_entities"] = inv.verify(wr, ha, ents)
    inv2 = make(ha, lambda role: {"a": "number.a", "b": "number.b", "c": "select.c", "d": "number.d"}.get(role))
    out["verify_role_entity"] = inv2.verify(wr, ha)
    out["verify_none"] = inv.verify([], ha)
    roles = ["timed_charge_start_hour#2", "timed_charge_end_minute", "timed_discharge_start_minute#3",
             "timed_charge_current", "timed_discharge_current", "timed_update_button#1", "timed_update_button",
             "storage_mode", "rc_mode", "rc_charge_power", "rc_discharge_power", "inverter_clock_sync", "",
             "x_start_hour",
             "TIMED_CHARGE_START_HOUR", "button.solis_update_charge_discharge_times",
             "number.solis_timed_charge_start_hours_2", "number.solis_timed_charge_end_minutes",
             "select.solis_inverter_battery_control_override", "number.solis_timed_charge_current"]
    out["write_storage"] = [[r, inv.write_storage(Write(r, 1, "number"))] for r in roles] + [
        ["dict", inv.write_storage({"role": "timed_charge_end_hour", "value": 1, "kind": "number"})]]

    # --- identity, capabilities
    out["display_names"] = inv.display_names()
    out["display_names_is_a_copy"] = [inv.display_names() is inv.display_names()]
    out["class_display_names"] = dict(type(inv).DISPLAY_NAMES)
    out["card_model"] = inv.card_model
    out["name"] = inv.name
    out["capabilities"] = inv.capabilities()
    out["capabilities_params"] = [make(FakeHA(), nothing, max_charge_w=4800, max_discharge_w=3700,
                                       ram_max_w=3500).capabilities(),
                                  make(FakeHA(), nothing, ram_max_w=10000).capabilities()]
    out["ram_method"] = [inv.capabilities().method("ram_remote"), inv.capabilities().method("timed_windows"),
                         inv.capabilities().method("cloud")]
    out["is_inverter_adapter"] = isinstance(inv, InverterAdapter)
    out["registry_name"] = get("inverter", "solis") is SolisInverter
    return out


def test_solis_inverter_matches_the_recorded_outputs():
    got = norm(collect(SolisInverter))
    got = json.loads(json.dumps(got, sort_keys=False))
    if os.environ.get("PE_PARITY_RECORD"):
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(got, indent=1) + "\n")
        pytest.skip(f"recorded {len(got)} groups to {GOLDEN.name}")
    want = json.loads(GOLDEN.read_text())
    assert list(got) == list(want)
    for key in want:
        assert got[key] == want[key], key


def test_the_recording_covers_every_protocol_method():
    want = json.loads(GOLDEN.read_text())
    needed = ["slot_map", "slot_keys", "read", "rc_entities", "rc_missing", "ram_command", "ram_off_command",
              "service_for", "test_roles", "test_window", "rc_test_problem", "rc_test_writes", "clock_entities",
              "clock_status", "clock_sync_write", "slot_writes", "writes_for", "release_slots", "release_rolling",
              "release", "rolling", "confirmed", "verify", "write_storage", "display_names", "card_model",
              "capabilities"]
    for n in needed:
        assert any(k.startswith(n) for k in want), n
    assert sum(len(v) if isinstance(v, list) else 1 for v in want.values()) > 500
