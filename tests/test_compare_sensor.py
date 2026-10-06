"""sensor.pe_cost_engines (design 2.5): the contract the card reads, its size, the entity's registration, the result
file built from a replay, and the app's publishing and diagnostics section."""
import json
from datetime import date, timedelta

import pytest
from replay_harness import import_powerengine

from pe_core.compare import results
from pe_core.compare import sensor as cs
from pe_core.entities import ENTITIES

TODAY = date(2026, 10, 7)
KEYS = {"date", "status", "reason", "in_control", "live", "v1", "v2", "bound", "best", "diff", "v1_flips", "v2_flips",
        "calib"}


def ok_result(day, v1=1.3, v2=1.38, bound=1.51, engine="v1", live=True, diff_pct=-2.0):
    return {"version": 1, "day": day.isoformat(), "status": "ok", "reason": "", "took_s": 412,
            "made_at": f"{(day + timedelta(days=1)).isoformat()}T03:40:00+00:00", "in_control": engine, "live": live,
            "selfuse": {"cost": 3.12, "end_soc": 41.0},
            "v1": {"cost": 1.95, "end_soc": 52.0, "saving": v1, "flips": 2, "modes": 9},
            "v2": {"cost": 1.80, "end_soc": 47.0, "saving": v2, "flips": 4, "modes": 14},
            "bound": {"cost": 1.70, "saving": bound}, "metered": {"cost": 1.99},
            "calibration": {"engine": engine, "replay": 1.95, "metered": 1.99, "diff": -0.04, "diff_pct": diff_pct}
            if live and engine in ("v1", "v2") else None}


def week(**over):
    out = {}
    for n in range(1, 8):
        d = TODAY - timedelta(days=n)
        out[d.isoformat()] = ok_result(d, v1=1.0 + n / 10, v2=1.0 + n / 10 + (0.08 if n % 2 else -0.05))
    out.update(over)
    return out


def test_the_contract():
    state, a = cs.build(TODAY, week(), set(), next_run="2026-10-08T03:20:00+01:00")
    assert state == "ok"
    assert set(a) == {"days", "totals", "calib", "last_run", "running", "next_run", "note"}
    assert len(a["days"]) == 7 and [r["date"] for r in a["days"]][:2] == ["2026-10-06", "2026-10-05"]   # newest first
    for r in a["days"]:
        assert set(r) == KEYS
    r = a["days"][0]
    assert (r["status"], r["in_control"], r["live"]) == ("ok", "v1", True)
    assert r["v1"] == 1.1 and r["v2"] == pytest.approx(1.18) and r["bound"] == 1.51 and r["diff"] == 0.08
    assert r["best"] == "v2"
    assert (r["v1_flips"], r["v2_flips"]) == (2, 4) and r["calib"] == {"engine": "v1", "diff": -0.04, "diff_pct": -2.0}
    assert a["days"][1]["best"] == "v1"                                           # v2 was 5p worse that day
    assert a["totals"]["days"] == 7 and a["totals"]["diff"] == round(a["totals"]["v2"] - a["totals"]["v1"], 3)
    assert a["calib"] == {"engine": "v1", "days": 7, "mean_abs_diff": 0.04, "mean_abs_pct": 2.0}
    assert a["last_run"]["day"] == "2026-10-06" and a["last_run"]["status"] == "ok" and a["last_run"]["took_s"] == 412
    assert a["next_run"] == "2026-10-08T03:20:00+01:00" and "rough guide" in a["note"]


def test_a_tie_is_within_a_penny():
    r = cs.day_row(date(2026, 10, 6), ok_result(date(2026, 10, 6), v1=1.0, v2=1.009), True)
    assert r["best"] == "tie" and r["diff"] == 0.009


def test_days_not_compared_say_why():
    res = week()
    d1, d2, d3 = (TODAY - timedelta(days=n) for n in (1, 2, 3))
    res[d1.isoformat()] = None
    res[d2.isoformat()] = {"version": 1, "day": d2.isoformat(), "status": "no_snapshot",
                           "reason": "no forecast record at the start of the day", "took_s": 0,
                           "made_at": "2026-10-06T03:21:00+00:00"}
    res[d3.isoformat()] = {"version": 1, "day": d3.isoformat(), "status": "failed", "reason": "replay failed: x" * 30,
                           "took_s": 5, "made_at": "2026-10-05T03:21:00+00:00"}
    state, a = cs.build(TODAY, res, {d1.isoformat()}, next_run=None)
    rows = {r["date"]: r for r in a["days"]}
    assert rows[d1.isoformat()]["status"] == "pending" and rows[d1.isoformat()]["reason"] == cs.WAITING
    assert rows[d2.isoformat()]["status"] == "no_snapshot" and "forecast record" in rows[d2.isoformat()]["reason"]
    assert rows[d3.isoformat()]["status"] == "failed" and len(rows[d3.isoformat()]["reason"]) <= 120
    assert rows[d3.isoformat()]["v1"] is None and rows[d3.isoformat()]["best"] is None
    assert a["totals"]["days"] == 4
    nothing = cs.day_row(d1, None, False)
    assert nothing["status"] == "pending" and nothing["reason"] == cs.NO_SNAPSHOT


