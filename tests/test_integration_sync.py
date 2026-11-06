"""custom_components/powerengine is the integration HACS installs, and part of it is generated from the app
(tools/build_integration.py): the copy of the engine, the shared protocol files, the manifest's version. A change that
leaves them out of date would install an engine that is not the one that was tested."""

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_the_generated_parts_of_the_integration_are_current():
    result = subprocess.run([sys.executable, str(ROOT / "tools" / "build_integration.py"), "--check"],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + "\nRun: python tools/build_integration.py --sync"


def test_the_manifest_has_the_engines_version_and_what_hacs_needs():
    import json
    import re
    manifest = json.loads((ROOT / "custom_components" / "powerengine" / "manifest.json").read_text())
    version = re.search(r'__version__\s*=\s*"([^"]+)"', (ROOT / "engine" / "pe_core" / "__init__.py")
                        .read_text()).group(1)
    assert manifest["version"] == version
    for key in ("domain", "name", "codeowners", "documentation", "issue_tracker", "config_flow", "version"):
        assert manifest.get(key), key
    assert manifest["domain"] == "powerengine"
