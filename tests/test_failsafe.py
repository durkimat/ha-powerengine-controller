"""The inverter's failsafe the engine hands to the integration (pe_core/failsafe.py, powerengine._failsafe_sync)."""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from replay_harness import import_powerengine
from test_adapter_solis import ENTITY, FOR_SLOTS_2_3, make_ha_with_slots

from pe_core import failsafe
from pe_core.adapters.solis import SolisInverter
from pe_core.host_socket import HostError, HostUnavailable
from pe_core.modes import ACTIVE, PASSIVE, PAUSED

SELECT = "select.solis_energy_storage_control_switch"


def role_entity(role):
    return SELECT if role == "storage_mode" else ENTITY.get(role)


def inverter():
    ha = make_ha_with_slots({SELECT: "Self-Use"})
    inv = SolisInverter(ha, role_entity)
    slots, _ = inv.slot_map(datetime(2026, 11, 6, 12, 0))
    return inv, slots


def slot_calls():
    inv, slots = inverter()
    entities = inv.slot_keys(slots)
    return failsafe.calls_for(inv.release_slots(entities, {}), entities, inv), entities


def test_the_slot_failsafe_closes_every_window_sets_self_use_then_presses_the_update_buttons():
    calls, entities = slot_calls()
    steps = [c for c in calls if "service" in c]
    numbers = [c for c in steps if c["service"] == "number/set_value"]
    assert len(numbers) == 24 and all(c["data"]["value"] == 0 for c in numbers)          # 8 values in each of 3 slots
    windows = {e for k, e in entities.items() if "#" in k and "button" not in k}
    assert {c["data"]["entity_id"] for c in numbers} == windows
    assert {"service": "select/select_option", "data": {"entity_id": SELECT, "option": "Self-Use"}} in steps
    buttons = [c["data"]["entity_id"] for c in steps if c["service"] == "button/press"]
    assert sorted(buttons) == sorted([ENTITY["timed_update_button"], *(m["timed_update_button"]
                                                                        for m in FOR_SLOTS_2_3.values())])
    wait = calls.index({"wait_s": failsafe.PAUSE_BEFORE_BUTTONS_S})
    assert all(c["service"] == "button/press" for c in calls[wait + 1:]) and wait > 0       # values first, then buttons
    assert json.loads(json.dumps(calls)) == calls                                          # plain data, nothing else


def test_a_forbidden_entity_is_never_in_the_failsafe():
    inv, slots = inverter()
    entities = dict(inv.slot_keys(slots))
    entities["timed_charge_start_hour#1"] = "number.solis_bump_charge_start"
    calls = failsafe.calls_for(inv.release_slots(entities, {}), entities, inv)
    assert all("bump" not in str(c) for c in calls)


# --- the engine telling the integration -----------------------------------------------------------------

class Native:
    def __init__(self, raises=None):
        self.failsafe_sent, self.sent, self.raises = None, [], raises

    def failsafe(self, calls, armed):
        if self.raises:
            raise self.raises
        self.sent.append((calls, armed))
        self.failsafe_sent = (calls, armed)


def app(monkeypatch, tmp_path, native, mode=ACTIVE, demo=False):
    pe = import_powerengine(monkeypatch)
    inv, slots = inverter()
    ha = tmp_path / "ha"
    (ha / "powerengine").mkdir(parents=True)
    (ha / "packages").mkdir()
    (ha / "packages" / "powerengine_handover.yaml").write_text("# Managed by PowerEngine: x\n")
    s = SimpleNamespace(cfg=object(), _demo=demo, mode=SimpleNamespace(effective=mode), logs=[], calls=[],
                        host=SimpleNamespace(native_entities=native,
                                             call_service=lambda svc, **kw: s.calls.append(svc)),
                        _save_path=lambda: str(ha / "powerengine" / "config.yaml"))
    s.log = lambda msg, level="INFO": s.logs.append((level, msg))
    s._inverter = lambda: inv
    s._slot_map = lambda: slots
    s._slot_keys = lambda m: inv.slot_keys(m)
    s._rc_entities = lambda: {}
    s._ha_config_dir = lambda: pe.PowerEngine._ha_config_dir(s)
    s._failsafe_calls = lambda: pe.PowerEngine._failsafe_calls(s)
    s._retire_package = lambda: pe.PowerEngine._retire_package(s)
    return pe, s, ha


def test_the_engine_hands_over_the_failsafe_armed_while_in_control_and_then_retires_the_old_package(monkeypatch,
                                                                                                    tmp_path):
    native = Native()
    pe, s, ha = app(monkeypatch, tmp_path, native)
    pe.PowerEngine._failsafe_sync(s, force=True)
    (calls, armed), = native.sent
    assert armed is True and calls and any(m == "script/reload" for m in s.calls)
    assert not (ha / "packages" / "powerengine_handover.yaml").exists()                 # the integration holds it now
    pe.PowerEngine._failsafe_sync(s)
    assert len(native.sent) == 1                                                       # unchanged: not sent again
    s.mode.effective = PAUSED
    pe.PowerEngine._failsafe_sync(s, force=True)
    assert native.sent[-1][1] is True                                                  # paused is still in control
    s.mode.effective = PASSIVE
    pe.PowerEngine._failsafe_sync(s, force=True)
    assert native.sent[-1][1] is False and len(native.sent) == 2                       # passive: nothing to guard


def test_until_the_integration_can_take_it_the_old_package_stays_and_the_engine_tries_again_later(monkeypatch,
                                                                                                   tmp_path):
    native = Native(raises=HostError("unknown request 'failsafe'"))
    pe, s, ha = app(monkeypatch, tmp_path, native)
    pe.PowerEngine._failsafe_sync(s, force=True)
    assert (ha / "packages" / "powerengine_handover.yaml").exists() and s.calls == []
    assert sum("cannot hold" in m for _lvl, m in s.logs) == 1
    pe.PowerEngine._failsafe_sync(s)                                                    # not yet due: nothing
    assert native.sent == []
    s._failsafe_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    native.raises = None                                                                # Home Assistant restarted
    pe.PowerEngine._failsafe_sync(s)
    assert len(native.sent) == 1 and not (ha / "packages" / "powerengine_handover.yaml").exists()
    assert sum("cannot hold" in m for _lvl, m in s.logs) == 1                           # said once


def test_a_lost_connection_is_retried_quietly_and_a_demo_or_a_host_without_entities_does_nothing(monkeypatch,
                                                                                                  tmp_path):
    pe, s, ha = app(monkeypatch, tmp_path, Native(raises=HostUnavailable("not connected")))
    pe.PowerEngine._failsafe_sync(s, force=True)
    assert (ha / "packages" / "powerengine_handover.yaml").exists() and s.logs == []          # quiet: it reconnects
    for native, demo in ((Native(), True), (None, False)):
        pe, s, ha = app(monkeypatch, tmp_path / str(demo), native, demo=demo)
        pe.PowerEngine._failsafe_sync(s, force=True)
        assert (ha / "packages" / "powerengine_handover.yaml").exists() and s.logs == []
        if native is not None:
            assert native.sent == []
