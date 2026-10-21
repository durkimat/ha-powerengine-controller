"""The "In use" sentences under the figures that can be measured or learned."""

from pe_core import inuse


def test_a_measured_capacity_that_is_used_says_so():
    e = inuse.capacity(18, 17.02, 400, True, 17.02, True, 14)
    assert e["text"] == "In use: 17.02 kWh, measured (you entered 18 kWh)." and e["source"] == "measured"


def test_a_capacity_not_yet_measured():
    assert inuse.capacity(18, None, 0, False, 18, True, 14)["text"] == "In use: 18 kWh, as entered (not measured yet)."


def test_a_capacity_is_not_used_for_the_reason_given():
    off = inuse.capacity(18, 17.02, 400, True, 18, False, 14)["text"]
    few = inuse.capacity(18, 17.02, 40, False, 18, True, 14)["text"]
    far = inuse.capacity(18, 9.0, 400, False, 18, True, 14)["text"]
    days = inuse.capacity(18, 17.02, 400, False, 18, True, 14)["text"]
    assert "Use measured is off" in off and "only 40 of the 100 readings" in few
    assert "outside 70% to 130%" in far and "fewer than 14 full days" in days
    assert all(t.startswith("In use: 18 kWh, as entered.") for t in (off, few, far, days))


def test_the_round_trip_shows_the_battery_and_the_grid_to_grid_figure():
    e = inuse.round_trip(0.95, 0.94, 0.97, 0.885, True, 14)
    assert e["text"].startswith("In use: 94%, measured (you entered 90.2%).")
    assert "78.3%, grid to grid" in e["text"]
    plain = inuse.round_trip(0.95, 0.94, 0.97, 0.97, True, 14)["text"]
    assert "grid to grid" not in plain


def test_a_measured_rate_only_shapes_the_plan():
    e = inuse.rate(4.8, 4.78, 4.78, True, False)
    assert "In use for the plan: 4.78 kW, measured (you entered 4.8 kW)" in e["text"]
    assert "still asked for 4.8 kW" in e["text"]
    assert "Use measured is off" in inuse.rate(4.8, 4.78, 4.8, False, False)["text"]
    assert "outside 50% to 120%" in inuse.rate(4.8, 1.0, 4.8, True, False)["text"]


def test_a_learned_figure_replaces_the_typed_one_or_says_why_not():
    assert inuse.learned("7.4 kW", "7.02 kW", "7.02 kW", "Learn: car charge rate", True, "x")["source"] == "learned"
    off = inuse.learned("7.4 kW", "7.02 kW", "7.4 kW", "Learn: car charge rate", False, "x")["text"]
    assert "switch is off" in off
    far = inuse.learned("7.4 kW", "30 kW", "7.4 kW", "s", True, "it is outside the range")["text"]
    assert "it is outside the range" in far
    assert "nothing learned yet" in inuse.learned("7.4 kW", None, "7.4 kW", "s", True, "x")["text"]
