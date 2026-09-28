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
RAM = {"system": {"control_method": "ram_remote"}}
# a high write limit so the whole night is exercised (the real limit would pause control part-way)
WINDOWS = {"system": {"control_method": "timed_windows"}, "safety": {"max_writes_per_day": 500}}
SCENARIOS = {
    "ram": {"overrides": RAM},
    "windows": {"overrides": WINDOWS},
    # pause/resume, on both control methods
    "ram_pause": {
        "overrides": RAM, "start": "2026-09-27T19:30:00+00:00", "end": "2026-09-27T23:00:00+00:00",
        "events": [("2026-09-27T20:15:00+00:00", ("pause", True)), ("2026-09-27T20:45:00+00:00", ("pause", False))],
    },
    "windows_pause": {
        "overrides": WINDOWS, "start": "2026-09-27T19:30:00+00:00", "end": "2026-09-27T23:00:00+00:00",
        "events": [("2026-09-27T20:15:00+00:00", ("pause", True)), ("2026-09-27T20:45:00+00:00", ("pause", False))],
    },
    # an AppDaemon restart mid-run
    "ram_restart": {
        "overrides": RAM, "start": "2026-09-27T20:00:00+00:00", "end": "2026-09-27T22:30:00+00:00",
        "events": [("2026-09-27T21:10:00+00:00", ("restart",))],
    },
    # the inverter doesn't take a timed-window write: read-back retry, then control halted
    "windows_readback": {
        "overrides": WINDOWS, "start": "2026-09-27T19:30:00+00:00", "end": "2026-09-27T22:30:00+00:00",
        "events": [("2026-09-27T19:30:00+00:00", ("reject", "number.solis_timed_discharge_start_hours"))],
    },
    # the default (real) daily write limit: hits it and pauses (shortened to just past the limit, not the full night)
    "windows_limit": {
        "overrides": {"system": {"control_method": "timed_windows"}}, "end": "2026-09-28T03:25:00+00:00",
    },
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
    spec = SCENARIOS[name]
    got = Replay(fixture, tmp_path / "powerengine", powerengine, spec.get("overrides"),
                start=spec.get("start"), end=spec.get("end"), events=spec.get("events")).run()
    got = json.loads(json.dumps(got, default=str))
    golden = HERE / f"expected_{name}.json"
    if os.environ.get("PE_REPLAY_UPDATE") or not golden.exists():
        golden.write_text(json.dumps(got, indent=0) + "\n")
        pytest.skip(f"recorded {len(got)} entries to {golden.name}")
    want = json.loads(golden.read_text())
    assert got == want, "replay differs from the recording:\n" + _diff(got, want)
