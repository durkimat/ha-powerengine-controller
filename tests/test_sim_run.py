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
