

def test_attribute_sizes_warn_once_and_rank():
    from pe_core import diagnostics as dg
    sizes = {}
    assert not dg.track_attr_size(sizes, "plan", 9000)
    assert dg.track_attr_size(sizes, "plan", 15600)           # first time past the warning line
    assert not dg.track_attr_size(sizes, "plan", 15700)       # not again
    dg.track_attr_size(sizes, "activity", 4000)
    rows = dg.largest_attrs(sizes)
    assert rows[0]["sensor"] == "plan" and rows[0]["peak_bytes"] == 15700 and rows[0]["limit_bytes"] == 16384
    assert rows[1]["sensor"] == "activity"


def test_log_ring_saves_loads_and_publishes(tmp_path):
    from datetime import datetime, timezone

    from pe_core.diagnostics import LogRing
    t = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
    ring = LogRing(size=5)
    for i in range(3):
        ring.add(t, "INFO", f"line {i}")
    ring.add(t, "WARNING", "careful")
    path = str(tmp_path / "log.json")
    ring.save(path)
    assert not ring.changed
    fresh = LogRing(size=5)
    fresh.add(t, "INFO", "started again")
    fresh.load(path)
    assert [x["msg"] for x in fresh.lines] == ["line 0", "line 1", "line 2", "careful", "started again"]
    pub = fresh.published(recent=2, warnings=5, width=4)
    assert [x["m"] for x in pub["recent"]] == ["star", "care"]            # newest first, trimmed
    assert pub["warnings"] == [{"t": t.isoformat(timespec="seconds"), "l": "W", "m": "care"}]


def test_health_finding_keys_and_ledger_threshold():
    from pe_core.health import data_findings, finding_key
    a = {"level": "warning", "title": "Sun 27 Sep: battery ledger corrected by -3.0 kWh"}
    b = {"level": "warning", "title": "Mon 28 Sep: battery ledger corrected by -3.0 kWh"}
    assert finding_key(a) == finding_key(dict(a)) and finding_key(a) != finding_key(b)
    def recs(corr, through):
        return [{"v": {"correction_kwh": corr}, "battery_in": through / 2, "battery_out": through / 2,
                 "solar": 0, "grid_import": 0, "house": 0, "car": 0, "grid_export": 0}]
    titles = lambda rs: [f["title"] for f in data_findings(rs, "Sun 27 Sep")]  # noqa: E731
    assert any("ledger" in t for t in titles(recs(-3.5, 10)))             # small day: over 3 kWh flags
    assert not any("ledger" in t for t in titles(recs(-3.5, 40)))         # busy day: under 10% of 40 kWh
    assert any("ledger" in t for t in titles(recs(-4.5, 40)))
