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


def test_a_withdrawn_slot_and_an_announced_event_reach_the_snapshot(tmp_path):
    import json
    from datetime import date

    import synth

    template = next((d for d in workspace.days_with_snapshots(DATA) if date.fromisoformat(d).weekday() == 3), None)
    if template is None:
        pytest.skip("the archive has no Thursday with a snapshot")
    slots = synth.parse_slots("09:00-12:00,13:00-16:00")
    info = synth.make(
        template,
        "2026-08-20",
        DATA,
        tmp_path,
        4.0,
        slots,
        6.66,
        1.0,
        events=[(18.0, 19.0)],
        announce_h=13.0,
        withdraw=[(13.0, 16.0, 12 + 5 / 60)],
    )
    assert info["slot_half_hours"] == 6 and info["event_half_hours"] == 2  # only the morning slot happens
    recs = json.loads((tmp_path / "costs" / "2026-08-20.json").read_text())
    assert recs[27]["v"]["act"] > 0.2 and not recs[27]["v"]["slot"] and recs[19]["v"]["slot"]
    snap = json.loads((tmp_path / "costs" / "snapshots" / "2026-08-20.json").read_text())
    roles = snap["roles"]
    entries = sorted(snap["entries"], key=lambda e: e["at"])
    disp = [e for e in entries if roles["smart_dispatches"] in (e.get("states") or {})]
    before = next(e for e in reversed(disp) if e["at"] < "2026-08-20T12:05")
    after = next(e for e in disp if e["at"] >= "2026-08-20T12:05")
    assert before["states"][roles["smart_dispatches"]]["attributes"][
        "planned_dispatches"
    ]  # the afternoon slot is planned
    assert after["states"][roles["smart_dispatches"]]["attributes"]["planned_dispatches"] == []  # then taken back
    ann = next(e for e in entries if roles["axle_event_start"] in (e.get("states") or {}) and e["at"] > "2026-08-20T01")
    assert ann["at"].startswith("2026-08-20T13:00") and ann["states"][roles["axle_event_start"]]["state"].startswith(
        "2026-08-20T17:00"
    )