def test_the_states():
    res = week()
    assert cs.build(TODAY, res, set(), feature_on=False)[0] == "off"
    assert cs.build(TODAY, res, set(), running={"day": "2026-10-06", "since": "x"})[0] == "running"
    assert cs.build(TODAY, {}, set())[0] == "waiting"
    d = TODAY - timedelta(days=1)
    failed = {"version": 1, "day": d.isoformat(), "status": "failed", "reason": "boom", "took_s": 1,
              "made_at": "2026-10-07T03:21:00+00:00"}
    assert cs.build(TODAY, {d.isoformat(): failed}, set())[0] == "error"
    assert cs.build(TODAY, {**week(), d.isoformat(): failed}, set())[0] == "error"      # the latest run failed
    state, a = cs.build(TODAY, {}, set())
    assert a["days"][0]["status"] == "pending" and a["totals"]["days"] == 0 and a["calib"] is None
    assert a["last_run"] is None


def test_calibration_over_mixed_engines_and_days_without_one():
    res = week()
    d = TODAY - timedelta(days=2)
    res[d.isoformat()] = ok_result(d, engine="v2", diff_pct=4.0)
    d2 = TODAY - timedelta(days=3)
    res[d2.isoformat()] = ok_result(d2, engine="mixed", live=True)               # mixed control: not calibrated
    _, a = cs.build(TODAY, res, set())
    assert a["calib"]["engine"] == "mixed" and a["calib"]["days"] == 6
    assert next(r for r in a["days"] if r["date"] == d2.isoformat())["calib"] is None


def test_the_attributes_stay_under_4kb_in_the_worst_case():
    res = week()
    long = "x" * 400
    for n in (2, 4, 6):
        d = TODAY - timedelta(days=n)
        res[d.isoformat()] = {"version": 1, "day": d.isoformat(), "status": "failed", "reason": long, "took_s": 5400,
                              "made_at": "2026-10-06T04:50:00+00:00"}
    for r in res.values():
        r["made_at"] = r["made_at"] + ""
    for attrs in (cs.build(TODAY, week(), set(), next_run="2026-10-08T03:20:00+01:00",
                           running={"day": "2026-10-06", "since": "2026-10-07T03:20:00+01:00"})[1],
                  cs.build(TODAY, res, set(), next_run="2026-10-08T03:20:00+01:00")[1]):
        size = len(json.dumps(attrs, separators=(",", ":"), ensure_ascii=False).encode())
        assert size < 4096, size


def test_the_entity_is_registered_like_the_others():
    ent = next(e for e in ENTITIES if e.key == "cost_engines")
    assert ent.entity_id == "sensor.pe_cost_engines" and ent.component == "sensor"


# --- the result file --------------------------------------------------------------------------------------------

class DI:
    day = date(2026, 10, 6)
    in_control, live, metered_gbp = "v2", True, 1.234


def outcome():
    def run(cost, end, save, flips=1, modes=3):
        return {"cost_gbp": cost, "end_soc": end, "adj_save_gbp": save, "flip_flops": flips, "mode_changes": modes}
    return {"runs": {"selfuse": {"cost_gbp": 3.1234, "end_soc": 41.04}, "v1": run(1.9512, 52.0, 1.3, 2, 9),
                     "v2": run(1.2, 47.0, 1.38, 4, 14), "bound": {"cost_gbp": 1.0, "adj_save_gbp": 1.51}}}


def test_the_result_file_is_built_as_designed():
    r = results.build_result(DI, outcome(), 411.6, made_at="2026-10-07T03:40:00+00:00")
    assert r == {"version": 1, "day": "2026-10-06", "status": "ok", "reason": "", "took_s": 412,
                 "made_at": "2026-10-07T03:40:00+00:00", "in_control": "v2", "live": True,
                 "selfuse": {"cost": 3.123, "end_soc": 41.0},
                 "v1": {"cost": 1.951, "end_soc": 52.0, "saving": 1.3, "flips": 2, "modes": 9},
                 "v2": {"cost": 1.2, "end_soc": 47.0, "saving": 1.38, "flips": 4, "modes": 14},
                 "bound": {"cost": 1.0, "saving": 1.51}, "metered": {"cost": 1.234},
                 "calibration": {"engine": "v2", "replay": 1.2, "metered": 1.234, "diff": -0.034, "diff_pct": -2.8}}


