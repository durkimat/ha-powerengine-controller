"""The simulator's tooling (tools/sim): variants, the cache key, code export, the report, the GUI server's guards
and the
viewer's data adapter. The full-day runs need the owner's archive and are in test_sim_run.py (skipped without it)."""

import http.client
import json
import pathlib
import shutil
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "sim"))
import export  # noqa: E402
import runner  # noqa: E402
import scoreboard  # noqa: E402
import server  # noqa: E402
import workspace  # noqa: E402


def make_archive(tmp_path, days=("2026-10-07",)):
    data = tmp_path / "data"
    (data / "costs" / "snapshots").mkdir(parents=True)
    for d in days:
        (data / "costs" / f"{d}.json").write_text("[]")
        (data / "costs" / "snapshots" / f"{d}.json").write_text("{}")
    for sub, name, text in (
        ("config", "a.yaml", "x: 1"),
        ("engine_v2_state", "s.json", "{}"),
        ("slots", "l.json", "{}"),
    ):
        (data / "versions" / sub).mkdir(parents=True, exist_ok=True)
        (data / "versions" / sub / name).write_text(text)
    return data


def test_days_and_assignments():
    avail = ["2026-10-06", "2026-10-07"]
    assert runner.parse_days("all", avail) == avail
    assert runner.parse_days("2026-10-06..2026-10-08", avail) == ["2026-10-06", "2026-10-07", "2026-10-08"]
    assert runner.parse_days("2026-10-07,2026-10-09", avail) == ["2026-10-07", "2026-10-09"]
    assert runner.parse_assignments(["a=1", "b = x"]) == {"a": "1", "b": "x"}
    with pytest.raises(SystemExit):
        runner.parse_assignments(["nokey"])


def test_sweep_makes_a_grid_and_routes_constants():
    base = runner.Variant("v", settings={"reserve_soc": "10"})
    out = runner.sweep_variants(base, ["switch_cost_p=1,3", "engine_v2.value.NAME=5,6"])
    assert len(out) == 4
    assert {tuple(sorted(v.settings)) for v in out} == {("reserve_soc", "switch_cost_p")}
    assert all(len(v.consts) == 1 for v in out)
    assert len({v.name for v in out}) == 4
    assert runner.sweep_variants(base, []) == [base]


def test_work_folder_must_not_overlap_the_archive(tmp_path):
    with pytest.raises(SystemExit):
        workspace.check_work(tmp_path / "data" / "sim", tmp_path / "data")
    with pytest.raises(SystemExit):
        workspace.check_work(tmp_path, tmp_path / "data")
    workspace.check_work(tmp_path / "work", tmp_path / "data")


def test_job_key_follows_every_input(tmp_path):
    data = make_archive(tmp_path)
    seed = workspace.seed_files(data)

    def key(**kw):
        args = dict(code="c1", day="2026-10-07", data=data, seed=seed, fresh=False, settings={}, consts={})
        return workspace.job_key(**{**args, **kw})

    base = key()
    assert key() == base
    assert key(code="c2") != base
    assert key(settings={"reserve_soc": 15}) != base
    assert key(consts={"engine_v2.value.X": 1}) != base
    assert key(fresh=True) != base
    (data / "costs" / "2026-10-07.json").write_text("[1]")
    assert key() != base  # the day's records changed


def test_code_id_ignores_git_state_and_follows_files(tmp_path):
    app = tmp_path / "apps" / "powerengine" / "pe_core"
    app.mkdir(parents=True)
    (app / "a.py").write_text("x = 1\n")
    first = workspace.code_id(tmp_path)
    (app / "__pycache__").mkdir()
    (app / "__pycache__" / "a.pyc").write_bytes(b"junk")
    assert workspace.code_id(tmp_path) == first
    (app / "a.py").write_text("x = 2\n")
    assert workspace.code_id(tmp_path) != first


