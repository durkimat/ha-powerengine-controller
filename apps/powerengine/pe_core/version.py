"""Which PowerEngine version is on disk (HACS may have installed a newer one than is running)."""

from __future__ import annotations

import re

_VERSION = re.compile(r'^__version__\s*=\s*"([^"]+)"', re.M)


def installed_version(path: str) -> str | None:
    """The __version__ written in pe_core/__init__.py at `path`, or None if it can't be read."""
    try:
        with open(path, encoding="utf-8") as fh:
            m = _VERSION.search(fh.read())
    except OSError:
        return None
    return m.group(1) if m else None


# The oldest card this app works with, published as `min_card_version` on sensor.pe_diag_version. 0.9.86 is the card
# with the Plan history day picker and its previous/next arrows (the dashboard's tab names it). Raise it only
# when the app starts to need something a newer card does. The card holds the matching MIN_APP_VERSION for the
# other direction.
MIN_CARD_VERSION = "0.9.86"

_DOTTED = re.compile(r"^\s*v?(\d+(?:\.\d+)*)\s*$")


def parse_version(v) -> tuple[int, ...] | None:
    """"0.9.70" -> (0, 9, 70); None when it isn't a plain dotted number."""
    m = _DOTTED.match(str(v)) if v is not None else None
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def older_than(a, b) -> bool | None:
    """True if version a is older than b, False if equal or newer, None if either can't be read (so: no warning)."""
    x, y = parse_version(a), parse_version(b)
    if x is None or y is None:
        return None
    n = max(len(x), len(y))
    return x + (0,) * (n - len(x)) < y + (0,) * (n - len(y))


def card_warning(card_version, minimum: str = MIN_CARD_VERSION) -> str | None:
    """A plain-words warning when the card is older than the app's minimum; None otherwise (differing is fine)."""
    if older_than(card_version, minimum) is True:
        return f"The card ({card_version}) is older than the {minimum} this app needs. Update the card and reload."
    return None