@pytest.mark.parametrize("engine, live", [("mixed", True), ("v1", False), (None, False)])
def test_no_calibration_without_a_live_single_engine(engine, live):
    class D(DI):
        in_control = engine
    D.live = live
    assert results.build_result(D, outcome(), 1)["calibration"] is None


def test_result_files_are_written_read_and_pruned(tmp_path):
    costs = str(tmp_path)
    old, new = date(2026, 8, 1), date(2026, 10, 6)
    results.write_result(costs, old, results.refused_result(old, "no_snapshot", "x"))
    results.write_result(costs, new, results.refused_result(new, "incomplete", "y" * 500))
    got = results.read_result(costs, new)
    assert got["status"] == "incomplete" and len(got["reason"]) == 300
    assert results.read_result(costs, date(2026, 9, 9)) is None and results.has_result(costs, new)
    results.prune(costs, TODAY)
    assert not results.has_result(costs, old) and results.has_result(costs, new)


# --- the app ------------------------------------------------------------------------------------------------------

class Stub:
    """Just enough of PowerEngine for the comparison's methods."""

    def __init__(self, pe, tmp, features=None, demo=False):
        self.pe = pe
        self.cfg = type("Cfg", (), {"features": features if features is not None else {}})()
        self._demo, self.tz, self._published, self.published, self.logs = demo, None, {}, [], []
        self.tmp = tmp

    def _save_path(self):
        return str(self.tmp / "config.yaml")

    def _today(self):
        return TODAY

    def _publish_state(self, key, state, attrs=None):
        self.published.append((key, state, attrs))

    def log(self, msg, **kw):
        self.logs.append((kw.get("level", "INFO"), msg))

    def __getattr__(self, name):
        if name.startswith("_compare") or name == "_publish_if_changed":
            return getattr(self.pe.PowerEngine, name).__get__(self)
        raise AttributeError(name)


@pytest.fixture
def pe(monkeypatch):
    return import_powerengine(monkeypatch)


def test_the_app_publishes_the_sensor(pe, tmp_path):
    app = Stub(pe, tmp_path)
    costs = str(tmp_path / "costs")
    results.write_result(costs, TODAY - timedelta(days=1), ok_result(TODAY - timedelta(days=1)))
    app._compare_publish()
    (key, state, attrs), = app.published
    assert key == "cost_engines" and state == "ok" and attrs["days"][0]["v2"] == 1.38 and attrs["next_run"]
    app._compare_publish()
    assert len(app.published) == 1                                                  # unchanged: not published again


def test_feature_off_and_demo(pe, tmp_path):
    off = Stub(pe, tmp_path, features={"engine_compare": False})
    off._compare_publish()
    assert off.published[0][1] == "off" and off.published[0][2]["next_run"] is None
    off._compare_start()                                                            # nothing starts
    assert off.__dict__.get("_compare") is None
    demo = Stub(pe, tmp_path, demo=True)
    demo._compare_publish()
    assert demo.published[0][1] == "off"
    demo._compare_start()
    assert demo.__dict__.get("_compare") is None


def test_the_nightly_start_and_the_failure_warning(pe, tmp_path, monkeypatch):
    app = Stub(pe, tmp_path)
    started = []

    class P:
        pid = 1

        def poll(self):
            return 1

        def kill(self):
            pass
    monkeypatch.setattr("pe_core.compare.schedule.subprocess.Popen", lambda cmd, **kw: started.append(cmd) or P())
    app._compare_start()
    assert started and "pe_core.compare.run" in started[0] and "2026-10-06" in started[0]
    app._compare_poll()
    warnings = [m for lvl, m in app.logs if lvl == "WARNING"]
    assert len(warnings) == 1 and "2026-10-06 failed" in warnings[0]                 # one WARNING, not one per poll
    assert app.published[-1][1] == "error"


def test_the_diagnostics_section(pe, tmp_path):
    app = Stub(pe, tmp_path)
    d = TODAY - timedelta(days=1)
    results.write_result(str(tmp_path / "costs"), d, ok_result(d))
    b = app._compare_bundle()
    assert b["feature"] is True and b["state"] == "ok" and b["results"][0]["day"] == d.isoformat()
    assert b["queue"] == [] and b["running"] is False and b["totals"]["days"] == 1
    json.dumps(b)
