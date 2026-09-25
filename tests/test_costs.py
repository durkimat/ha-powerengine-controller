from datetime import datetime, timedelta, timezone

import pytest

from pe_core.costs import SimDefault, day_summary, process, steps
from pe_core.energy import FLOWS, Recorder, allocate
from pe_core.ledger import Ledger
from pe_core.readings import Readings, Window
from pe_core.tariff import Rates, cheap_tods, overnight_window, rates_at, tod

UTC = timezone.utc
T0 = datetime(2026, 9, 22, 0, 0, tzinfo=UTC)
PEAK, CHEAP, EXP = 0.3028, 0.0699, 0.15


# --- allocation ------------------------------------------------------------------------

def test_allocation_solar_first_then_battery_then_grid():
    f = allocate(solar_w=1000, grid_w=500, battery_w=300, house_w=1800, car_w=0)
    assert (f["s_h"], f["b_h"], f["g_h"]) == (1000, 300, 500)
    assert f["unallocated_src"] == 0 and f["unallocated_sink"] == 0


def test_allocation_grid_charging_and_export():
    f = allocate(solar_w=0, grid_w=5800, battery_w=-4800, house_w=1000, car_w=0)
    assert f["g_h"] == 1000 and f["g_b"] == 4800
    f = allocate(solar_w=3000, grid_w=-1500, battery_w=-1000, house_w=500, car_w=0)
    assert f["s_h"] == 500 and f["s_b"] == 1000 and f["s_e"] == 1500


def test_allocation_car_is_covered_after_the_house_and_never_from_nothing():
    f = allocate(solar_w=0, grid_w=8000, battery_w=0, house_w=600, car_w=7400)
    assert f["g_h"] == 600 and f["g_c"] == 7400


def test_allocation_keeps_meter_mismatch_visible():
    f = allocate(solar_w=0, grid_w=1000, battery_w=0, house_w=800, car_w=0)
    assert f["unallocated_src"] == pytest.approx(200)


# --- recorder --------------------------------------------------------------------------

def R(t, **kw):
    base = dict(now=t, battery_soc=50, battery_power=0, grid_power=1000, house_power=1000, solar_power=0,
                import_rate=PEAK, export_rate=EXP, ev_plug="EV Disconnected", ev_power=0, standing_charge=0.57)
    base.update(kw)
    return Readings(**base)


def test_recorder_integrates_and_splits_at_the_half_hour():
    rec = Recorder()
    done = None
    t = T0 + timedelta(minutes=29)
    for i in range(5):                               # 29:00 .. 31:00 every 30 s
        done = rec.add(R(t + timedelta(seconds=30 * i))) or done
    assert done is not None and done.start == T0
    assert done.grid_import == pytest.approx(1.0 * 60 / 3600)     # 1 kW for the minute up to :30
    assert rec.current.grid_import == pytest.approx(1.0 * 60 / 3600)


# --- tariff ------------------------------------------------------------------------------

def day_rates(day0, slot_hours=()):
    out = []
    for i in range(48):
        t = day0 + timedelta(minutes=30 * i)
        cheap = i < 11 or i >= 47 or (t.hour in slot_hours)       # 23:30-05:30 overnight + slots
        out.append(Window(t, t + timedelta(minutes=30), CHEAP if cheap else PEAK))
    return out


def test_standard_rate_for_a_smart_slot_is_the_peak_rate():
    d1, d2 = day_rates(T0, slot_hours=(13,)), day_rates(T0 + timedelta(days=1), slot_hours=(15,))
    window = overnight_window(cheap_tods(d1 + d2))
    assert tod(T0 + timedelta(hours=13)) not in window and tod(T0 + timedelta(hours=2)) in window
    slot = rates_at(T0 + timedelta(hours=13), d1 + d2, window, EXP, None)
    assert slot.smart_slot and slot.actual == CHEAP and slot.standard == PEAK and slot.overnight == CHEAP
    night = rates_at(T0 + timedelta(hours=2), d1 + d2, window, EXP, None)
    assert not night.smart_slot and night.standard == CHEAP
    peak = rates_at(T0 + timedelta(hours=18), d1 + d2, window, EXP, None)
    assert peak.standard == peak.actual == PEAK


# --- ledger ------------------------------------------------------------------------------

def test_ledger_first_in_first_out_with_losses():
    lg = Ledger()
    lg.add(2.0, "grid", 0.07)
    lg.add(2.0, "solar", 0.15)
    used = lg.take(1.8, 0.9, 0.07)                  # needs 2.0 kWh of lots
    assert [(round(x.kwh, 3), x.source) for x in used] == [(2.0, "grid")]
    assert lg.kwh == pytest.approx(2.0) and lg.value == pytest.approx(0.30)
    used = lg.take(2.7, 0.9, 0.07)                  # 3.0 needed, only 2.0 left -> 1.0 unknown at fallback
    assert sum(x.kwh for x in used) == pytest.approx(3.0) and lg.kwh == 0


# --- the reconciliation identity -----------------------------------------------------------

