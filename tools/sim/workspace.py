"""Where the simulator reads and writes, and what makes one run the same as another.

Reads: the archive (`~/pe-data`, never written). Writes: the work folder (`~/pe-sim`: cached runs, code exported from
git refs, saved variants) and the temporary folder of a run. Nothing is written anywhere else.

A run is identified by its key: the code (a hash of the app's files, whatever the git state), the day and its inputs,
the settings and constants overridden, and the config, learned state and slot history it was seeded with.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

RUNNER_VERSION = 1  # bump when a change makes cached results wrong
REPO = Path(__file__).resolve().parents[2]
APP_DIR = Path("apps") / "powerengine"
CODE_SUFFIXES = {".py", ".yml", ".yaml", ".lovelace", ".template", ".json"}


def default_data() -> Path:
    return Path(os.environ.get("PE_SIM_DATA", "~/pe-data")).expanduser()


def default_work() -> Path:
    return Path(os.environ.get("PE_SIM_HOME", "~/pe-sim")).expanduser()


def inside(path: Path, folder: Path) -> bool:
    try:
        Path(path).resolve().relative_to(Path(folder).resolve())
        return True
    except ValueError:
        return False


def check_work(work: Path, data: Path) -> None:
    """The work folder must not be (or sit inside) the archive: the archive is append-only and read-only here."""
    if inside(work, data) or inside(data, work):
        raise SystemExit(f"the work folder {work} and the archive {data} must be separate folders")


def sha(*parts) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update(p if isinstance(p, bytes) else str(p).encode())
        h.update(b"\0")
    return h.hexdigest()[:16]


def file_sha(path) -> str:
    if not path:
        return "none"
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


# --- code -----------------------------------------------------------------------------------------------------


def git(*args, cwd=REPO) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def code_id(root: Path) -> str:
    """A hash of the files the app runs from: the same code gives the same id whatever branch it came from."""
    app = Path(root) / APP_DIR
    h = hashlib.sha1()
    for p in sorted(app.rglob("*")):
        if p.is_file() and p.suffix in CODE_SUFFIXES and "__pycache__" not in p.parts:
            h.update(str(p.relative_to(app)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:12]


def code_label(root: Path, ref: str | None) -> str:
    """Words for the code: the ref, and the commit it is at (with + if the files differ from it)."""
    try:
        head = git("rev-parse", "--short", "HEAD", cwd=root)
        dirty = bool(git("status", "--porcelain", "--", str(APP_DIR), cwd=root))
        return f"{ref or 'working tree'} @ {head}{'+' if dirty else ''}"
    except (subprocess.CalledProcessError, OSError):
        return ref or str(root)


def resolve_code(spec: str | None, work: Path) -> tuple[Path, str]:
    """(root folder, label). None, "" or "working": this checkout as it stands. An existing folder: that checkout.
    Anything else is a git ref of this repo, exported (not checked out) under the work folder."""
    if spec in (None, "", "working", "."):
        return REPO, code_label(REPO, None)
    p = Path(spec).expanduser()
    if p.is_dir():
        if not (p / APP_DIR / "pe_core").is_dir():
            raise SystemExit(f"{p} has no {APP_DIR}/pe_core")
        return p.resolve(), code_label(p, str(p))
    try:
        full = git("rev-parse", "--verify", f"{spec}^{{commit}}")
    except subprocess.CalledProcessError:
        raise SystemExit(f"{spec!r} is not a git ref or a folder") from None
    dest = Path(work) / "code" / full
    if not (dest / APP_DIR / "pe_core").is_dir():
        tmp = Path(tempfile.mkdtemp(dir=_ensure(Path(work) / "code"), prefix="export-"))
        try:
            tar = subprocess.run(
                ["git", "archive", full, str(APP_DIR)], cwd=REPO, check=True, capture_output=True
            ).stdout
            tmp_tar = tmp / "x.tar"
            tmp_tar.write_bytes(tar)
            with tarfile.open(tmp_tar) as tf:
                tf.extractall(tmp / "tree", filter="data")
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(tmp / "tree", dest)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return dest, f"{spec} @ {full[:7]}"


def _ensure(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def refs() -> list[dict]:
    """The branches this repo knows (local and as last fetched), newest first. Nothing is fetched."""
    out = git(
        "for-each-ref",
        "--sort=-committerdate",
        "--format=%(refname:short)\t%(objectname:short)\t%(committerdate:short)\t%(subject)\t%(refname)",
        "refs/heads",
        "refs/remotes",
    )
    rows = []
    for line in out.splitlines():
        name, sha_, date, subject, full = (line.split("\t") + ["", "", "", "", ""])[:5]
        if name.endswith("/HEAD") or (full.startswith("refs/remotes/") and "/" not in name):
            continue
        rows.append({"name": name, "sha": sha_, "date": date, "subject": subject})
    return rows


# --- the archive ----------------------------------------------------------------------------------------------


def days_with_snapshots(data: Path, extra: Path | None = None) -> list[str]:
    """Days that have a forecast snapshot: the archive's, and any synthetic ones under `extra`."""
    days = set()
    for root in (data, extra):
        snaps = Path(root) / "costs" / "snapshots" if root else None
        if snaps is not None and snaps.is_dir():
            days |= {p.stem for p in snaps.glob("????-??-??.json")}
    return sorted(days)