def test_a_git_ref_is_exported_not_checked_out(tmp_path):
    before = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout
    root, label = workspace.resolve_code("HEAD", tmp_path)
    assert (root / "apps" / "powerengine" / "pe_core" / "version.py").is_file()
    assert "HEAD" in label
    assert workspace.resolve_code("HEAD", tmp_path)[0] == root  # reused, not exported again
    after = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout
    assert before == after
    with pytest.raises(SystemExit):
        workspace.resolve_code("no-such-ref-xyz", tmp_path)
    shutil.rmtree(tmp_path / "code")


def test_seed_assembly_only_links_the_archive(tmp_path):
    data = make_archive(tmp_path)
    save = workspace.assemble_save_dir(tmp_path / "save", data, workspace.seed_files(data), fresh=False)
    assert (save / "config.yaml").read_text() == "x: 1"
    assert (save / "engine_v2_state.json").exists() and (save / "costs" / "slots.json").exists()
    assert (save / "costs" / "2026-10-07.json").is_symlink() and (save / "costs" / "snapshots").is_symlink()
    fresh = workspace.assemble_save_dir(tmp_path / "save2", data, workspace.seed_files(data), fresh=True)
    assert not (fresh / "engine_v2_state.json").exists()


def fake_result(day, cost, flips=0):
    score = {
        "cost": cost,
        "adj_cost": cost,
        "save_vs_selfuse": 1.0,
        "gap_to_bound": 0.5,
        "mode_changes": 6,
        "flip_flops": flips,
        "peak_charge_kwh": 0.0,
        "min_soc": 14.0,
        "commands": 6,
        "calc_s": 10.0,
        "wall_s": 100.0,
    }
    plan = {
        "at": "2026-10-07T12:00:00+01:00",
        "because": "test",
        "timeline": [["charge", "2026-10-07T12:00", "2026-10-07T13:00", 40.0, 60.0, "", ""]],
    }
    return {
        "day": day,
        "status": "ok",
        "code": "x @ abc",
        "score": score,
        "series": {
            "changes": [["12:00:00", "charge"]],
            "trace": [["12:00", 40.0, "charge", 5000, 0]],
            "plans": [plan],
            "rows": [
                {"start": "2026-10-07T00:00+01:00", "act": 0.07, "exp": 0.15, "solar": 0.0, "house": 0.3, "car": 0.0}
            ],
        },
    }


def test_scoreboard_and_report(tmp_path):
    a, b = (
        [fake_result("2026-10-07", -0.5), fake_result("2026-10-08", -0.4)],
        [fake_result("2026-10-07", -0.7, 1), {"day": "2026-10-08", "status": "failed", "reason": "boom"}],
    )
    text = scoreboard.table("base", a)
    assert "TOTAL" in text and "-0.50" in text
    assert "not run: failed" in scoreboard.table("v", b)
    assert "cheaper" not in scoreboard.versus(a, b, "v") and "-0.20" in scoreboard.versus(a, b, "v")
    variants = [runner.Variant("base").as_dict(), runner.Variant("v", settings={"reserve_soc": 20}).as_dict()]
    out = export.write_report(tmp_path / "out", variants, {"base": a, "v": b})
    assert "reserve_soc" in (out / "report.md").read_text()
    body = json.loads((out / "report.json").read_text())
    assert body["variants"][0]["days"][0]["replans"][0].startswith("12:00 [test] 12:00 chg 40->60")
    assert body["variants"][1]["days"][1]["status"] == "failed"


@pytest.fixture
def gui(tmp_path):
    data = make_archive(tmp_path)
    ws = runner.Workspace(data, tmp_path / "work")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    server.Handler.ws, server.Handler.batches, server.Handler.port = ws, server.Batches(ws, 1), httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1], ws
    httpd.shutdown()


def call(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers or {})
    r = conn.getresponse()
    data = r.read()
    conn.close()
    return r.status, data


