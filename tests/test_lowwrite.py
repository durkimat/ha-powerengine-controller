"""pe_core/lowwrite.py (stage L1 of the low-write mode): the study on recorded days, and the optimiser's plan_tier."""

import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from pe_core.decide import EXPORT, GRID_CHARGE, HOLD, SELF_USE
from pe_core.forecast import Slot
from pe_core.lowwrite import PROFILES, Study, evaluate_day, profile_params, summarise, usable
from pe_core.optimiser import _actions
from pe_core.planner import Params

LON = ZoneInfo("Europe/London")
UTC = timezone.utc
DAYS = [f"2026-09-{d}" for d in range(14, 22)]
NOW = datetime(2026, 9, 22, 9, 0, tzinfo=UTC)


def day(d):
    start = datetime.combine(date.fromisoformat(d), datetime.min.time(), tzinfo=LON).astimezone(UTC)
    return [{"start": (start + timedelta(minutes=30 * i)).isoformat(), "seconds": 1800, "house": 0.4, "car": 0,
             "solar": 0.8 if 20 <= i < 30 else 0.0, "import_rate": 0.07 if i < 10 else 0.30, "export_rate": 0.15,
             "standing": 0.57, "grid_import": 0.4, "grid_export": 0.0, "soc_start": 30.0} for i in range(48)]


def slot(**kw):
    return Slot(start=NOW, price=0.07, export=0.15, solar_kwh=0.0, load_kwh=0.4, **kw)


def test_the_default_tier_changes_nothing_and_lower_tiers_take_actions_away():
    s = slot(overnight=False)
    full = _actions(s, Params(arbitrage=True))
    assert set(full) >= {SELF_USE, HOLD, GRID_CHARGE, EXPORT}
    assert _actions(s, Params(arbitrage=True, plan_tier=4)) == full
    assert _actions(s, Params(arbitrage=True, plan_tier=0)) == [SELF_USE]
    by_day = _actions(s, Params(arbitrage=True, plan_tier=2))
    assert by_day == [SELF_USE]                                       # nothing happens outside the overnight window
    night = _actions(slot(overnight=True), Params(arbitrage=True, plan_tier=2))
    assert GRID_CHARGE in night and EXPORT in night
    assert EXPORT not in _actions(slot(overnight=True), Params(arbitrage=True, plan_tier=1))
    assert GRID_CHARGE in _actions(s, Params(arbitrage=True, plan_tier=3))


def test_a_profile_sets_tier_price_and_arbitrage():
    base = Params(arbitrage=True, switch_cost_p=5.0)
    full = profile_params(base, PROFILES[1], 5.0)
    assert full.plan_tier == 4 and full.switch_cost_p == 5.0
    charge = profile_params(base, PROFILES[3], 5.0)
    assert charge.plan_tier == 1 and charge.switch_cost_p == 10.0 and charge.arbitrage is False


def test_a_day_is_planned_and_the_full_plan_beats_self_use():
    recs = day("2026-09-20")
    assert usable(recs) and not usable(recs[:20])
    base = Params()
    res = {p[0]: evaluate_day(recs, day("2026-09-21"), profile_params(base, p, 5.0), LON) for p in PROFILES}
    assert res["self_use"]["changes"] == 0 and res["self_use"]["writes"] == 0
    assert res["full"]["net"] <= res["self_use"]["net"]
    assert res["overnight_charge"]["changes"] <= res["full"]["changes"]
    assert len(res["full"]["acts"]) == 48


def test_the_summary_compares_with_self_use_and_the_full_plan():
    per = {d: {"self_use": {"net": 2.0, "changes": 0, "writes": 0}, "full": {"net": 1.0, "changes": 6, "writes": 12},
               "overnight_cycle": {"net": 1.2, "changes": 3, "writes": 6},
               "overnight_charge": {"net": 1.5, "changes": 2, "writes": 4}} for d in ("a", "b")}
    s = summarise(per, ["a", "b"])
    rows = {r["id"]: r for r in s["profiles"]}
    assert rows["full"]["saving_gbp_day"] == 1.0 and rows["full"]["kept_pct"] == 100
    assert rows["overnight_cycle"]["kept_pct"] == 80 and rows["overnight_cycle"]["writes_day"] == 6
    assert rows["self_use"]["kept_pct"] is None
    assert summarise({}, [])["days"] == 0


def test_the_study_plans_new_days_only_and_keeps_its_results(tmp_path):
    store = {d: day(d) for d in DAYS}
    calls = []

    def records(d):
        calls.append(d)
        return store[d]
    study = Study(str(tmp_path))
    first = study.run(sorted(store), records, Params(), 5.0, LON, NOW, max_days=3, limit=2)
    assert first["days"] == 2 and first["pending"] == 1
    second = study.run(sorted(store), records, Params(), 5.0, LON, NOW, max_days=3, limit=2)
    assert second["days"] == 3 and second["pending"] == 0
    saved = json.loads((tmp_path / "lowwrite.json").read_text())
    assert set(saved["days"]) == {"2026-09-19", "2026-09-20", "2026-09-21"}
    again = Study(str(tmp_path))
    assert again.run(sorted(store), records, replace(Params(), target_soc=90.0), 5.0, LON, NOW, max_days=3)["days"] == 3
