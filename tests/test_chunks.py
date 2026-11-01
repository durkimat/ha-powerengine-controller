"""A catalogue too big for one sensor is split across numbered ones and joins back unchanged."""
import copy

import pytest

from pe_core import chunks
from pe_core.config import settings_catalogue
from pe_core.entities import ENTITIES
from pe_core.roles import catalogue

LIMIT_OK = 15500        # HA's recorder limit is 16384


def _grown(attrs, times):
    """The catalogue as it would be after growing `times` over: every list repeated, items renamed so they differ."""
    out = copy.deepcopy(attrs)
    for k, v in attrs.items():
        if isinstance(v, list):
            out[k] = [dict(i, key=f"{i.get('key', 'x')}_{n}") if isinstance(i, dict) else i
                      for n in range(times) for i in v]
    return out


@pytest.mark.parametrize("make", [catalogue, settings_catalogue])
def test_today_each_catalogue_is_still_one_sensor(make):
    parts = chunks.split(make())
    assert len(parts) == 1 and "parts" not in parts[0]


@pytest.mark.parametrize("make", [catalogue, settings_catalogue])
@pytest.mark.parametrize("times", [2, 3])
def test_a_grown_catalogue_splits_and_joins_back(make, times):
    attrs = _grown(make(), times)
    parts = chunks.split(attrs)
    assert len(parts) > 1 and parts[0]["parts"] == len(parts) <= chunks.MAX_PARTS
    assert all(chunks.size(p) < LIMIT_OK for p in parts)
    assert chunks.join(parts) == attrs


def test_every_part_carries_every_list_key():
    parts = chunks.split(_grown(settings_catalogue(), 2))
    assert all({"safety", "system", "sections"} <= set(p) for p in parts)


def test_non_list_keys_stay_on_the_first_part():
    attrs = {"note": "hello", "roles": [{"key": f"r{i}", "d": "x" * 200} for i in range(200)]}
    parts = chunks.split(attrs)
    assert parts[0]["note"] == "hello" and all("note" not in p for p in parts[1:])


def test_one_huge_item_is_not_lost():
    attrs = {"roles": [{"key": "big", "d": "x" * 20000}, {"key": "small"}]}
    parts = chunks.split(attrs)
    assert chunks.join(parts)["roles"] == attrs["roles"]


def test_part_sensors_exist_for_both_catalogues():
    keys = {e.key for e in ENTITIES}
    for base in ("map_catalogue", "map_settings"):
        assert all(chunks.part_key(base, n) in keys for n in range(1, chunks.MAX_PARTS + 1))
