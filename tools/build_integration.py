#!/usr/bin/env python3
"""Keep custom_components/powerengine/ (the Home Assistant integration, docs/plans/native-integration.md) in step with
the engine it runs.

    python tools/build_integration.py --sync     make the generated files current (do this after changing the app)
    python tools/build_integration.py --check    exit 1 and say what differs if they are not current (CI does this)
    python tools/build_integration.py OUT        build a clean copy of the installable folder under OUT

The integration's own files are written by hand in custom_components/powerengine/. Four things in that folder are
generated from the app and committed, because HACS installs a repository's custom_components folder as it stands:
  * engine/             a copy of the whole app (apps/powerengine/), which the integration starts as its own process;
  * protocol.py and host_server.py, copies of apps/powerengine/pe_core/'s, so that the integration and the engine are
    written against the same files (an engine update that changes the protocol makes the engine exit with status 10);
  * `version` in manifest.json, the engine's.
The release tools run --sync after they bump the version, and CI fails a change that leaves them out of date.
"""

from __future__ import annotations

import filecmp
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "powerengine"
APP = ROOT / "apps" / "powerengine"
SHARED = ("protocol.py", "host_server.py")          # copied from pe_core into the integration package
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache")


def engine_version() -> str:
    match = re.search(r"""__version__\s*=\s*['"]([^'"]+)['"]""", (APP / "pe_core" / "__init__.py").read_text())
    if not match:
        raise SystemExit("cannot read the engine's version")
    return match.group(1)


def generate(target: Path) -> None:
    """Write the generated parts into `target` (an integration folder that already has the hand-written files)."""
    for name in SHARED:
        shutil.copy2(APP / "pe_core" / name, target / name)
    if (target / "engine").exists():
        shutil.rmtree(target / "engine")
    shutil.copytree(APP, target / "engine", ignore=IGNORE)
    manifest = target / "manifest.json"
    data = json.loads(manifest.read_text())
    data["version"] = engine_version()
    manifest.write_text(json.dumps(data, indent=2) + "\n")


def sync() -> Path:
    generate(INTEGRATION)
    return INTEGRATION


def build(out: Path) -> Path:
    """A clean copy of the installable folder under `out` (out/custom_components/powerengine)."""
    target = Path(out) / "custom_components" / "powerengine"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(INTEGRATION, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    generate(target)
    return Path(out)


def differences() -> list[str]:
    """What in the committed integration differs from what --sync would write (empty: it is current)."""
    with tempfile.TemporaryDirectory() as tmp:
        fresh = build(Path(tmp)) / "custom_components" / "powerengine"
        out: list[str] = []

        def walk(cmp: filecmp.dircmp, prefix: str = "") -> None:
            out.extend(f"missing from the repository: {prefix}{n}" for n in cmp.left_only)
            out.extend(f"not generated (stale): {prefix}{n}" for n in cmp.right_only)
            out.extend(f"differs: {prefix}{n}" for n in cmp.diff_files + cmp.funny_files)
            for name, sub in cmp.subdirs.items():
                walk(sub, f"{prefix}{name}/")
        walk(filecmp.dircmp(fresh, INTEGRATION, ignore=["__pycache__", ".pytest_cache"]))
        return sorted(out)


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "--check"
    if arg == "--sync":
        print(f"synced {sync().relative_to(ROOT)}")
    elif arg == "--check":
        problems = differences()
        if problems:
            print("custom_components/powerengine is out of date; run: python tools/build_integration.py --sync")
            print("\n".join(problems[:30]))
            sys.exit(1)
        print("custom_components/powerengine is current")
    else:
        print(build(Path(arg)))
