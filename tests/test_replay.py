"""Replay safety net: a recorded day through the whole app must produce the same plans, decisions and inverter writes.

If a change is meant to alter behaviour, re-record the expected results and review the difference:

    PE_REPLAY_UPDATE=1 pytest tests/test_replay.py
    git diff tests/replay/

Refactoring (moving code into adapters) must pass this unchanged.
"""

import json
import os
from pathlib import Path

import pytest
from replay_harness import Replay, load_app

HERE = Path(__file__).parent / "replay"
FIXTURE = HERE / "day_2026_09_27.json"
SCENARIOS = {
    "ram": {"system": {"control_method": "ram_remote"}},
    # a high write limit so the whole night is exercised (the real limit would pause control part-way)
    "windows": {"system": {"control_method": "timed_windows"}, "safety": {"max_writes_per_day": 500}},
}


def _diff(got, want, n=8):
    out = []
    for i, (g, w) in enumerate(zip(got, want, strict=False)):
        if g != w:
            out.append(f"#{i}\n  got:  {g}\n  want: {w}")
            if len(out) >= n:
                break
    if len(got) != len(want):
        out.append(f"length: got {len(got)}, want {len(want)}")
    return "\n".join(out)


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_replay_matches_the_recording(name, monkeypatch, tmp_path):
    powerengine = load_app(monkeypatch)
    fixture = json.loads(FIXTURE.read_text())
    got = Replay(fixture, tmp_path / "powerengine", powerengine, SCENARIOS[name]).run()
    got = json.loads(json.dumps(got, default=str))
    golden = HERE / f"expected_{name}.json"
    if os.environ.get("PE_REPLAY_UPDATE") or not golden.exists():
        golden.write_text(json.dumps(got, indent=0) + "\n")
        pytest.skip(f"recorded {len(got)} entries to {golden.name}")
    want = json.loads(golden.read_text())
    assert got == want, "replay differs from the recording:\n" + _diff(got, want)
