"""tools/prepare_release.sh and release.sh --prepared, against a throw-away git repo (no network, no gh login)."""
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not shutil.which("git") or not shutil.which("jq"), reason="needs git and jq")

NOTES = "### Behaviour changes\n\n- None.\n\n### Fixes\n\n- A thing.\n"


def sh(cwd, *cmd, check=True, env=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=env)
    if check and r.returncode:
        raise AssertionError(f"{cmd} failed:\n{r.stdout}\n{r.stderr}")
    return r


def seed(repo, version="0.9.50"):
    (repo / "apps/powerengine/pe_core").mkdir(parents=True)
    (repo / "apps/powerengine/pe_core/__init__.py").write_text(f'__version__ = "{version}"\n')
    (repo / "docs").mkdir()
    (repo / "docs/INSTALL.md").write_text(
        f"**Version this guide matches:** {version}\n\nlog: PowerEngine {version} starting\n")
    (repo / "CHANGELOG.md").write_text(f"# Changelog\n\n## {version} (beta)\n\n- old\n")
    shutil.copytree(ROOT / "tools", repo / "tools", ignore=shutil.ignore_patterns("__pycache__"))


@pytest.fixture
def repos(tmp_path):
    origin = tmp_path / "origin.git"
    sh(tmp_path, "git", "init", "-q", "--bare", "-b", "main", str(origin))
    work = tmp_path / "work"
    work.mkdir()
    for c in (["git", "init", "-q", "-b", "main"], ["git", "config", "user.email", "t@example.com"],
              ["git", "config", "user.name", "T"], ["git", "remote", "add", "origin", str(origin)]):
        sh(work, *c)
    seed(work)
    sh(work, "git", "add", "-A")
    sh(work, "git", "commit", "-qm", "base")
    sh(work, "git", "push", "-q", "-u", "origin", "main")
    sh(work, "git", "checkout", "-q", "-b", "change")
    (work / "feature.txt").write_text("x\n")
    sh(work, "git", "add", "-A")
    sh(work, "git", "commit", "-qm", "the change")
    (tmp_path / "notes.md").write_text(NOTES)
    return work, tmp_path / "notes.md"


def prepare(work, notes, version="0.9.51", *extra):
    return sh(work, "bash", "tools/prepare_release.sh", version, "--notes", str(notes), *extra, check=False)


def test_prepare_bumps_commits_and_pushes(repos):
    work, notes = repos
    r = prepare(work, notes, "0.9.51", "--title", "A thing")
    assert r.returncode == 0, r.stderr
    assert '__version__ = "0.9.51"' in (work / "apps/powerengine/pe_core/__init__.py").read_text()
    assert "0.9.51" in (work / "docs/INSTALL.md").read_text()
    log = (work / "CHANGELOG.md").read_text()
    assert log.index("## 0.9.51 (beta)") < log.index("## 0.9.50")
    msg = sh(work, "git", "log", "-1", "--format=%B").stdout
    assert msg.startswith("0.9.51: A thing") and "Co-Authored-By:" in msg
    assert sh(work, "git", "rev-parse", "HEAD").stdout == sh(work, "git", "rev-parse", "origin/change").stdout
    assert sh(work, "git", "status", "--porcelain").stdout == ""


def test_prepare_refuses_a_branch_behind_main(repos, tmp_path):
    work, notes = repos
    other = tmp_path / "other"
    sh(tmp_path, "git", "clone", "-q", str(tmp_path / "origin.git"), str(other))
    sh(other, "git", "config", "user.email", "t@example.com")
    sh(other, "git", "config", "user.name", "T")
    (other / "more.txt").write_text("y\n")
    sh(other, "git", "add", "-A")
    sh(other, "git", "commit", "-qm", "main moved")
    sh(other, "git", "push", "-q", "origin", "main")
    r = prepare(work, notes)
    assert r.returncode != 0 and "behind main" in r.stderr
    assert '__version__ = "0.9.50"' in (work / "apps/powerengine/pe_core/__init__.py").read_text()


@pytest.mark.parametrize(
    "version,words", [("0.9.50", "not newer"), ("0.9.49", "not newer"), ("1.0", "give the version")])
def test_prepare_refuses_a_version_that_does_not_move_forward(repos, version, words):
    work, notes = repos
    r = prepare(work, notes, version)
    assert r.returncode != 0 and words in r.stderr


def test_prepare_refuses_main_and_a_dirty_tree_and_bad_notes(repos, tmp_path):
    work, notes = repos
    bad = tmp_path / "bad.md"
    bad.write_text("- no heading\n")
    assert "Behaviour changes" in prepare(work, bad).stderr
    (work / "feature.txt").write_text("dirty\n")
    assert "uncommitted" in prepare(work, notes).stderr
    sh(work, "git", "checkout", "-q", "--", "feature.txt")
    sh(work, "git", "checkout", "-q", "main")
    assert "not on 'main'" in prepare(work, notes).stderr


def test_prepare_twice_does_not_double_bump(repos):
    work, notes = repos
    assert prepare(work, notes).returncode == 0
    r = prepare(work, notes, "0.9.52")
    assert r.returncode != 0 and "already changed __version__" in r.stderr


def release(work, *args):
    return sh(work, "bash", "tools/release.sh", *args, check=False)


def test_release_flags_are_checked_before_anything_happens(repos):
    work, notes = repos
    r = release(work, "0.9.51", "--app-notes", str(notes), "--wait-main-ci")
    assert r.returncode != 0 and "only goes with --prepared" in r.stderr
    r = release(work, "0.9.51", "--app-notes", str(notes), "--prepared", "--card-notes", str(notes))
    assert r.returncode != 0 and "does not release the card yet" in r.stderr


def test_verify_prepared_accepts_a_prepared_branch_and_rejects_others(repos):
    work, notes = repos
    call = 'source tools/release.sh; DRY=1; VERSION=0.9.51; verify_prepared "$PWD" change'
    r = sh(work, "bash", "-c", call, check=False)
    assert r.returncode != 0 and "__version__ is '0.9.50'" in r.stderr
    assert prepare(work, notes).returncode == 0
    r = sh(work, "bash", "-c", call, check=False)
    assert r.returncode == 0, r.stderr
    (work / "CHANGELOG.md").write_text("# Changelog\n")
    r = sh(work, "bash", "-c", call, check=False)
    assert r.returncode != 0 and "no '## 0.9.51 (beta)' section" in r.stderr
