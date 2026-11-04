#!/usr/bin/env python3
"""Assemble the installable PowerEngine integration (docs/plans/native-integration.md).

    python tools/build_integration.py [OUT]        (default: integration/build)

OUT/custom_components/powerengine/ gets:
  * the integration (integration/custom_components/powerengine/), with `version` in manifest.json set to the engine's;
  * protocol.py and host_server.py from apps/powerengine/pe_core/, so the integration and the engine are written
    against the same files (an engine update that changes the protocol makes the engine exit with status 10);
  * engine/, a copy of the whole app (apps/powerengine/), which the integration starts as its own process.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "integration" / "custom_components" / "powerengine"
APP = ROOT / "apps" / "powerengine"
SHARED = ("protocol.py", "host_server.py")          # copied from pe_core into the integration package
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache")


def engine_version() -> str:
    match = re.search(r"""__version__\s*=\s*['"]([^'"]+)['"]""", (APP / "pe_core" / "__init__.py").read_text())
    if not match:
        raise SystemExit("cannot read the engine's version")
    return match.group(1)


def build(out: Path | None = None) -> Path:
    """Build into `out` (default integration/build); the folder that holds `custom_components`."""
    out = Path(out) if out else ROOT / "integration" / "build"
    target = out / "custom_components" / "powerengine"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(INTEGRATION, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "engine", *SHARED))
    for name in SHARED:
        shutil.copy2(APP / "pe_core" / name, target / name)
    shutil.copytree(APP, target / "engine", ignore=IGNORE)
    manifest = target / "manifest.json"
    data = json.loads(manifest.read_text())
    data["version"] = engine_version()
    manifest.write_text(json.dumps(data, indent=2) + "\n")
    return out


if __name__ == "__main__":
    print(build(Path(sys.argv[1]) if len(sys.argv) > 1 else None))
