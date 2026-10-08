"""Engine v2: the facade (docs/plans/engine-v2.md). Layers 2 and 3 are stubbed; one smoke test uses the real ones."""

import json
from datetime import timedelta, timezone

import pytest
from test_engine_v2_observe import T0, forecast, inp, segment, value_result

from pe_core import decide as v1d
from pe_core.engine_v2 import engine as engine_mod
from pe_core.engine_v2.engine import EngineV2
from pe_core.engine_v2.settings import V2Settings
from pe_core.engine_v2.types import CHARGE, EVENT, HOLD, SELF_USE, Lines, TimelineItem
from pe_core.override import Override
from pe_core.parsing import Window


class StubForecast:
    def __init__(self):
        self.calls, self.fail, self.learned = 0, False, None

    def build(self, inp_, settings, learned=None):
        self.calls += 1
        self.learned = learned
        if self.fail:
            raise ValueError("the forecast broke")
        return forecast(inp_.now, [segment(inp_.now + timedelta(minutes=30 * i)) for i in range(-1, 12)])


class StubValue:
    def __init__(self):
        self.value_p, self.target, self.floor = 30.0, 88.0, None
        self.solves, self.fail_solve, self.fail_lines, self.because = 0, False, False, []

    def solve(self, fc, start_soc, facts, settings, limits_for, now, because, tz=None, running=None):
        self.solves += 1
        self.because.append(because)
        self.running = getattr(self, "running", []) + [running]
        if self.fail_solve:
            raise RuntimeError("the solver broke")
        for seg in fc.segments:
            limits_for(seg)
        item = TimelineItem(CHARGE, now, now + timedelta(hours=1), start_soc, 88.0, "until 88%", "a reason")
        return value_result(now, fc, timeline=[item], because=because, calc_s=0.5)

    def lines(self, vr, t, soc, import_p, export_p, facts, settings):
        if self.fail_lines:
            raise RuntimeError("the lines broke")
        e = facts.eta_charge
        return Lines(value_p=self.value_p, buy_line_p=import_p / e, sell_line_p=export_p * facts.eta_discharge,
                     use_line_p=import_p * facts.eta_discharge, store_sun_line_p=export_p / e, import_p=import_p,
                     export_p=export_p, charge_target_soc=self.target if self.value_p > import_p / e else None,
                     sell_floor_soc=self.floor)


@pytest.fixture
def stubs(monkeypatch):
    f, v = StubForecast(), StubValue()
    monkeypatch.setattr(engine_mod, "forecast", f)
    monkeypatch.setattr(engine_mod, "value", v)
    return f, v


def tick(eng, sec, **kw):
    kw.setdefault("import_rate", 0.07)                       # cheap: with the stub's value of 30p it charges
    return eng.step(inp(T0 + timedelta(seconds=sec), **kw))


def run(eng, start, seconds, step=10, **kw):
    return [tick(eng, s, **kw) for s in range(start, start + seconds + 1, step)]


# ---- the first tick and the start --------------------------------------------------------------
def test_the_first_tick_works_the_values_out_and_chooses_a_mode(stubs):
    f, v = stubs
    eng = EngineV2(V2Settings())
    out = tick(eng, 0, battery_soc=50.0)
    assert out.revalued and f.calls == 1 and v.solves == 1 and out.value is not None
    assert v.because == ["The engine started"]
    assert out.mode_changed and out.mode.mode == CHARGE and out.decision.action == v1d.GRID_CHARGE
    assert out.decision.rule == "v2_value" and out.decision.target_soc == 88.0
    kinds = [r["kind"] for r in out.journal]
    assert kinds == ["revalue", "mode"]
    mode_row = out.journal[1]
    assert mode_row["event"] == "start" and mode_row["to"] == CHARGE and mode_row["import_p"] == 7.0
    assert mode_row["value_p"] == 30.0 and "buy_line_p" in mode_row and mode_row["level"] == 50.0
    assert "start" in [e.kind for e in out.events]


