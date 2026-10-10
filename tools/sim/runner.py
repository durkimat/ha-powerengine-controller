"""Variants, the cache and the parallel launcher.

A variant is: which code (a git ref, a folder, or this checkout), which engine settings and constants to override, which
archived config to seed from, and whether to start from the learned state. A job is one variant on one day.
Finished jobs
are cached by their key (workspace.job_key), so asking again is instant and a sweep runs only what is new.
"""

from __future__ import annotations

import itertools
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import workspace

WORKER = Path(__file__).resolve().parent / "worker.py"


@dataclass
class Variant:
    name: str
    code: str | None = None  # git ref, folder, or None for this checkout as it stands
    settings: dict = field(default_factory=dict)
    consts: dict = field(default_factory=dict)
    config: str | None = None  # an archived config file name (default: the newest)
    fresh: bool = False  # no learned state

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "code": self.code,
            "settings": self.settings,
            "consts": self.consts,
            "config": self.config,
            "fresh": self.fresh,
        }

    @staticmethod
    def of(d: dict) -> Variant:
        return Variant(
            d["name"],
            d.get("code") or None,
            dict(d.get("settings") or {}),
            dict(d.get("consts") or {}),
            d.get("config") or None,
            bool(d.get("fresh")),
        )


def parse_days(text: str, available: list[str], synthetic: list[str] | None = None) -> list[str]:
    """`A..B` (inclusive), `A,B,C`, `all` (every real day with a snapshot) or `synthetic` (the days synth.py made)."""
    if text == "all":
        return list(available)
    if text == "synthetic":
        return list(synthetic or [])
    days = []
    for part in text.split(","):
        if ".." in part:
            a, b = (date.fromisoformat(x) for x in part.split(".."))
            days += [(a + timedelta(n)).isoformat() for n in range((b - a).days + 1)]
        elif part:
            days.append(date.fromisoformat(part).isoformat())
    return days


def parse_assignments(items) -> dict:
    out = {}
    for item in items or []:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise SystemExit(f"expected key=value, got {item!r}")
        out[key.strip()] = value.strip()
    return out


def parse_variant(text: str, default_code: str | None = None) -> Variant:
    """`name[@ref]:key=value,key=value`: a named variant. A key with a dot is a constant, else an engine setting."""
    head, _, body = text.partition(":")
    name, _, ref = head.partition("@")
    v = Variant(name.strip(), ref.strip() or default_code)
    for item in [x for x in body.split(",") if x.strip()]:
        key, sep, value = item.partition("=")
        if not sep or not key.strip():
            raise SystemExit(f"--variant {text!r}: expected key=value, got {item!r}")
        (v.consts if "." in key else v.settings)[key.strip()] = value.strip()
    return v


def sweep_variants(base: Variant, sweeps: list[str]) -> list[Variant]:
    """`key=a,b,c` (several --sweep options make a grid)."""
    if not sweeps:
        return [base]
    axes = []
    for s in sweeps:
        key, sep, values = s.partition("=")
        if not sep or not values:
            raise SystemExit(f"--sweep wants key=a,b,c, got {s!r}")
        axes.append([(key.strip(), v.strip()) for v in values.split(",")])
    out = []
    for combo in itertools.product(*axes):
        settings, consts = dict(base.settings), dict(base.consts)
        for key, value in combo:
            (consts if "." in key else settings)[key] = value
        label = ",".join(f"{k}={v}" for k, v in combo)
        out.append(Variant(f"{base.name}[{label}]", base.code, settings, consts, base.config, base.fresh))
    return out


