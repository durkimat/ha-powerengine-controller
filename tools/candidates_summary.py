#!/usr/bin/env python3
"""Check and summarise a setup wizard "candidate entities" export (the card writes it for unsupported devices).

    tools/candidates_summary.py candidates.json

Prints what is wrong with the file (shape, anything left unscrubbed), then a readable list of each device and its
entities. The starting point for a new inverter definition: docs/INVERTERS.md, docs/WIZARD.md.
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "apps" / "powerengine"))

from pe_core import candidates  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] in ("-h", "--help"):
        print(__doc__)
        return 2
    try:
        with open(argv[1], encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as err:
        print(f"Could not read {argv[1]}: {err}")
        return 2
    bad = candidates.problems(data)
    if bad:
        print("Problems with the file:\n  " + "\n  ".join(bad))
        return 1
    leaks = candidates.unscrubbed(data)
    if leaks:
        print("NOT SCRUBBED. Do not commit or share this file; ask the sender for a fresh one:")
        print("  " + "\n  ".join(leaks))
        return 1
    print(candidates.summary(data))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