def test_without_a_value_result_yet_it_is_self_use_and_starting(stubs):
    f, v = stubs
    f.fail = True
    eng = EngineV2(V2Settings())
    out = tick(eng, 0)
    assert not out.revalued and out.value is None
    assert out.decision.rule == "v2_starting" and out.decision.action == v1d.SELF_USE
    assert [r["kind"] for r in out.journal if r["kind"] == "error"] == ["error"]
    assert out.journal[0]["where"] == "revalue"


def test_a_failed_first_revalue_is_retried_after_a_minute_not_every_tick(stubs):
    f, v = stubs
    f.fail = True
    eng = EngineV2(V2Settings())
    run(eng, 0, 50)
    assert f.calls == 1
    f.fail = False
    out = tick(eng, 60)
    assert out.revalued and f.calls == 2 and v.because[-1] == "Trying again after a problem"
    assert out.value is not None


def test_decisions_change_only_on_events_not_on_a_clock(stubs):
    f, v = stubs
    eng = EngineV2(V2Settings())
    outs = run(eng, 0, 1800)
    assert f.calls == 1
    assert sum(o.mode_changed for o in outs) == 1 and not any(o.revalued for o in outs[1:])


# ---- revalue triggers --------------------------------------------------------------------------
def test_new_prices_revalue_once_after_the_batch_window(stubs):
    f, v = stubs
    eng = EngineV2(V2Settings())
    tick(eng, 0)
    new = [Window(T0 - timedelta(hours=12) + timedelta(minutes=30 * i), T0 - timedelta(hours=12)
                  + timedelta(minutes=30 * (i + 1)), 0.25) for i in range(96)]
    outs = run(eng, 10, 120, rates=new, import_rate=0.25)
    assert sum(o.revalued for o in outs) == 1 and v.solves == 2
    assert v.because[-1] == "New prices published"


def test_a_settings_change_revalues_and_reaches_every_layer(stubs):
    f, v = stubs
    eng = EngineV2(V2Settings())
    tick(eng, 0)
    new = V2Settings(price_band_p=1.5, reserve_soc=30.0)
    eng.update_settings(new)
    out = tick(eng, 10)
    assert out.revalued and v.because[-1] == "The settings were saved"
    assert eng.s.price_band_p == 1.5 and eng.executor.s.reserve_soc == 30.0 and eng.observer.s.price_band_p == 1.5
    assert eng.learner.s.price_band_p == 1.5 and eng.triggers.s.price_band_p == 1.5


def test_the_backstop_revalues_after_the_maximum_age(stubs):
    f, v = stubs
    v.value_p = 3.0                                          # self-use: no charge with a deadline in the way
    eng = EngineV2(V2Settings(max_value_age_min=120))
    tick(eng, 0)
    out = tick(eng, 119 * 60)
    assert not out.revalued
    out = tick(eng, 121 * 60)
    assert out.revalued and v.because[-1] == "The backstop timer"
    assert eng.health(T0 + timedelta(minutes=122))["today"]["backstop"] == 1


def test_a_deadline_revalues_and_the_charge_carries_on(stubs):                           # B22
    f, v = stubs
    eng = EngineV2(V2Settings(deadline_grace_min=10))
    out = tick(eng, 0)
    assert out.mode.mode == CHARGE and out.mode.deadline == T0 + timedelta(minutes=70)
    out = tick(eng, 71 * 60, battery_soc=70.0)
    assert [e.kind for e in out.events if e.kind == "deadline"] == ["deadline"]
    assert out.mode.mode == CHARGE
    out = tick(eng, 71 * 60 + 15, battery_soc=70.0)                                  # the revalue follows
    assert out.revalued and "ran past its expected end" in v.because[-1]
    assert out.mode.mode == CHARGE


def test_learning_that_moved_asks_for_a_revalue(stubs, monkeypatch):
    f, v = stubs
    eng = EngineV2(V2Settings())
    tick(eng, 0)
    flags = iter([True])
    monkeypatch.setattr(eng.learner, "observe", lambda *a, **k: next(flags, False))
    out = tick(eng, 10)
    assert "learned" in [e.kind for e in out.events]
    out = tick(eng, 30)
    assert out.revalued and "learned" in v.because[-1]


def test_forecast_gets_the_learned_view(stubs):
    f, v = stubs
    eng = EngineV2(V2Settings())
    tick(eng, 0)
    assert {"weights", "solar_weights", "load_weights", "solar_bias", "load_spread"} <= set(f.learned)