def rec(**kw):
    base = dict.fromkeys(FLOWS, 0.0)
    base.update(seconds=1800, soc_start=None, standing=0.0)      # None: no state-of-charge check
    base.update(kw)
    base["grid_import"] = base["g_h"] + base["g_c"] + base["g_b"]
    base["grid_export"] = base["s_e"] + base["b_e"]
    return base


CASES = [
    (rec(g_h=0.3, g_b=2.4, soc_start=50), Rates(CHEAP, CHEAP, CHEAP, EXP, False)),  # overnight charge (opening SoC)
    (rec(g_h=0.2, g_c=3.7, g_b=2.0), Rates(CHEAP, PEAK, CHEAP, EXP, True)),        # daytime smart slot, car + battery
    (rec(s_h=0.4, s_b=1.0, s_e=0.5), Rates(PEAK, PEAK, CHEAP, EXP, False)),       # sunny
    (rec(b_h=0.6, s_h=0.1), Rates(PEAK, PEAK, CHEAP, EXP, False)),                # evening, battery covers house
    (rec(b_h=0.4, b_e=1.5), Rates(PEAK, PEAK, CHEAP, EXP, False)),  # battery export: grid lots -> arbitrage
    (rec(g_h=0.5), Rates(PEAK, PEAK, CHEAP, EXP, False)),                         # plain import
]


def run(cases, **kw):
    lg, sim, out = Ledger(), SimDefault(), []
    for r, rt in cases:
        v = process(r, rt, lg, sim, capacity=18, eff=0.95, floor_soc=12, max_kw=4.8, includes_ev=True, **kw)
        out.append({"v": v})
    return out, lg


def test_layers_reconcile_exactly_when_the_meters_balance():
    records, _ = run(CASES)
    s = day_summary(records, complete=False)
    assert s["unexplained"] == pytest.approx(0, abs=0.011)          # rounding to pennies only
    assert s["smart"] == pytest.approx(0.2 * (PEAK - CHEAP) + 2.0 * (PEAK - CHEAP), abs=0.01)
    assert s["arbitrage"] != 0                                        # grid energy was exported from the battery
    st = steps(s)
    assert st[0][0] == "S0" and st[-1] == ("Actual", s["actual"])
    assert st[-2][1] == pytest.approx(s["actual"] - s["unexplained"], abs=0.02)


def test_events_are_kept_out_of_the_everyday_layers():
    axle = rec(b_e=2.0)
    axle["axle"] = True
    records, _ = run(CASES[:1] + [(axle, Rates(PEAK, PEAK, CHEAP, EXP, False))])
    s = day_summary(records, complete=False)
    ev = s["events"]["axle"]
    assert ev["kwh"] == pytest.approx(2.0) and ev["gross"] == pytest.approx(2.0)
    assert 0 < ev["net"] < 2.0
    only_night, _ = run(CASES[:1])
    assert s["s0"] == day_summary(only_night, complete=False)["s0"]


def test_standing_charge_counts_once_per_complete_day():
    records, _ = run(CASES)
    s = day_summary(records, standing_per_day=0.57, complete=True)
    assert s["standing"] == 0.57
    assert s["unexplained"] == pytest.approx(0, abs=0.011)


# --- the cost book (files) ---------------------------------------------------------------

def test_cost_book_records_values_and_summarises(tmp_path):
    from pe_core.costbook import CostBook, cost_entity_states
    rates = day_rates(T0) + day_rates(T0 + timedelta(days=1))
    book = CostBook(str(tmp_path), UTC)
    rec = Recorder()
    t = T0 + timedelta(hours=1)
    stored = []
    for i in range(4 * 60 * 2):                     # 4 hours of 30-second readings, grid-charging overnight at 2 kW
        soc = 50 + 2.0 * 0.95 * (i * 30 / 3600) / 18 * 100
        r = R(t + timedelta(seconds=30 * i), grid_power=3000, battery_power=-2000, house_power=1000,
              import_rate=CHEAP, rates=rates, battery_soc=soc)
        hh = rec.add(r)
        if hh:
            out = book.add(hh, r, capacity=18, eff=0.95, floor_soc=12, max_kw=4.8, includes_ev=True)
            stored.append(out)
    assert len([x for x in stored if x]) == 7
    s = book.summary(T0.date(), today=T0.date())
    assert s["half_hours"] == 7 and not s["complete"]
    assert s["actual"] == pytest.approx(7 * 0.5 * 3.0 * CHEAP + 0.57 * 3.5 / 24, abs=0.02)   # + standing so far
    assert s["energy"]["grid_import"] == pytest.approx(10.5, abs=0.1)
    assert abs(s["energy"]["correction_kwh"]) < 0.2              # the ledger tracked the battery
    assert s["stored"] > 0                                      # bought now, for later
    assert s["unexplained"] == pytest.approx(0, abs=0.02)
    # state survives a restart
    again = CostBook(str(tmp_path), UTC)
    assert again.ledger.kwh == pytest.approx(book.ledger.kwh)
    states = cost_entity_states(again, T0.date(), again.months(T0.date()))
    assert states["cost_today"][0] == s["actual"]
    assert states["cost_days"][1]["days"][-1]["date"] == T0.date().isoformat()


