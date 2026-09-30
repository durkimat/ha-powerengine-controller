"""The card and app are released separately: each warns only if the other is older than its minimum."""

import json
import re
import sys
import types
from unittest import mock

from pe_core import __version__
from pe_core import names as nm
from pe_core.version import MIN_CARD_VERSION, card_warning, older_than, parse_version


def test_versions_compare_as_numbers():
    assert older_than("0.9.9", "0.9.10") is True
    assert older_than("0.9.70", "0.9.70") is False
    assert older_than("0.10.0", "0.9.70") is False
    assert older_than("0.9", "0.9.0") is False
    assert parse_version("v0.9.70") == (0, 9, 70)
    for bad in ("?", "unavailable", "", None, "0.9.70-beta"):
        assert older_than(bad, "0.9.70") is None
        assert older_than("0.9.70", bad) is None


def test_card_warning_only_when_older_than_minimum():
    assert card_warning("0.9.69") and MIN_CARD_VERSION in card_warning("0.9.69")
    assert card_warning(MIN_CARD_VERSION) is None
    assert card_warning("0.9.99") is None  # newer than the minimum: fine, even if not the app's version
    assert card_warning(None) is None and card_warning("?") is None


def test_minimum_is_a_real_version_no_newer_than_the_app():
    assert re.fullmatch(r"\d+\.\d+\.\d+", MIN_CARD_VERSION)
    assert older_than(__version__, MIN_CARD_VERSION) is False


def test_version_sensor_publishes_min_card_version():
    hassapi = types.ModuleType("appdaemon.plugins.hass.hassapi")

    class Hass:
        def __getattr__(self, name):
            raise AttributeError(name)

        def log(self, *a, **k):
            pass

    hassapi.Hass = Hass
    mods = {n: types.ModuleType(n) for n in ("appdaemon", "appdaemon.plugins", "appdaemon.plugins.hass")}
    mods["appdaemon.plugins.hass.hassapi"] = hassapi
    with mock.patch.dict(sys.modules, mods):
        sys.modules.pop("powerengine", None)
        import powerengine

        e = powerengine.PowerEngine.__new__(powerengine.PowerEngine)
        e.cfg = None
        sent = {}
        e._publisher_obj = types.SimpleNamespace(
            publish=lambda key, state, attrs=None: sent.__setitem__(key, (state, attrs))
        )
        e._publish_names()
        state, attrs = sent["diag_version"]
        attrs = json.loads(attrs) if isinstance(attrs, str) else attrs
        assert state == __version__
        assert attrs["min_card_version"] == MIN_CARD_VERSION
        assert len(json.dumps(attrs["min_card_version"])) < 20
        nm.set_current(None)  # _publish_names sets the neutral map; leave the defaults as found
        sys.modules.pop("powerengine", None)