# ---- the rules at work -------------------------------------------------------------------------
def test_a_car_charging_holds_the_battery_within_the_debounce(stubs):                   # B7
    f, v = stubs
    eng = EngineV2(V2Settings())
    v.value_p = 5.0
    tick(eng, 0, import_rate=0.30, ev_plug="Connected")
    outs = run(eng, 10, 120, import_rate=0.30, ev_plug="Charging", ev_power=7000.0, battery_soc=60.0)
    starts = [o for o in outs if any(e.kind == "car_start" for e in o.events)]
    assert len(starts) == 1
    assert (starts[0].observation.now - T0).total_seconds() <= 80
    assert outs[-1].mode.mode == HOLD and outs[-1].decision.rule == "v2_car"
    assert any(o.revalued for o in outs)


def test_the_reserve_stops_every_discharge(stubs):                                       # B14
    f, v = stubs
    v.value_p = 3.0
    eng = EngineV2(V2Settings(reserve_soc=20.0))
    out = tick(eng, 0, import_rate=0.30, battery_soc=45.0)
    assert out.mode.mode == SELF_USE
    eng = EngineV2(V2Settings(reserve_soc=20.0))
    out = tick(eng, 0, import_rate=0.30, battery_soc=19.0)
    assert out.mode.mode == HOLD and out.decision.rule == "v2_reserve"
    assert "reserve" in [e.kind for e in out.events]
    out = tick(eng, 10, import_rate=0.30, battery_soc=19.0)
    assert out.mode.mode == HOLD and "reserve" not in [e.kind for e in out.events]


def test_a_grid_event_goes_below_the_reserve_to_the_hard_floor_plus_margin(stubs):     # B14a
    f, v = stubs
    ev_kw = dict(axle_active=True, axle_start=T0 - timedelta(minutes=5), axle_end=T0 + timedelta(hours=1))
    eng = EngineV2(V2Settings(reserve_soc=25.0))
    out = tick(eng, 0, battery_soc=30.0, **ev_kw)
    assert out.mode.mode == EVENT and out.decision.action == v1d.FORCE_DISCHARGE
    out = tick(eng, 10, battery_soc=26.0, **ev_kw)                  # below the owner's reserve: still selling
    assert out.mode.mode == EVENT
    eng = EngineV2(V2Settings(reserve_soc=25.0))
    out = tick(eng, 0, battery_soc=11.0, **ev_kw)                    # the hard floor of 12% plus 1 is 13%
    assert out.mode.mode == HOLD and out.decision.rule == "v2_event_floor"


def test_an_override_is_forced_when_active(stubs):                                         # B19
    f, v = stubs
    eng = EngineV2(V2Settings())
    ov = Override("hold", T0 + timedelta(hours=1), T0)
    tick(eng, 0)
    out = tick(eng, 10, override=ov)
    assert out.mode.mode == HOLD and out.decision.rule == "v2_override"
    assert "override" in [e.kind for e in out.events]


def test_missing_data_for_ten_minutes_gives_self_use_and_coming_back_revalues(stubs):    # B12
    f, v = stubs
    eng = EngineV2(V2Settings())
    tick(eng, 0)
    outs = run(eng, 10, 600, import_rate=None)
    assert outs[3].mode.mode == CHARGE                       # three minutes in: still the old mode
    assert outs[-1].mode.mode == SELF_USE and outs[-1].decision.rule == "v2_data_missing"
    back = tick(eng, 620)
    back2 = tick(eng, 630)
    assert "data_back" in [e.kind for e in back.events]
    assert back.revalued or back2.revalued


def test_one_missing_reading_changes_nothing(stubs):                                       # B11
    f, v = stubs
    eng = EngineV2(V2Settings())
    first = tick(eng, 0)
    out = tick(eng, 10, import_rate=None)
    assert out.mode.mode == first.mode.mode and not out.mode_changed and not out.revalued
    assert out.decision.rule == first.decision.rule