# --- backfill from history -----------------------------------------------------------------

def _rows(points, unit=None):
    rows = [{"state": str(v), "last_changed": t.isoformat()} for t, v in points]
    if unit and rows:
        rows[0]["attributes"] = {"unit_of_measurement": unit}
    return [rows]


def test_replay_backfills_a_day_and_revalue_reconciles(tmp_path):
    from fixtures import CONFIG

    from pe_core.costbook import CostBook
    from pe_core.replay import Timeline, history_entities, replay
    day0 = T0
    rates = [{"start": w.start.isoformat(), "end": w.end.isoformat(), "value_inc_vat": w.value}
             for w in day_rates(day0)]
    night, evening = day0 + timedelta(hours=1), day0 + timedelta(hours=18)
    tl = {
        "sensor.bat_soc": Timeline(_rows([(day0, 30), (night + timedelta(hours=3), 90), (evening, 90)])),
        # grid-charge 01:00-04:00, battery covers the house 18:00-21:00
        "sensor.bat_power": Timeline(_rows([(day0, 0), (night, -4800), (night + timedelta(hours=3), 0),
                                            (evening, 1000), (evening + timedelta(hours=3), 0)], "W")),
        "sensor.meter_power": Timeline(_rows([(day0, 1000), (night, 5800), (night + timedelta(hours=3), 1000),
                                              (evening, 0), (evening + timedelta(hours=3), 1000)], "W")),
        "sensor.house_load": Timeline(_rows([(day0, 1000)], "W")),
        "sensor.rate_now": Timeline(_rows([(day0, CHEAP), (day0 + timedelta(hours=5, minutes=30), PEAK)], "GBP/kWh")),
        "sensor.export_rate": Timeline(_rows([(day0, EXP)], "GBP/kWh")),
        "event.rates_today": Timeline([[{"state": day0.isoformat(), "last_changed": day0.isoformat(),
                                         "attributes": {"rates": rates}}]]),
    }
    plain, full = history_entities(CONFIG)
    assert "sensor.house_load" in plain and full == ["event.rates_today"]
    book = CostBook(str(tmp_path), UTC)
    assert day0.date() in book.days_to_backfill(day0.date() + timedelta(days=1), 3)
    rec, n = Recorder(), 0
    for r in replay(CONFIG, tl, day0, day0 + timedelta(days=1, seconds=30)):
        hh = rec.add(r)
        if hh and book.add(hh, r, capacity=18, eff=0.95, floor_soc=12, max_kw=4.8, includes_ev=True,
                           keep_existing=True):
            n += 1
    assert n == 48
    assert day0.date() not in book.days_to_backfill(day0.date() + timedelta(days=1), 3)
    assert book.revalue(capacity=18, eff=0.95, floor_soc=12, max_kw=4.8, includes_ev=True) == 48
    s = book.summary(day0.date(), today=day0.date() + timedelta(days=1))
    assert s["complete"] and s["half_hours"] == 48 and s["coverage"] == 1.0
    assert s["actual"] == pytest.approx(20.4 * CHEAP + 15 * PEAK, abs=0.02)      # metered import at the listed rates
    assert s["unexplained"] == pytest.approx(0, abs=0.02)
    assert s["s3b"] > 0                                    # charging at night for the evening beats plain self-use
    assert s["stored"] > 0                                 # the battery ends fuller than it started


def test_ledger_follows_the_real_battery_and_corrections_land_in_unexplained():
    """A battery that holds more than the flows say (e.g. better efficiency than configured) is corrected every
    half-hour, so 'carried in battery' stays real and the difference shows as unexplained, never as a drift."""
    lg, sim, out = Ledger(), SimDefault(), []
    soc = 50.0
    for _i in range(20):                                       # battery covers the house; SoC falls more slowly
        r = rec(b_h=0.5, soc_start=soc)
        out.append({"v": process(r, Rates(PEAK, PEAK, CHEAP, EXP, False), lg, sim, capacity=18, eff=0.95,
                                 floor_soc=12, max_kw=4.8, includes_ev=True)})
        soc -= 0.5 / 0.98 / 18 * 100                           # real losses smaller than configured
    s = day_summary(out, complete=False)
    assert s["energy"]["correction_kwh"] > 0
    assert abs(s["stored"]) < 3.0                              # bounded by what the battery can hold
    assert s["unexplained"] < 0                                # the extra energy made the day cheaper than modelled


def test_older_method_records_are_flagged_for_revalue(tmp_path):
    import json

    from pe_core.costbook import CostBook
    (tmp_path / "state.json").write_text(json.dumps({"method": 1, "ledger": []}))
    assert CostBook(str(tmp_path), UTC).needs_revalue
    book = CostBook(str(tmp_path / "fresh"), UTC)
    assert not book.needs_revalue
