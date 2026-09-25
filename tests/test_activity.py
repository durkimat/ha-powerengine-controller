from datetime import timedelta

from fixtures import BST, NOW

from pe_core.activity import MAX_ENTRIES, ActivityLog
from pe_core.decide import GRID_CHARGE, HOLD, Decision


def test_records_only_changes_newest_first():
    log = ActivityLog()
    a = Decision(GRID_CHARGE, "cheap_rate", "import is cheap", target_soc=100)
    assert log.record(a, NOW, True, BST)["text"].startswith("Would grid-charge to 100%")
    assert log.record(a, NOW + timedelta(minutes=1), True, BST) is None
    assert log.record(Decision(HOLD, "cheap_rate", "full"), NOW + timedelta(minutes=2), True, BST)
    assert [e["action"] for e in log.entries] == ["hold", "grid_charge"]
    assert log.entries[1]["hhmm"] == "17:58"


def test_capped_and_restorable():
    log = ActivityLog()
    for i in range(MAX_ENTRIES + 5):
        log.record(Decision(HOLD, f"r{i}", "x"), NOW, True)
    assert len(log.entries) == MAX_ENTRIES
    again = ActivityLog(log.entries)
    assert again.record(Decision(HOLD, f"r{MAX_ENTRIES + 4}", "x"), NOW, True) is None
