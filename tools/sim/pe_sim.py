#!/usr/bin/env python3
"""PowerEngine simulator: replay stored days through the real app (engine v2) with other code or other settings.

    pe_sim.py run   --days 2026-10-06..2026-10-09 [--code REF] [--set k=v ...] [--sweep k=a,b,c] [--jobs N] [--out DIR]
    pe_sim.py check --days ...        the runner against the nightly comparison's own code, on the same inputs
    pe_sim.py serve [--port 8765]     the GUI (127.0.0.1 only)

Reads the archive (`--data`, default ~/pe-data) and never writes to it. Writes cached runs to `--work`
(default ~/pe-sim)
and the detail files of a run to `--out`. No network, no Home Assistant.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import export  # noqa: E402
import runner  # noqa: E402
import scoreboard  # noqa: E402
import workspace  # noqa: E402


def _common(p):
    p.add_argument("--data", default=str(workspace.default_data()), help="the archive (read only)")
    p.add_argument("--work", default=str(workspace.default_work()), help="cached runs and exported code")
    p.add_argument("--days", default="all", help="A..B, A,B,C or all (every day that has a forecast snapshot)")
    p.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2), help="runs at a time")


def cmd_run(a) -> int:
    ws = runner.Workspace(Path(a.data), Path(a.work))
    days = runner.parse_days(a.days, workspace.days_with_snapshots(ws.data))
    base = runner.Variant("base", None)
    variants = [base]
    mine = runner.Variant("variant", a.code, runner.parse_assignments(a.set), {}, a.config, a.fresh)
    for k in list(mine.settings):
        if "." in k:
            mine.consts[k] = mine.settings.pop(k)
    if a.code or mine.settings or mine.consts or a.sweep or a.config or a.fresh:
        variants = ([] if a.no_base else [base]) + runner.sweep_variants(mine, a.sweep or [])
    jobs, by_variant = [], {}
    for v in variants:
        by_variant[v.name] = [ws.job(v, d) for d in days]
        jobs += by_variant[v.name]
    results = runner.run_jobs(ws, jobs, a.jobs, a.force, lambda m: print(m, file=sys.stderr, flush=True))
    ordered = {name: [results[j["key"]] for j in js] for name, js in by_variant.items()}
    first = None
    for name, res in ordered.items():
        label = next((r.get("code") for r in res if r.get("code")), "?")
        print(scoreboard.table(f"{name}  [{label}]  {json.dumps(variants_of(variants, name))}", res) + "\n")
        if first is None:
            first = res
        elif not a.no_base and variants[0].name == "base":
            print(scoreboard.versus(first, res, name) + "\n")
    if a.out:
        export.write_report(Path(a.out), [v.as_dict() for v in variants], ordered)
        print(f"report written to {a.out}", file=sys.stderr)
    return 1 if any(r.get("status") == "failed" for rs in ordered.values() for r in rs) else 0


def variants_of(variants, name) -> dict:
    v = next(v for v in variants if v.name == name)
    return {k: x for k, x in (("set", {**v.settings, **v.consts}), ("fresh", v.fresh)) if x}


def cmd_check(a) -> int:
    """Runs the nightly comparison's own code (`pe_core.compare.run`) and the runner's worker on the same inputs and
    compares engine v2's result. They must agree exactly: both are the same app, the same world and the same seed.
    Also prints the recorded nightly result of that day, which differs for honest reasons: the config, learned state
    and code of that night were not today's."""
    ws = runner.Workspace(Path(a.data), Path(a.work))
    days = runner.parse_days(a.days, workspace.days_with_snapshots(ws.data))
    root, label = workspace.resolve_code(None, ws.work)
    seed = workspace.seed_files(ws.data)
    bad = 0
    for day in days:
        job = ws.job(runner.Variant("check"), day)
        mine = runner.run_jobs(ws, [job], 1, True)[job["key"]]
        with tempfile.TemporaryDirectory(prefix="pe-sim-check-") as tmp:
            save = workspace.assemble_save_dir(Path(tmp) / "save", ws.data, seed, False)
            out = Path(tmp) / "nightly.json"
            env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
            env["PYTHONPATH"] = str(root / workspace.APP_DIR)
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            subprocess.run(
                [sys.executable, "-m", "pe_core.compare.run", "--save-dir", str(save), "--day", day, "--out", str(out)],
                env=env,
                capture_output=True,
                text=True,
                cwd=tmp,
            )
            nightly = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
        recorded = {}
        try:
            recorded = json.loads((ws.data / "costs" / "compare" / f"{day}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        if mine.get("status") != "ok" or nightly.get("status") != "ok":
            both = mine.get("status") == nightly.get("status") != "failed"  # refused for the same reason: agreed
            print(
                f"{day}: not compared (runner {mine.get('status')} {mine.get('reason', '')[:60]}; "
                f"nightly code {nightly.get('status')} {nightly.get('reason', '')[:60]})"
            )
            bad += 0 if both else 1
            continue
        s, n = mine["score"], nightly["v2"]
        same = abs(s["cost"] - n["cost"]) < 0.0005 and s["mode_changes"] == n["modes"] and s["flip_flops"] == n["flips"]
        bad += 0 if same else 1
        rec = recorded.get("v2") or {}
        gap = (
            ""
            if not rec
            else f"; recorded nightly {rec.get('cost')} ({rec.get('modes')} modes, {rec.get('flips')} flips)"
        )
        print(
            f"{day}: runner {s['cost']} ({s['mode_changes']} modes, {s['flip_flops']} flips) vs nightly code "
            f"{n['cost']} ({n['modes']}, {n['flips']}) -> {'SAME' if same else 'DIFFERENT'}{gap}"
        )
    return 1 if bad else 0


def cmd_serve(a) -> int:
    import server

    return server.serve(Path(a.data), Path(a.work), a.port, a.jobs)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run variants over stored days and print the scoreboard")
    _common(r)
    r.add_argument("--code", help="a git ref of this repo, or a folder holding a checkout (default: this checkout)")
    r.add_argument(
        "--set", action="append", metavar="KEY=VALUE", help="an engine_v2 setting, or pe_core.mod.NAME=value"
    )
    r.add_argument("--sweep", action="append", metavar="KEY=A,B,C", help="a grid (repeat for more axes)")
    r.add_argument("--config", help="an archived config file name to seed from (default: the newest)")
    r.add_argument("--fresh", action="store_true", help="start without the learned state")
    r.add_argument("--no-base", action="store_true", help="don't run the baseline (this checkout, defaults) beside it")
    r.add_argument("--force", action="store_true", help="ignore cached runs")
    r.add_argument("--out", help="write report.md and report.json here")
    r.set_defaults(fn=cmd_run)
    c = sub.add_parser("check", help="the runner against the nightly comparison's own code")
    _common(c)
    c.set_defaults(fn=cmd_check)
    s = sub.add_parser("serve", help="the GUI")
    _common(s)
    s.add_argument("--port", type=int, default=8765)
    s.set_defaults(fn=cmd_serve)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
