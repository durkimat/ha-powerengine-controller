"""#175: a charge target reached early replans for the rest of the half-hour; every look is recorded."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from replay_harness import import_powerengine

from pe_core import earlytarget as et
from pe_core.decide import GRID_CHARGE, HOLD, SELF_USE, Decision
from pe_core.forecast import SLOT, Slot
from pe_core.planner import PlanSlot

SLOT0 = datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc)


def test_evaluate():
    assert et.evaluate(20, True) is None
    assert et.evaluate(4.9, True) == "late"
    assert et.evaluate(20, False) == "no_plan"


def test_one_look_per_half_hour_and_saved(tmp_path):
    path = str(tmp_path / "e.json")
    e = et.EarlyTargets(path)
    assert e.first_look("a") and not e.first_look("a") and e.first_look("b")
    e.record({"t": "x", "outcome": "late", "left_min": 3.0}, replanned=False)
    e.record({"t": "y", "outcome": "replanned_changed", "left_min": 18.0, "new_action": "export",
              "slot_end": (SLOT0 + et.SLOT).isoformat()}, replanned=True)
    again = et.EarlyTargets(path)
    assert [r["outcome"] for r in again.records] == ["late", "replanned_changed"]
    s = again.summary()
    assert s["count"] == 2 and s["by_outcome"]["late"]["count"] == 1 and s["replan_chose"] == {"export": 1}


def test_decisions_after_a_replan_are_noted_until_the_slot_ends():
    e = et.EarlyTargets()
    rec = {"t": "y", "outcome": "replanned_changed", "left_min": 18.0, "new_action": "export",
           "slot_end": (SLOT0 + et.SLOT).isoformat()}
    e.record(rec, replanned=True)
    e.note_decision(SLOT0 + timedelta(minutes=14), "export", "plan", None)          # same as the new action: nothing
    e.note_decision(SLOT0 + timedelta(minutes=20), "hold", "plan", None)
    e.note_decision(SLOT0 + timedelta(minutes=31), "self_use", "plan", None)        # next half-hour: not counted
    assert [a["action"] for a in rec["after"]] == ["hold"]


def app(monkeypatch, new, soc=91.0, now=SLOT0 + timedelta(minutes=12)):
    pe = import_powerengine(monkeypatch)
    slots = [PlanSlot(Slot(SLOT0 + i * SLOT, 0.0666, 0.15), GRID_CHARGE if i == 0 else "export", "") for i in range(3)]
    stub = SimpleNamespace(_early=et.EarlyTargets(), plan=SimpleNamespace(slots=slots), cfg=None, tz=None,
                           mode=SimpleNamespace(effective="active"), replans=[], logs=[],
                           _active_override=lambda: None)
    stub._maybe_replan = lambda r, force=False: stub.replans.append(force)
    stub._bridge_data_gap = lambda d, n: d
    stub.log = lambda msg, level="INFO": stub.logs.append(msg)
    monkeypatch.setattr(pe, "decide", lambda *a, **k: new)
    r = SimpleNamespace(now=now, battery_soc=soc, import_rate=0.0666, ev_state=lambda: "idle")
    held = Decision(HOLD, "plan", "reached the 91% charge target for this half-hour: holding until the next one",
                    target_soc=91.0, details={"reached": {"slot": SLOT0.isoformat(), "target": 91.0}})
    return pe, stub, r, held


def test_early_target_replans_and_takes_the_new_decision(monkeypatch):
    new = Decision("export", "plan", "sell at 15p, buy back at 6.66p from 13:30")
    pe, stub, r, held = app(monkeypatch, new)
    out = pe.PowerEngine._early_target(stub, r, held)
    assert out is new and stub.replans == [True]
    rec = stub._early.records[0]
    assert rec["outcome"] == "replanned_changed" and rec["new_action"] == "export" and rec["left_min"] == 18.0
    assert rec["next_action"] == "export" and rec["price_p"] == 6.66
    # the same hold on the next cycles is not looked at again
    assert pe.PowerEngine._early_target(stub, r, held) is held and stub.replans == [True]
    assert len(stub._early.records) == 1


def test_a_replan_that_holds_again_is_recorded_as_such(monkeypatch):
    again = Decision(HOLD, "plan", "reached the 92% charge target for this half-hour: holding until the next one",
                     details={"reached": {"slot": SLOT0.isoformat(), "target": 92.0}})
    pe, stub, r, held = app(monkeypatch, again)
    assert pe.PowerEngine._early_target(stub, r, held) is again
    assert stub._early.records[0]["outcome"] == "replanned_still_hold"


def test_too_little_time_left_keeps_the_hold_and_says_why(monkeypatch):
    pe, stub, r, held = app(monkeypatch, Decision(SELF_USE, "plan", "x"), now=SLOT0 + timedelta(minutes=27))
    assert pe.PowerEngine._early_target(stub, r, held) is held and stub.replans == []
    assert stub._early.records[0]["outcome"] == "late" and stub._early.records[0]["left_min"] == 3.0


def test_other_decisions_pass_straight_through(monkeypatch):
    pe, stub, r, _ = app(monkeypatch, None)
    plain = Decision(SELF_USE, "plan", "the battery covers the house")
    assert pe.PowerEngine._early_target(stub, r, plain) is plain and not stub._early.records
    json.dumps(stub._early.summary())


def test_the_summary_tool_reads_the_export_section():
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent / "tools"))
    import diag_summary
    e = et.EarlyTargets()
    e.record({"t": "2026-10-04T10:12:00+00:00", "outcome": "late", "left_min": 3.0}, replanned=False)
    export = {"generated": "2026-10-04T10:20:00Z", "app": {"early_target": {**e.summary(), "records": e.records}}}
    out = diag_summary.summarise(export)["early_target"]
    assert out["count"] == 1 and out["by_outcome"]["late"]["count"] == 1
    assert "early targets (#175), 1" in diag_summary.render(diag_summary.summarise(export))


def test_plan_signature_changes_with_the_car_and_the_half_hour_while_it_charges(monkeypatch):
    pe = import_powerengine(monkeypatch)
    stub = SimpleNamespace(profile=None, cfg=SimpleNamespace(safety={}, features={}, system={}),
                           _active_override=lambda: None)
    def sig(state, minute):
        r = SimpleNamespace(rates=[], dispatches=[], axle_start=None, axle_end=None, free_start=None, free_end=None,
                            now=datetime(2026, 10, 4, 19, minute, tzinfo=timezone.utc), ev_state=lambda: state)
        return pe.PowerEngine._plan_signature(stub, r)
    assert sig("plugged_in", 5) == sig("plugged_in", 35)                  # not charging: the half-hour doesn't matter
    assert sig("plugged_in", 5) != sig("charging", 5)                     # starts
    assert sig("charging", 5) == sig("charging", 20)                      # same half-hour
    assert sig("charging", 5) != sig("charging", 35)                      # still charging next half-hour: re-plan