def test_passive_still_decides_and_says_it_is_not_sending(stubs):
    f, v = stubs
    eng = EngineV2(V2Settings())
    out = tick(eng, 0, active=False)
    assert out.mode.mode == CHARGE and eng.sending is False
    from pe_core.engine_v2.types import Situation
    out = eng.step(inp(T0 + timedelta(seconds=10), situation=Situation(active=False, mode_reason="Passive mode")))
    assert eng.not_sending_reason == "Passive mode"


# ---- never raises ------------------------------------------------------------------------------
def test_a_broken_forecast_never_raises_and_keeps_the_previous_values(stubs):
    f, v = stubs
    eng = EngineV2(V2Settings())
    first = tick(eng, 0)
    f.fail = True
    new = [Window(T0 - timedelta(hours=12) + timedelta(minutes=30 * i), T0 - timedelta(hours=12)
                  + timedelta(minutes=30 * (i + 1)), 0.25) for i in range(96)]
    outs = run(eng, 10, 120, rates=new, import_rate=0.25)
    assert outs[-1].value is first.value
    errs = [r for o in outs for r in o.journal if r["kind"] == "error"]
    assert errs and errs[0]["where"] == "revalue" and "forecast broke" in errs[0]["error"]
    assert outs[-1].decision is not None


def test_a_forecast_that_returns_rubbish_never_raises(stubs, monkeypatch):
    f, v = stubs
    monkeypatch.setattr(f, "build", lambda *a, **k: object())
    eng = EngineV2(V2Settings())
    out = tick(eng, 0)
    assert out.value is None and out.decision.rule == "v2_starting"


def test_broken_lines_fall_back_to_self_use_and_are_journalled(stubs):
    f, v = stubs
    eng = EngineV2(V2Settings())
    tick(eng, 0)
    v.fail_lines = True
    out = tick(eng, 10)
    assert out.decision.rule == "v2_no_lines" and out.decision.action == v1d.SELF_USE
    assert any(r["where"] == "lines" for r in out.journal if r["kind"] == "error")


def test_a_failure_inside_the_step_is_caught_and_the_last_decision_stays(stubs, monkeypatch):
    f, v = stubs
    eng = EngineV2(V2Settings())
    first = tick(eng, 0)

    def boom(*a, **k):
        raise KeyError("executor bug")
    monkeypatch.setattr(eng.executor, "step", boom)
    out = tick(eng, 10)
    assert out.decision == first.decision and out.mode.mode == CHARGE
    assert out.journal[0]["kind"] == "error" and out.journal[0]["where"] == "step"
    assert eng.journal()[-1]["where"] == "step"
    assert eng.health(T0 + timedelta(seconds=10))["errors_today"] >= 1


def test_a_failure_on_the_very_first_step_gives_self_use(stubs, monkeypatch):
    f, v = stubs
    eng = EngineV2(V2Settings())
    monkeypatch.setattr(eng.observer, "update", lambda *a, **k: 1 / 0)
    out = tick(eng, 0)
    assert out.decision.action == v1d.SELF_USE and out.decision.rule == "v2_error"
    assert out.mode.mode == SELF_USE


# ---- the journal -------------------------------------------------------------------------------
def test_every_mode_change_has_a_journal_row_with_its_event_prices_and_value(stubs):
    f, v = stubs
    eng = EngineV2(V2Settings(min_dwell_s=0))
    changes = 0
    rows = []
    script = [(0, dict(import_rate=0.07)), (60, dict(import_rate=0.30)),
              (120, dict(import_rate=0.30, ev_plug="Charging", ev_power=7000.0)),
              (180, dict(import_rate=0.07, ev_plug="Charging", ev_power=7000.0)),
              (240, dict(import_rate=0.50)), (300, dict(import_rate=0.07))]
    for sec, kw in script:
        for s in range(sec, sec + 60, 10):
            out = tick(eng, s, **kw)
            changes += out.mode_changed
            rows += [r for r in out.journal if r["kind"] == "mode"]
    assert changes >= 4 and len(rows) == changes
    for r in rows:
        assert r["event"] and r["to"] and r["import_p"] is not None and r["value_p"] is not None and r["why"]
    assert len(eng.journal()) >= changes