class Workspace:
    def __init__(self, data: Path, work: Path):
        self.data, self.work = Path(data), Path(work)
        self.extra = self.work / "synthetic"  # days made by synth.py, read beside the archive
        workspace.check_work(self.work, self.data)
        (self.work / "runs").mkdir(parents=True, exist_ok=True)

    def seed_for(self, v: Variant) -> dict:
        seed = workspace.seed_files(self.data)
        if v.config:
            chosen = next((p for p in workspace.configs(self.data) if p.name == v.config), None)
            if chosen is None:
                raise SystemExit(f"no archived config named {v.config}")
            seed["config"] = chosen
        if not seed["config"]:
            raise SystemExit(f"{self.data} has no archived config under versions/config")
        return seed

    def job(self, v: Variant, day: str) -> dict:
        root, label = workspace.resolve_code(v.code, self.work)
        seed = self.seed_for(v)
        cid = workspace.code_id(root)
        key = workspace.job_key(
            code=cid,
            day=day,
            data=self.data,
            seed=seed,
            fresh=v.fresh,
            settings=v.settings,
            consts=v.consts,
            extra=self.extra,
        )
        return {
            "key": key,
            "day": day,
            "code": str(root),
            "label": label,
            "code_id": cid,
            "data": str(self.data),
            "extra": str(self.extra),
            "seed": {k: str(p) if p else None for k, p in seed.items()},
            "seed_names": {k: (p.name if p else None) for k, p in seed.items()},
            "fresh": v.fresh,
            "settings": v.settings,
            "consts": v.consts,
        }

    def cached(self, key: str) -> dict | None:
        try:
            return json.loads((self.work / "runs" / f"{key}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def result_path(self, key: str) -> Path:
        return self.work / "runs" / f"{key}.json"


def run_jobs(
    ws: Workspace,
    jobs: list[dict],
    n_jobs: int,
    force: bool = False,
    say=lambda *_: None,
    on_done=lambda job, result: None,
) -> dict[str, dict]:
    """Run each job in its own process, `n_jobs` at a time; a finished one is read from the cache. {key: result}."""
    results: dict[str, dict] = {}
    todo = []
    for job in jobs:
        if job["key"] in results:
            continue
        hit = None if force else ws.cached(job["key"])
        if hit is not None and hit.get("status") in ("ok", "no_snapshot", "incomplete"):
            results[job["key"]] = hit
            on_done(job, hit)
        elif job["key"] not in {j["key"] for j in todo}:
            todo.append(job)
    if todo:
        say(f"running {len(todo)} job(s), {min(n_jobs, len(todo))} at a time ({len(jobs) - len(todo)} cached)")
    lock = threading.Lock()
    queue = list(todo)

    def drain():
        while True:
            with lock:
                if not queue:
                    return
                job = queue.pop(0)
            results[job["key"]] = run_one(ws, job)
            on_done(job, results[job["key"]])
            say(f"  {job['day']} {job['label']}: {results[job['key']].get('status')}")

    threads = [threading.Thread(target=drain) for _ in range(max(1, min(n_jobs, len(todo))))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def run_one(ws: Workspace, job: dict) -> dict:
    out = ws.result_path(job["key"])
    with tempfile.TemporaryDirectory(prefix="pe-sim-job-") as tmp:
        spec = Path(tmp) / "job.json"
        spec.write_text(json.dumps(job), encoding="utf-8")
        t0 = time.monotonic()
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
        env["PYTHONDONTWRITEBYTECODE"] = "1"  # the exported code and the archive stay as they were
        proc = subprocess.run(
            [sys.executable, "-I", str(WORKER), "run", str(spec), str(out)],
            capture_output=True,
            text=True,
            env=env,
            cwd=tmp,
        )
        if not out.exists():
            return {
                "day": job["day"],
                "status": "failed",
                "reason": f"worker died ({proc.returncode}): {(proc.stderr or proc.stdout)[-600:]}",
            }
        res = json.loads(out.read_text(encoding="utf-8"))
        res["wall_s"] = round(time.monotonic() - t0, 1)
        if res.get("status") == "ok":
            out.write_text(json.dumps(res, separators=(",", ":")), encoding="utf-8")
        else:
            out.unlink(missing_ok=True)  # a failure is never cached
        return res


def catalogue(root: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    proc = subprocess.run(
        [sys.executable, "-I", str(WORKER), "catalogue", root], capture_output=True, text=True, env=env
    )
    if proc.returncode:
        raise RuntimeError(proc.stderr[-400:])
    return json.loads(proc.stdout)