def test_server_serves_state_and_refuses_strangers(gui):
    port, _ = gui
    status, data = call(port, "GET", "/api/state")
    assert status == 200 and json.loads(data)["days"] == ["2026-10-07"]
    assert call(port, "GET", "/")[0] == 200 and call(port, "GET", "/app.js")[0] == 200
    assert call(port, "GET", "/../server.py")[0] == 404
    assert call(port, "GET", "/api/state", headers={"Host": "evil.example"})[0] == 403  # DNS rebinding
    assert call(port, "POST", "/api/run", {"variants": [], "days": []})[0] == 403  # no header: a cross-site form
    ok = {"X-PE-Sim": "1"}
    assert call(port, "POST", "/api/run", {"variants": [], "days": []}, ok)[0] == 400
    bad = {"variants": [{"name": "x", "code": "/tmp/anything"}], "days": ["2026-10-07"]}
    assert call(port, "POST", "/api/run", bad, ok)[0] == 400  # never a folder from the page
    assert call(port, "GET", "/api/result/not-a-key")[0] == 404


def test_server_saves_variants(gui):
    port, ws = gui
    v = [runner.Variant("a", settings={"reserve_soc": 20}).as_dict()]
    assert call(port, "POST", "/api/variants", {"variants": v}, {"X-PE-Sim": "1"})[0] == 200
    assert json.loads(call(port, "GET", "/api/state")[1])["variants"] == v


def test_worker_rejects_unknown_settings_and_constants():
    import worker

    worker._use_code(str(ROOT))
    with pytest.raises(ValueError, match="unknown engine setting"):
        worker.patch_config("engine_v2: {}", {"no_such_setting": 1})
    out = worker.patch_config("engine_v2: {reserve_soc: 10}", {"switch_cost_p": 3})
    assert "switch_cost_p: 3" in out and "reserve_soc: 10" in out
    with pytest.raises(ValueError, match="no constant"):
        worker.apply_consts({"engine_v2.value.NOT_THERE": 1})
    assert "reserve_soc" in worker.catalogue(str(ROOT))


def test_viewer_adapter():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    run = subprocess.run(
        [node, str(ROOT / "tools" / "sim" / "viewer" / "test_adapt.cjs")], capture_output=True, text=True
    )
    assert run.returncode == 0, run.stderr + run.stdout


def test_named_variants_and_ranking():
    v = runner.parse_variant("no_leg@main:engine_v2.execute.Executor._leg_going=@false,reserve_soc=20")
    assert (v.name, v.code) == ("no_leg", "main")
    assert v.settings == {"reserve_soc": "20"} and v.consts == {"engine_v2.execute.Executor._leg_going": "@false"}
    assert runner.parse_variant("x", "ref").code == "ref"
    with pytest.raises(SystemExit):
        runner.parse_variant("x:oops")
    base = [fake_result("2026-10-07", -0.5, 1), fake_result("2026-10-08", -0.4)]
    cheaper = [fake_result("2026-10-07", -0.9, 4), fake_result("2026-10-08", -0.3)]
    text = scoreboard.ranking(
        {"base": base, "worse": cheaper, "gone": [{"day": "2026-10-07", "status": "failed"}]}, "base"
    )
    lines = text.splitlines()
    assert "worse" in text and "not run" in text and lines[1].startswith("variant")
    assert "-0.30" in text and "+3" in text  # total adj cost -0.4 + 0.1, three more flip-flops


def test_a_layer_can_be_stubbed_and_unknown_ones_are_refused():
    import worker

    worker._use_code(str(ROOT))
    from pe_core.engine_v2.execute import Executor

    original = Executor._worth_the_change
    try:
        worker.apply_consts({"engine_v2.execute.Executor._worth_the_change": "@true"})
        assert Executor._worth_the_change(None) is True
        with pytest.raises(ValueError, match="no constant"):
            worker.apply_consts({"engine_v2.execute.Executor._not_a_layer": "@true"})
        with pytest.raises(ValueError, match="no Nothing"):
            worker.apply_consts({"engine_v2.execute.Nothing.x": "@true"})
    finally:
        Executor._worth_the_change = original
