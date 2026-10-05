"""The overnight window setting: learned from the rates (default) or the owner's fixed times (0.9.105).

tests/test_overnight_window.py covers how the learned window is worked out."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from replay_harness import import_powerengine

from pe_core.config import SAFETY, SYSTEM_CHOICES, SYSTEM_DEFAULTS, ConfigError, parse_config, settings_catalogue
from pe_core.costbook import CostBook
from pe_core.tariff import chosen_window, describe_window, fixed_window, overnight_window

NIGHT = {t for t in range(0, 10)} | {47}                 # 23:30 to 05:00 learned


# --- the pure helpers -----------------------------------------------------------------------------------

@pytest.mark.parametrize("start,end,first,last,count", [
    (23.5, 5.5, 47, 10, 12),           # over midnight
    (0.5, 5.5, 1, 10, 10),
    (0, 5, 0, 9, 10),
    (22, 24, 44, 47, 4),               # to midnight
])
def test_fixed_window_half_hours(start, end, first, last, count):
    w = fixed_window(start, end)
    assert len(w) == count and first in w and last in w and (last + 1) % 48 not in w


@pytest.mark.parametrize("start,end", [(1, 1), (5, 23.3), (-1, 3), (2, 25), (0.25, 3)])
def test_a_start_and_end_that_make_no_window_give_an_empty_set(start, end):
    assert fixed_window(start, end) == set()


def test_zero_to_twenty_four_is_the_whole_day():
    assert len(fixed_window(0, 24)) == 48 and describe_window(fixed_window(0, 24)) == "all day"


def test_describe_window_reads_as_times():
    assert describe_window(fixed_window(23.5, 5.5)) == "23:30–05:30"
    assert describe_window({10, 11, 47, 0, 1}) == "05:00–06:00, 23:30–01:00"
    assert describe_window(set()) == "none yet"


def test_fixed_wins_over_learned_and_an_empty_fixed_falls_back():
    history = {"2026-10-01": sorted(NIGHT), "2026-10-02": sorted(NIGHT)}
    assert chosen_window(history) == NIGHT == overnight_window(history)
    assert chosen_window(history, fixed_window(0, 5)) == fixed_window(0, 5)
    assert chosen_window(history, fixed_window(1, 1)) == NIGHT
    assert chosen_window({}, None) == set()


# --- the settings ---------------------------------------------------------------------------------------

def test_defaults_are_learned_and_the_catalogue_offers_both():
    assert SYSTEM_DEFAULTS["overnight_window"] == "learned"
    assert [v for v, _ in SYSTEM_CHOICES["overnight_window"][1]] == ["learned", "fixed"]
    cat = settings_catalogue()
    assert any(x["key"] == "overnight_window" and x["options"] for x in cat["system"])
    keys = {x["key"] for x in cat["safety"]}
    assert {"overnight_start_h", "overnight_end_h"} <= keys
    assert {"overnight_start_h", "overnight_end_h"} <= {k for sec in cat["sections"] for k in sec["keys"]}
    assert SAFETY["overnight_start_h"][0] == 23.5 and SAFETY["overnight_end_h"][0] == 5.5


def test_a_fixed_window_parses_and_bad_values_are_refused():
    cfg = parse_config({"system": {"overnight_window": "fixed"}, "safety": {"overnight_start_h": 0.5,
                                                                            "overnight_end_h": 5.5}})
    assert cfg.system["overnight_window"] == "fixed"
    with pytest.raises(ConfigError, match="on the hour or half past"):
        parse_config({"safety": {"overnight_start_h": 23.3}})
    with pytest.raises(ConfigError, match="differ"):
        parse_config({"system": {"overnight_window": "fixed"}, "safety": {"overnight_start_h": 3,
                                                                          "overnight_end_h": 3}})
    parse_config({"safety": {"overnight_start_h": 3, "overnight_end_h": 3}})        # learned: the times are unused
    with pytest.raises(ConfigError, match="must be one of"):
        parse_config({"system": {"overnight_window": "sometimes"}})


# --- the cost book --------------------------------------------------------------------------------------

def test_the_cost_book_uses_the_fixed_window_when_set(tmp_path):
    book = CostBook(str(tmp_path), None)
    book.cheap_history = {"2026-10-01": sorted(NIGHT), "2026-10-02": sorted(NIGHT)}
    assert book.window() == NIGHT
    book.fixed_window = fixed_window(0, 5)
    assert book.window() == fixed_window(0, 5)
    book.fixed_window = None
    assert book.window() == NIGHT


# --- the app --------------------------------------------------------------------------------------------

def stub(monkeypatch, system, safety=None, history=None, tmp_path=None):
    pe = import_powerengine(monkeypatch)
    cfg = parse_config({"system": system, "safety": safety or {}})
    book = CostBook(str(tmp_path), None)
    book.cheap_history = history or {}
    s = SimpleNamespace(cfg=cfg, costbook=book, published=[])
    s._fixed_window = lambda: pe.PowerEngine._fixed_window(s)
    s._publish_if_changed = lambda key, state, attrs: s.published.append((key, state, attrs))
    return pe, s


def test_the_app_uses_learned_by_default_and_fixed_when_chosen(monkeypatch, tmp_path):
    history = {"2026-10-01": sorted(NIGHT), "2026-10-02": sorted(NIGHT)}
    pe, s = stub(monkeypatch, {}, history=history, tmp_path=tmp_path)
    assert pe.PowerEngine._fixed_window(s) is None
    assert pe.PowerEngine._sync_window(s) is False and pe.PowerEngine._overnight(s) == NIGHT
    pe, s = stub(monkeypatch, {"overnight_window": "fixed"}, {"overnight_start_h": 0, "overnight_end_h": 5},
                 history, tmp_path)
    assert pe.PowerEngine._sync_window(s) is True                      # the window in use changed
    assert pe.PowerEngine._overnight(s) == fixed_window(0, 5) and s.costbook.fixed_window == fixed_window(0, 5)
    assert pe.PowerEngine._sync_window(s) is False                     # already in step


def test_the_app_without_a_cost_book_still_honours_a_fixed_window(monkeypatch, tmp_path):
    pe, s = stub(monkeypatch, {"overnight_window": "fixed"}, {"overnight_start_h": 23, "overnight_end_h": 5},
                 tmp_path=tmp_path)
    s.costbook = None
    assert pe.PowerEngine._overnight(s) == fixed_window(23, 5)
    pe, s = stub(monkeypatch, {}, tmp_path=tmp_path)
    s.costbook = None
    assert pe.PowerEngine._overnight(s) == set()


def test_the_readout_says_which_is_in_use_and_what_was_learned(monkeypatch, tmp_path):
    history = {"2026-10-01": sorted(NIGHT), "2026-10-02": sorted(NIGHT)}
    pe, s = stub(monkeypatch, {"overnight_window": "fixed"}, {"overnight_start_h": 0.5, "overnight_end_h": 5.5},
                 history, tmp_path)
    pe.PowerEngine._sync_window(s)
    pe.PowerEngine._publish_overnight(s, pe.PowerEngine._overnight(s))
    key, state, attrs = s.published[-1]
    assert key == "diag_overnight" and state == "00:30–05:30"
    assert attrs["source"] == "fixed" and attrs["fixed"] == "00:30–05:30"
    assert attrs["learned"] == "23:30–05:00" and attrs["learned_days"] == 2 and not attrs["fixed_not_valid"]
    pe, s = stub(monkeypatch, {}, history=history, tmp_path=tmp_path)
    pe.PowerEngine._publish_overnight(s, pe.PowerEngine._overnight(s))
    assert s.published[-1][1] == "23:30–05:00" and s.published[-1][2]["source"] == "learned"
    assert s.published[-1][2]["fixed"] is None


def test_the_plan_signature_includes_the_system_settings(monkeypatch):
    pe = import_powerengine(monkeypatch)

    def sig(system):
        s = SimpleNamespace(profile=None, cfg=SimpleNamespace(safety={}, features={}, system=system),
                            _active_override=lambda: None)
        r = SimpleNamespace(rates=[], dispatches=[], axle_start=None, axle_end=None, free_start=None, free_end=None,
                            now=datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc), ev_state=lambda: "idle")
        return pe.PowerEngine._plan_signature(s, r)
    assert sig({"overnight_window": "learned"}) != sig({"overnight_window": "fixed"})
