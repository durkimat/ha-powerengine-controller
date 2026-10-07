"""tools/outline.sh: the section map of the big files."""
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not shutil.which("awk"), reason="needs awk")

PY = """import os


class A:
    # --- first part ------------------------------------------------------
    def one(self):
        pass

    def two(self):
        pass

    # --- second part (plan.md) --------------------------------------------
    def three(self):
        pass
"""
JS = """const A = 1;
/* ------------------------------------------------------------------ helpers */
function help() {}
// --- Config page --------------------------------------------------------
class X extends Y {}
// ---- the cards ----
customElements.define("x-card", X);
/* ---- last one */
"""


def run(tmp_path, name, text, *args):
    f = tmp_path / name
    f.write_text(text)
    cmd = ["bash", str(ROOT / "tools/outline.sh"), str(f), *args]
    return subprocess.run(cmd, capture_output=True, text=True).stdout


def test_python_sections_with_ranges_and_defs(tmp_path):
    out = run(tmp_path, "app.py", PY).splitlines()
    assert out[0].endswith("app.py (14 lines)")
    assert out[1].split(None, 1) == ["5-11", "first part"]
    assert out[2].split(None, 1) == ["12-14", "second part (plan.md)"]
    full = run(tmp_path, "app.py", PY, "--defs")
    assert "def one" in full and "def three" in full and "class A" not in full   # class A is before the first section


def test_js_block_and_line_comment_sections(tmp_path):
    out = run(tmp_path, "card.js", JS).splitlines()
    names = [line.split(None, 1)[1] for line in out[1:]]
    assert names == ["helpers", "Config page", "the cards", "last one"]
    full = run(tmp_path, "card.js", JS, "--defs")
    assert "function help" in full and "class X" in full and "customElements.define" in full


def test_a_missing_file_says_so(tmp_path):
    cmd = ["bash", str(ROOT / "tools/outline.sh"), str(tmp_path / "nope.py")]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert "no such file" in r.stdout
