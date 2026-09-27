

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
