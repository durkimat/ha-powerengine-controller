"""Keep PowerEngine's Home Assistant package files in step (0.9.109).

The engine ships one package file (`ha_packages/*.yml`). This module decides what to do with it in the user's
`<ha config>/packages/` folder, and does it:

  write            absent and wanted
  update           present, recognised as PowerEngine's, and different
  remove           present, recognised, not wanted
  keep             present, recognised, identical
  skip_unmanaged   present and not recognised: never touched

Recognised means: the first lines carry the marker, or it is an older shipped version (by its header), or it is the old
combined package (the handover scripts and the update/restart automations in one file). The main file is always wanted.
The Predbat handover file an earlier version installed is retired: when it is there and recognised it is removed.

`plan` is pure (a file-reading function in, decisions out); `apply` does the writing, with a backup beside the old file
named so that Home Assistant ignores it (it loads only `*.yaml` in the folder, and `.bak-DATE` is not that).
"""

from __future__ import annotations

import glob
import os
import shutil
from dataclasses import dataclass

MARKER = "# Managed by PowerEngine: rewritten when PowerEngine updates. Edits here are replaced (a backup is kept)."
_MARKER_PREFIX = "# Managed by PowerEngine:"

MAIN = "powerengine_handover.yaml"
RETIRED = ("powerengine_predbat_handover.yaml",)          # shipped until 0.9.211; removed from a packages folder
SHIPPED_FILES = {MAIN: "powerengine_handover.yml"}

# What Home Assistant creates when a package is loaded (the stateless "is it loaded" check).
MAIN_ENTITY = "script.powerengine_update"

# The first line of every earlier shipped version (before the marker existed).
_OLD_HEADERS = ("# PowerEngine package:", "# PowerEngine and Predbat handover (OPTIONAL", "# PowerEngine handover:")
_COMBINED_SIGNATURE = "battery_handover_to_powerengine"

STATES = ("ok", "reload_needed", "no_packages_dir", "unmanaged", "error")


@dataclass(frozen=True)
class FileAction:
    name: str                       # file name in the packages folder
    action: str                     # write | update | remove | keep | skip_unmanaged
    managed: bool                   # recognised as PowerEngine's (or about to be)
    text: str | None = None         # what to write, for write and update


def _norm(text: str) -> str:
    return text.replace("\r\n", "\n")


def recognised(text: str) -> bool:
    """Is this file PowerEngine's: the marker in its first lines, an older shipped header, or the old combined file."""
    head = _norm(text).lstrip("﻿ \n").split("\n")[:6]
    if any(line.startswith(_MARKER_PREFIX) for line in head):
        return True
    first = head[0] if head else ""
    if first.startswith(_OLD_HEADERS[:2]):
        return True
    return first.startswith(_OLD_HEADERS[2]) and _COMBINED_SIGNATURE in text


def plan(packages_dir: str, shipped: dict[str, str], read) -> list[FileAction]:
    """One decision per file that is present or wanted. `read(path)` gives the text or None when there is no file."""
    out = []
    for name, text in shipped.items():
        existing = read(os.path.join(packages_dir, name))
        if existing is None:
            out.append(FileAction(name, "write", True, text))
        elif not recognised(existing):
            out.append(FileAction(name, "skip_unmanaged", False))
        elif _norm(existing) == _norm(text):
            out.append(FileAction(name, "keep", True))
        else:
            out.append(FileAction(name, "update", True, text))
    for name in RETIRED:
        existing = read(os.path.join(packages_dir, name))
        if existing is not None and recognised(existing):
            out.append(FileAction(name, "remove", True))
    return out


def _backup_path(path: str, stamp: str) -> str:
    base = f"{path}.bak-{stamp[:8]}"
    candidate, n = base, 1
    while os.path.exists(candidate):
        n += 1
        candidate = f"{base}-{n}"
    return candidate


KEEP_BACKUPS = 3                      # of each file: a rewrite at every update would otherwise pile them up


def _prune_backups(path: str) -> None:
    for old in sorted(glob.glob(glob.escape(path) + ".bak-*"))[:-KEEP_BACKUPS]:
        try:
            os.remove(old)
        except OSError:
            pass


