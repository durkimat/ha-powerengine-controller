"""The forecast snapshots the app writes (pe_core/fcsnap.py) are what the comparison reads
(pe_core/compare/snapfeed.py): the same states at every time, the house profile and first-seen
times, and no refusal for a day recorded from midnight."""

import json
from datetime import date, datetime, time, timedelta

from compare_support import LONDON, snapshot_from_pack

from pe_core import fcsnap
from pe_core.compare import snapfeed
from pe_core.demo.pack import load_pack

DAY = date(2026, 10, 6)


def _replay_through_writer(tmp_path, synthetic):
    """Feed the synthetic snapshot's states, entry by entry, through the app's writer; return the written file."""
    writer = fcsnap.SnapshotWriter(str(tmp_path), LONDON)
    seen, profile0 = {}, synthetic["entries"][0]["profile"]
    for e in synthetic["entries"]:
        seen.update(e["states"])
        at = datetime.fromisoformat(e["at"])
        writer.observe(at, synthetic["roles"], dict(seen), profile=fcsnap.to_load_profile(profile0),
                       first_seen={"2026-10-06T23:30:00+01:00": "2026-10-06T19:02:00+01:00"})
    path = tmp_path / f"{DAY.isoformat()}.json"
    return json.loads(path.read_text())


def test_what_the_app_writes_is_what_the_comparison_reads(tmp_path):
    synthetic = snapshot_from_pack(load_pack(), "sunny", DAY)
    written = _replay_through_writer(tmp_path, synthetic)
    snap = snapfeed.Snapshot(written)
    start = datetime.combine(DAY, time(0), tzinfo=LONDON)
    assert snap.refusal(start) is None
    for i in range(0, 48, 3):
        t = start + timedelta(minutes=30 * i + 1)
        want = snapfeed.Snapshot(synthetic).states_at(t)
        assert snap.states_at(t) == want
        assert fcsnap.states_at(written, t) == want
    prof = snap.profile_at(start + timedelta(hours=12))
    want_w = fcsnap.to_load_profile(synthetic["entries"][0]["profile"]).watts
    assert prof is not None and prof.watts.keys() == want_w.keys()
    assert all(abs(prof.watts[k] - want_w[k]) < 0.06 for k in want_w)          # the writer keeps 0.1 W
    assert snap.first_seen_at(start + timedelta(hours=23)) == {"2026-10-06T23:30:00+01:00": "2026-10-06T19:02:00+01:00"}
