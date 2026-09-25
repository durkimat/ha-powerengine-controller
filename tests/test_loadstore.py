from datetime import datetime, timedelta, timezone

import pytest

from pe_core.forecast import (
    DEFAULT_LOAD_W,
    LoadProfile,
    build_slots,
    flatten_history,
    parse_history,
    profile_from_means,
)
from pe_core.loadstore import KEEP_DAYS, LoadStore

UTC = timezone.utc
T = datetime(2026, 9, 25, 10, 0, tzinfo=UTC)


def feed(store, start, minutes, watts, step_s=30):
    done = []
    for k in range(int(minutes * 60 / step_s) + 1):
        if store.add(start + timedelta(seconds=k * step_s), watts):
            done.append(k)
    return done


def test_half_hour_mean_recorded_when_slot_completes():
    st = LoadStore()
    feed(st, T, 29.5, 800)
    assert st.means == {}
    feed(st, T + timedelta(minutes=30), 1, 2000)
    assert st.means[T] == pytest.approx(800, abs=1)


def test_short_slots_are_not_recorded():
    st = LoadStore()
    feed(st, T + timedelta(minutes=25), 4, 800)       # only ~4 minutes in the 10:00 slot
    feed(st, T + timedelta(minutes=30), 1, 800)
    assert T not in st.means


def test_save_load_roundtrip_and_prune(tmp_path):
    path = str(tmp_path / "load_history.json")
    st = LoadStore(path)
    st.means = {T - timedelta(days=KEEP_DAYS + 2): 100.0, T: 900.0}
    feed(st, T + timedelta(minutes=30), 31, 500)       # completes a slot -> prunes old
    st.save()
    again = LoadStore(path)
    assert again.load() == len(st.means)
    assert all(k >= T - timedelta(days=KEEP_DAYS) for k in again.means)


def test_profile_from_means():
    means = {T - timedelta(days=d): 1200.0 for d in range(1, 8)}
    prof = profile_from_means(means, T, UTC)
    assert prof.expected_w(T, UTC) == pytest.approx(1200.0)
    assert prof.days == pytest.approx(7 / 48)


@pytest.mark.parametrize("shape", ["list_of_lists", "flat", "dict"])
def test_history_shapes(shape):
    rows = [{"state": "500", "last_changed": "2026-09-25T10:00:00+00:00", "attributes": {"unit_of_measurement": "W"}},
            {"state": "700", "last_changed": "2026-09-25T10:10:00+00:00"}]
    result = {"list_of_lists": [rows], "flat": rows, "dict": {"sensor.x": rows}}[shape]
    assert len(flatten_history(result)) == 2
    assert [v for _, v in parse_history(result)] == [500.0, 700.0]


def test_minimal_response_unit_carries_forward():
    rows = [[{"state": "1.5", "last_changed": "2026-09-25T10:00:00+00:00", "attributes": {"unit_of_measurement": "kW"}},
             {"state": "2.0", "last_changed": "2026-09-25T10:10:00+00:00"}]]
    assert [v for _, v in parse_history(rows)] == [1500.0, 2000.0]


def test_no_profile_uses_steady_default_not_live_power():
    from fixtures import BST, CONFIG, NOW, get_state

    from pe_core.readings import read
    r = read(CONFIG, get_state(), NOW)
    r.house_power = 3200                               # a kettle-and-oven moment
    slots = build_slots(r, [], LoadProfile(), BST)
    assert slots[0].load_kwh == pytest.approx(DEFAULT_LOAD_W / 1000 * 0.5)


def test_unit_passed_in_when_rows_have_no_attributes():
    rows = [[{"state": "1.2", "last_changed": "2026-09-25T10:00:00+00:00"}]]
    assert [v for _, v in parse_history(rows, unit="kW")] == [1200.0]
    assert [v for _, v in parse_history(rows)] == [1.2]