def apply(packages_dir: str, actions: list[FileAction], stamp: str) -> list[tuple[str, str]]:
    """Carry out write / update / remove. The old file is copied to `<name>.bak-<stamp>` first. Returns (name, error)
    for each file that failed; never raises for a filesystem problem."""
    errors = []
    for a in actions:
        if a.action not in ("write", "update", "remove"):
            continue
        path = os.path.join(packages_dir, a.name)
        try:
            if a.action in ("update", "remove"):
                shutil.copy2(path, _backup_path(path, stamp))
                _prune_backups(path)
            if a.action == "remove":
                os.remove(path)
            else:
                tmp = os.path.join(packages_dir, "." + a.name + ".tmp")       # a dotfile: Home Assistant ignores it
                with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                    f.write(a.text or "")
                os.replace(tmp, path)
        except OSError as err:
            errors.append((a.name, f"{a.name}: {err.strerror or err}"))
    return errors


def read_text(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError):
        return "\0unreadable"            # present but not ours to touch: not recognised, so skipped


def shipped_texts(folder: str | None = None) -> dict[str, str]:
    """The files shipped inside the app folder, keyed by the name they take in the packages folder."""
    folder = folder or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ha_packages")
    out = {}
    for name, shipped in SHIPPED_FILES.items():
        with open(os.path.join(folder, shipped), encoding="utf-8") as f:
            out[name] = f.read()
    return out


def sync(ha_dir: str, shipped: dict[str, str], stamp: str, read=read_text) -> dict:
    """Decide and apply for the HA config folder `ha_dir`. Never creates the packages folder. Returns
    {"dir_exists", "actions", "errors"}; `actions` are what was decided (failed ones also in errors)."""
    packages = os.path.join(ha_dir, "packages")
    if not os.path.isdir(packages):
        return {"dir_exists": False, "actions": [], "errors": []}
    actions = plan(packages, shipped, read)
    errors = apply(packages, actions, stamp)
    return {"dir_exists": True, "actions": actions, "errors": errors}


def _label(name: str) -> str:
    return "PowerEngine package"


def reload_needed(result: dict, entity_present) -> bool:
    """Is Home Assistant running something other than the file on disk? From entity presence alone (stateless): the
    package is loaded when `script.powerengine_update` exists. A file that is not PowerEngine's, or could not be
    written, is not counted: reloading would change nothing."""
    if not result["dir_exists"]:
        return False
    failed = {n for n, _ in result["errors"]}
    main = {a.name: a for a in result["actions"]}.get(MAIN)
    return main is not None and main.managed and MAIN not in failed and not entity_present(MAIN_ENTITY)


def report(result: dict, entity_present) -> tuple[str, dict]:
    """The state and attributes of `sensor.pe_diag_package`."""
    files = [{"name": a.name, "action": a.action, "managed": a.managed} for a in result["actions"]]
    needed = reload_needed(result, entity_present)
    unmanaged = [a.name for a in result["actions"] if a.action == "skip_unmanaged"]
    if not result["dir_exists"]:
        state = "no_packages_dir"
        message = ("There is no packages folder in your Home Assistant configuration, so PowerEngine can't keep its "
                   "package up to date. Create the folder and add \"homeassistant: packages: !include_dir_named "
                   "packages\" to configuration.yaml (see the install guide).")
    elif result["errors"]:
        state = "error"
        message = "PowerEngine could not update its package files: " + "; ".join(e for _, e in result["errors"]) + "."
    elif unmanaged:
        state = "unmanaged"
        message = ("The " + " and ".join(unmanaged) + " file in your packages folder was not written by PowerEngine, "
                   "so it is left alone and may be out of date. Remove or rename it and PowerEngine will "
                   "put its own in place.")
    elif needed:
        state = "reload_needed"
        message = ("PowerEngine changed its Home Assistant package. Press \"Load PowerEngine's Home Assistant "
                   "changes\" on the Config page to use the change.")
    else:
        state = "ok"
        message = "PowerEngine's Home Assistant package is in place and loaded."
    return state, {"files": files, "reload_needed": needed, "message": message}


def changes(result: dict) -> list[FileAction]:
    return [a for a in result["actions"] if a.action in ("write", "update", "remove")
            and a.name not in {n for n, _ in result["errors"]}]


def describe_change(a: FileAction) -> str:
    verb = {"write": "added", "update": "updated", "remove": "removed"}[a.action]
    return f"{_label(a.name)} {verb} ({a.name})"
