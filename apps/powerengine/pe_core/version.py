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