def synthetic_days(extra: Path | None) -> list[str]:
    """Days made by synth.py (they exist only under `extra`)."""
    snaps = Path(extra) / "costs" / "snapshots" if extra else None
    return sorted(p.stem for p in snaps.glob("????-??-??.json")) if snaps is not None and snaps.is_dir() else []


def day_source(day: str, data: Path, extra: Path | None) -> Path:
    """The costs folder that holds `day`: the archive's, or the synthetic one. A date in both is refused."""
    real, fake = (
        (Path(data) / "costs" / f"{day}.json").exists(),
        bool(extra) and (Path(extra) / "costs" / f"{day}.json").exists(),
    )
    if real and fake:
        raise SystemExit(f"{day} is both a real archived day and a synthetic one: remove one")
    return Path(extra) / "costs" if fake else Path(data) / "costs"


def newest(folder: Path, pattern: str) -> Path | None:
    files = sorted(Path(folder).glob(pattern)) if Path(folder).is_dir() else []
    return files[-1] if files else None


def seed_files(data: Path) -> dict:
    """The newest archived config, learned state and slot history (what a replay is seeded with by default)."""
    v = Path(data) / "versions"
    return {
        "config": newest(v / "config", "*.yaml"),
        "state": newest(v / "engine_v2_state", "*.json"),
        "slots": newest(v / "slots", "*.json"),
    }


def configs(data: Path) -> list[Path]:
    return (
        sorted((Path(data) / "versions" / "config").glob("*.yaml"))
        if (Path(data) / "versions" / "config").is_dir()
        else []
    )


def assemble_save_dir(dest: Path, data: Path, seed: dict, fresh: bool, extra: Path | None = None) -> Path:
    """The folder `compare.run` expects (config.yaml, engine_v2_state.json, costs/), made of copies and links into
    the archive. The archive is only ever read: the run writes into its own temporary folders."""
    dest = Path(dest)
    costs = _ensure(dest / "costs")
    snaps = _ensure(costs / "snapshots")
    for root in (data, extra):
        src = Path(root) / "costs" if root else None
        if src is None or not src.is_dir():
            continue
        for p in src.glob("????-??-??.json"):
            if not (costs / p.name).exists():
                (costs / p.name).symlink_to(p)
        for p in (src / "snapshots").glob("????-??-??.json") if (src / "snapshots").is_dir() else []:
            if not (snaps / p.name).exists():
                (snaps / p.name).symlink_to(p)
    if seed.get("config"):
        shutil.copyfile(seed["config"], dest / "config.yaml")
    if seed.get("state") and not fresh:
        shutil.copyfile(seed["state"], dest / "engine_v2_state.json")
    if seed.get("slots"):
        shutil.copyfile(seed["slots"], costs / "slots.json")
    return dest


def job_key(
    *, code: str, day: str, data: Path, seed: dict, fresh: bool, settings: dict, consts: dict, extra: Path | None = None
) -> str:
    costs = day_source(day, data, extra)
    return sha(
        RUNNER_VERSION,
        code,
        day,
        file_sha(costs / f"{day}.json"),
        file_sha(costs / "snapshots" / f"{day}.json"),
        file_sha(seed.get("config")),
        "fresh" if fresh else file_sha(seed.get("state")),
        file_sha(seed.get("slots")),
        json.dumps(settings, sort_keys=True),
        json.dumps(consts, sort_keys=True),
    )
