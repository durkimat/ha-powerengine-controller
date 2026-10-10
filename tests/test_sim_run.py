"""A real day through the simulator, against the owner's archive. Skipped where there is none (CI). About 2 minutes."""

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "sim"))
import runner  # noqa: E402
import workspace  # noqa: E402

DATA = workspace.default_data()


def tree(folder):
    """Every file under the archive with its size and modification time."""
    return sorted((str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in folder.rglob("*") if p.is_file())


pytestmark = pytest.mark.skipif(not workspace.days_with_snapshots(DATA), reason="no archive with snapshots")


def test_one_day_runs_caches_and_leaves_the_archive_alone(tmp_path):
    before = tree(DATA)
    ws = runner.Workspace(DATA, tmp_path / "work")
    day = workspace.days_with_snapshots(DATA)[1]
    job = ws.job(runner.Variant("base"), day)
    res = runner.run_jobs(ws, [job], 1)[job["key"]]
    assert res["status"] == "ok", res
    s = res["score"]
    assert s["flip_flops"] is not None and s["errors"] == 0 and res["series"]["plans"]
    again = runner.run_jobs(ws, [job], 1)[job["key"]]
    assert again["score"] == s  # from the cache
    assert tree(DATA) == before, "the run wrote into the archive"


def test_a_synthetic_day_can_be_made_from_a_real_one(tmp_path):
    import json
    from datetime import date

    import synth

    template = next((d for d in workspace.days_with_snapshots(DATA) if date.fromisoformat(d).weekday() == 3), None)
    if template is None:
        pytest.skip("the archive has no Thursday with a snapshot")
    day = "2026-09-03"
    info = synth.make(template, day, DATA, tmp_path, 4.0, synth.parse_slots("09:00-12:00,13:00-16:00"), 6.66, 1.0)
    assert (
        info["slot_half_hours"] == 12 and 15 < info["solar_kwh"] < 22 and info["peak_kw"] == pytest.approx(4.0, abs=0.1)
    )
    records = json.loads((tmp_path / "costs" / f"{day}.json").read_text())
    assert len(records) == 48 and all(r["synthetic"] and not r["axle"] and r["car"] == 0 for r in records)
    assert [r["v"]["act"] for r in records][18:24] == [0.0666] * 6 and records[24]["v"][
        "act"
    ] > 0.2  # 09:00-12:00, then dear
    snap = json.loads((tmp_path / "costs" / "snapshots" / f"{day}.json").read_text())
    assert snap["day"] == day and snap["entries"][0]["at"].startswith(day)
    with pytest.raises(SystemExit, match="weekday"):
        synth.make(template, "2026-09-04", DATA, tmp_path, 4.0, [], 6.66, 1.0)