def test_the_journal_keeps_the_last_300_rows(stubs):
    f, v = stubs
    eng = EngineV2(V2Settings())
    for i in range(400):
        eng.journal_rows.append({"at": str(i), "kind": "mode"})
    assert len(eng.journal()) == 300 and eng.journal()[0]["at"] == "100"


# ---- state -------------------------------------------------------------------------------------
def test_state_is_json_safe_and_a_restarted_engine_revalues_at_once_and_keeps_its_mode(stubs):   # B20
    f, v = stubs
    eng = EngineV2(V2Settings())
    run(eng, 0, 120)
    st = json.loads(json.dumps(eng.state()))
    eng2 = EngineV2(V2Settings(), st)
    assert eng2.executor.mode == CHARGE and eng2.journal() == eng.journal()
    out = tick(eng2, 200)
    assert out.revalued and out.mode.mode == CHARGE and not out.mode_changed
    assert "start" in [e.kind for e in out.events]


def test_health_reports_the_values_age_and_counts(stubs):
    f, v = stubs
    eng = EngineV2(V2Settings())
    run(eng, 0, 100)
    h = eng.health(T0 + timedelta(seconds=100))
    assert h["engine"] == "v2" and h["mode"] == CHARGE and h["values_because"] == "The engine started"
    assert h["values_age_min"] == 1.7 and h["today"]["revalues"] == 1 and h["today"]["mode_changes"] == 1
    assert h["sending"] is True and h["filter_gap_max_today"] >= 0


# ---- the real layers 2 and 3 -------------------------------------------------------------------
def test_smoke_with_the_real_forecast_and_value_layers():
    try:
        from pe_core.engine_v2 import forecast as real_fc
        from pe_core.engine_v2 import value as real_value
    except ImportError:                      # pragma: no cover
        pytest.skip("layers 2 and 3 are not there")
    assert engine_mod.forecast is real_fc and engine_mod.value is real_value
    from pe_core.forecast import LoadProfile
    from pe_core.readings import Readings
    from pe_core.tariff import tod
    local = timezone(timedelta(hours=1))
    now = T0.replace(hour=21, minute=0).astimezone(local)
    start = now.replace(hour=0, minute=0)
    rates = [Window(start + timedelta(minutes=30 * i), start + timedelta(minutes=30 * (i + 1)),
                    0.07 if (i >= 46 or i < 10 or 22 <= i < 24) else 0.30) for i in range(96)]
    eng = EngineV2(V2Settings())
    profile = LoadProfile(watts={(wk, h): 600.0 for wk in (True, False) for h in range(48)}, days=7)
    overnight = {tod(start + timedelta(minutes=30 * i), local) for i in list(range(0, 10)) + [46, 47]}
    outs = []
    for k in range(30):
        t = now + timedelta(minutes=k)
        r = Readings(now=t, battery_soc=40.0 + k * 0.1, battery_power=-4000.0 if k < 20 else 0.0, house_power=600.0,
                     solar_power=0.0, import_rate=0.30 if k < 10 else 0.07, export_rate=0.15, rates=rates,
                     ev_plug="Connected", ev_power=0.0, grid_power=0.0)
        from pe_core.engine_v2.types import BatteryFacts, Situation, StepInput
        out = eng.step(StepInput(now=t, readings=r, facts=BatteryFacts(), situation=Situation(active=True),
                                 tz=local, load_profile=profile, overnight=overnight))
        outs.append(out)
    assert outs[0].revalued and outs[0].value is not None and outs[0].value.timeline
    assert all(o.decision is not None for o in outs)
    assert not [r for o in outs for r in o.journal if r["kind"] == "error"], [r for o in outs for r in o.journal]
    assert outs[0].value.calc_s < 6.0
    assert sum(o.mode_changed for o in outs) <= 4


def test_the_values_are_worked_out_knowing_the_mode_running(stubs):
    # 8 Oct 2026: a plan made without it opened with "charge now" under a sale that was running and never ended
    f, v = stubs
    eng = EngineV2(V2Settings())
    tick(eng, 0, battery_soc=50.0)
    assert v.running == [None]                                  # first solve: nothing has run yet
    eng.executor.mode = "export"
    eng._forced_reason = "test"
    tick(eng, 30, battery_soc=50.0)
    assert v.running[-1] == "export"
