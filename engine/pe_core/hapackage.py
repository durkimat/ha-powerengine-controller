"""Retire the Home Assistant package file earlier versions kept in `<ha config>/packages/` (0.9.218).

PowerEngine used to write one package file there: a one-button update script and a watchdog automation. Both are now
the integration's own (the `powerengine.update` service and its watchdog), so the file is removed once the integration
has taken over the watchdog (powerengine.py, `_retire_package`). Only a file recognised as PowerEngine's is touched:
the marker in its first lines, an older shipped header, or the old combined package. The file is copied to
`<name>.bak-<date>` first, named so that Home Assistant ignores it (it loads only `*.yaml`), and only the newest few
backups are kept. A file that is not recognised is left alone and reported.
"""

from __future__ import annotations

import glob
import os
import shutil

_MARKER_PREFIX = "# Managed by PowerEngine:"
MAIN = "powerengine_handover.yaml"
RETIRED = (MAIN,)
KEEP_BACKUPS = 3

# The first line of every earlier shipped version (before the marker existed).
_OLD_HEADERS = ("# PowerEngine package:", "# PowerEngine handover:")
_COMBINED_SIGNATURE = "battery_handover_to_powerengine"


def _norm(text: str) -> str:
    return text.replace("\r\n", "\n")


def recognised(text: str) -> bool:
    """Is this file PowerEngine's: the marker in its first lines, an older shipped header, or the old combined file."""
    head = _norm(text).lstrip("﻿ \n").split("\n")[:6]
    if any(line.startswith(_MARKER_PREFIX) for line in head):
        return True
    first = head[0] if head else ""
    if first.startswith(_OLD_HEADERS[0]):
        return True
    return first.startswith(_OLD_HEADERS[1]) and _COMBINED_SIGNATURE in text


def _backup_path(path: str, stamp: str) -> str:
    base = f"{path}.bak-{stamp[:8]}"
    candidate, n = base, 1
    while os.path.exists(candidate):
        n += 1
        candidate = f"{base}-{n}"
    return candidate


def _prune_backups(path: str) -> None:
    for old in sorted(glob.glob(glob.escape(path) + ".bak-*"))[:-KEEP_BACKUPS]:
        try:
            os.remove(old)
        except OSError:
            pass


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError):
        return "\0unreadable"            # present but not ours to touch: not recognised, so left alone


def retire(ha_dir: str, stamp: str, read=_read) -> dict:
    """Remove PowerEngine's package files from `<ha_dir>/packages`, with a backup beside each. Returns
    {"removed": [names], "unmanaged": [names left alone], "errors": [(name, message)]}. Never raises for a filesystem
    problem and never creates anything but the backup."""
    out: dict = {"removed": [], "unmanaged": [], "errors": []}
    packages = os.path.join(ha_dir, "packages")
    if not os.path.isdir(packages):
        return out
    for name in RETIRED:
        path = os.path.join(packages, name)
        text = read(path)
        if text is None:
            continue
        if not recognised(text):
            out["unmanaged"].append(name)
            continue
        try:
            shutil.copy2(path, _backup_path(path, stamp))
            _prune_backups(path)
            os.remove(path)
            out["removed"].append(name)
        except OSError as err:
            out["errors"].append((name, f"{name}: {err.strerror or err}"))
    return out
