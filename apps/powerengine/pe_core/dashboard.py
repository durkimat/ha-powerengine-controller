"""Keep the managed dashboard file in HA's config folder up to date."""

from __future__ import annotations

import os

# Shipped as .lovelace, not .yaml: AppDaemon would try to load a .yaml here as app config.
SOURCE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dashboard", "dashboard.lovelace")


def sync_dashboard(target: str, source: str = SOURCE) -> bool:
    """Copy the shipped dashboard to `target` if it differs. Returns True if written."""
    with open(source, encoding="utf-8") as fh:
        wanted = fh.read()
    try:
        with open(target, encoding="utf-8") as fh:
            if fh.read() == wanted:
                return False
    except FileNotFoundError:
        pass
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(wanted)
    os.replace(tmp, target)
    return True
