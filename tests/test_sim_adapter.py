"""The app's overnight Simulator plumbing, with a stub AppDaemon (see test_pause_guards)."""
import json
import os

from test_pause_guards import app  # noqa: F401  (fixture)
from test_simulator import DAYS, day_records, fake_api

from pe_core import kraken
from pe_core.costbook import CostBook


def test_overnight_run_publishes_and_notifies(app, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(kraken, "fetch_json", fake_api)
    folder = tmp_path / "costs"
    folder.mkdir()
    for d in DAYS:
        (folder / f"{d}.json").write_text(json.dumps(day_records(d)))
    app.costbook = CostBook(str(folder), app.tz)
    app._save_path = lambda: str(tmp_path / "config.yaml")
    app._params = lambda readings=None: __import__("pe_core.planner", fromlist=["Params"]).Params(capacity_kwh=10.0)
    app.run_in = lambda cb, delay, **kw: app.timers.append((cb, kw))
    notes = []
    app._notify = lambda event, msg: notes.append((event, msg))
    app._sim_start({})
    while app.timers:
        cb, kw = app.timers.pop(0)
        cb(kw)
    key, state, attrs = next(p for p in app.published if p[0] == "cost_simulator")
    assert attrs["days"] == 3 and len(attrs["ranking"]) == 2
    assert os.path.exists(tmp_path / "simulator" / "results.json")
    assert all(e == "simulator" for e, _ in notes)


def test_simulator_can_be_switched_off(app, tmp_path):  # noqa: F811
    from pe_core.config import parse_config
    app.cfg = parse_config({"features": {"tariff_simulator": False}})
    app.costbook = object()
    app._sim_start({})
    assert getattr(app, "_sim_job", None) is None
