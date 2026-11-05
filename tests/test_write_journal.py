"""The write journal keeps three days."""


def test_write_journal_keeps_three_days(tmp_path):
    from datetime import datetime, timedelta, timezone

    from pe_core.journal import WriteJournal
    j = WriteJournal(str(tmp_path / "j.json"))
    t = datetime(2026, 9, 26, 12, tzinfo=timezone.utc)
    j.add(t - timedelta(days=4), "number.a", 1, 0, "old")
    j.add(t, "number.a", 2, 1, "three windows: grid_charge (plan)")
    j.save(t)
    again = WriteJournal(str(tmp_path / "j.json"))
    assert [e["why"] for e in again.entries] == ["three windows: grid_charge (plan)"]
