"""One simulation, in its own process: `python worker.py run <job.json> <result.json>`, or
`python worker.py catalogue <code root>` (the engine settings that code has, as JSON).

The process puts the chosen checkout's `apps/powerengine` first on `sys.path`, so it runs that code's real `pe_core`
and the real app (the same machinery as the nightly comparison, `pe_core.compare`): engine v2 only, Active, RAM remote
control, the owner's settings, the day's recorded house, sun, prices, smart slots and grid events, the forecasts as they
were. Nothing here changes what the engine does. It watches (the plan at every re-plan, the level, the mode changes) and
writes what it saw.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import traceback
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import workspace  # noqa: E402


def _use_code(root: str) -> None:
    sys.path.insert(0, str(Path(root) / workspace.APP_DIR))


def catalogue(root: str) -> dict:
    _use_code(root)
    from pe_core.engine_v2 import settings as s

    out = {}
    for key, (kind, default, lo, hi, unit, label, help_) in s.SETTINGS.items():
        out[key] = {"kind": kind, "default": default, "min": lo, "max": hi, "unit": unit, "label": label, "help": help_}
    return out


def _typed(text):
    import yaml

    return yaml.safe_load(text) if isinstance(text, str) else text


def apply_consts(consts: dict) -> None:
    """Module constants, named `pe_core.<module path>.NAME` (the `pe_core.` may be left off)."""
    import importlib

    for dotted, value in consts.items():
        mod, _, name = (dotted if dotted.startswith("pe_core.") else f"pe_core.{dotted}").rpartition(".")
        module = importlib.import_module(mod)
        if not hasattr(module, name):
            raise ValueError(f"{mod} has no constant {name}")
        setattr(module, name, _typed(value))


def patch_config(text: str, settings: dict) -> str:
    import yaml

    from pe_core.engine_v2 import settings as s

    unknown = [k for k in settings if k not in s.SETTINGS]
    if unknown:
        raise ValueError(f"unknown engine setting(s): {', '.join(unknown)}")
    cfg = yaml.safe_load(text)
    cfg["engine_v2"] = {**(cfg.get("engine_v2") or {}), **{k: _typed(v) for k, v in settings.items()}}
    return yaml.safe_dump(cfg, sort_keys=False)


def _r(x, n=3):
    return None if x is None else round(float(x), n)


def _plan(vr, at, tz) -> dict:
    path = vr.path or {}
    return {
        "at": at.astimezone(tz).isoformat(timespec="seconds"),
        "because": vr.because,
        "calc_s": _r(vr.calc_s, 2),
        "cost_p": _r(vr.cost_expected_p, 2),
        "selfuse_p": _r(vr.cost_selfuse_p, 2),
        "timeline": [
            [
                i.mode,
                i.start.astimezone(tz).isoformat(timespec="minutes"),
                i.end.astimezone(tz).isoformat(timespec="minutes"),
                _r(i.level_start, 1),
                _r(i.level_end, 1),
                i.until,
                i.reason,
            ]
            for i in vr.timeline
        ],
        "path": {
            "start": path.get("start"),
            "step_min": path.get("step_min"),
            **{k: [_r(x, 1) for x in path.get(k, [])] for k in ("mid", "low", "high")},
        },
    }


def _rows(world, day: date, tz) -> list[dict]:
    keys = ("act", "exp", "solar", "house", "car", "axle", "free")
    return [
        {
            "start": r["start"].astimezone(tz).isoformat(timespec="minutes"),
            **{k: (r[k] if isinstance(r[k], bool) else _r(r[k], 4)) for k in keys if k in r},
        }
        for r in world.rows_for(day)
    ]


def run(job: dict) -> dict:
    _use_code(job["code"])
    from pe_core.compare import replay
    from pe_core.compare.day import Refused, load_day
    from pe_core.compare.run import scenario_for

    t0 = time.monotonic()
    day = date.fromisoformat(job["day"])
    base = {
        "runner": workspace.RUNNER_VERSION,
        "day": job["day"],
        "code": job["label"],
        "code_id": job["code_id"],
        "settings": job["settings"],
        "consts": job["consts"],
        "seed": job["seed_names"],
        "fresh": job["fresh"],
    }
    with tempfile.TemporaryDirectory(prefix="pe-sim-") as tmp:
        save = workspace.assemble_save_dir(
            Path(tmp) / "save", Path(job["data"]), {k: v and Path(v) for k, v in job["seed"].items()}, job["fresh"]
        )
        costs = str(save / "costs")
        try:
            day_input = load_day(costs, day)
        except Refused as why:
            return {**base, "status": why.status, "reason": why.reason}
        apply_consts(job["consts"])
        sc = scenario_for(day_input, str(save), tmp, 24.0)
        original = sc.config_text
        sc.config_text = lambda engine: (
            patch_config(original(engine), job["settings"]) if engine == "v2" else original(engine)
        )
        plans, last = [], [None]

        def on_tick(app, world, t):
            eng = getattr(app, "_v2", None)
            out = eng.last_output if eng is not None else None
            if out is None or out is last[0]:
                return
            last[0] = out
            if out.revalued and out.value is not None:
                plans.append(_plan(out.value, t, sc.tz))

        v2 = replay.run_app(sc, "v2", on_tick=on_tick)
        if v2.get("real_calls"):
            raise RuntimeError("the replay reached for Home Assistant")
        su = replay.run_selfuse(sc, v2["event_pay"])
        bound = replay.perfect_bound(sc, v2["facts"], v2["settings"])
        cap = sc.capacity_kwh
        cheapest = min(sc.pack["days"][sc.day]["act"])
        for run_ in (su, v2, bound):
            run_["endval_gbp"] = (run_["end_soc"] - su["end_soc"]) / 100 * cap * cheapest
            run_["adj_cost_gbp"] = run_["cost_gbp"] - run_["endval_gbp"]
        world = sc.world(feed=False)
        score = {
            "cost": _r(v2["cost_gbp"]),
            "adj_cost": _r(v2["adj_cost_gbp"]),
            "end_soc": _r(v2["end_soc"], 1),
            "selfuse_cost": _r(su["cost_gbp"]),
            "selfuse_adj": _r(su["adj_cost_gbp"]),
            "bound_cost": _r(bound["cost_gbp"]),
            "bound_adj": _r(bound["adj_cost_gbp"]),
            "save_vs_selfuse": _r(su["adj_cost_gbp"] - v2["adj_cost_gbp"]),
            "gap_to_bound": _r(v2["adj_cost_gbp"] - bound["adj_cost_gbp"]),
            "mode_changes": v2["mode_changes"],
            "flip_flops": v2["flip_flops"],
            "commands": v2["commands"],
            "failsafes": v2["failsafes"],
            "peak_charge_kwh": _r(v2["peak_charge_kwh"], 2),
            "min_soc": _r(v2["min_soc"], 1),
            "import_kwh": _r(v2["import_kwh"], 2),
            "export_kwh": _r(v2["export_kwh"], 2),
            "revalues": v2.get("revalues"),
            "errors": v2.get("errors"),
            "calc_s": _r(sum(p["calc_s"] or 0 for p in plans), 1),
            "wall_s": v2["wall_s"],
        }
        series = {
            "rows": _rows(world, day, sc.tz),
            "trace": v2["trace"],
            "changes": v2["changes"],
            "plans": plans,
            "selfuse_trace": su["trace"],
            "capacity_kwh": cap,
            "tz": str(sc.tz.key),
            "journal": [r for r in v2.get("journal", []) if r.get("kind") in ("mode", "error", "revalue")][:400],
        }
        return {
            **base,
            "status": "ok",
            "reason": "",
            "score": score,
            "series": series,
            "took_s": round(time.monotonic() - t0, 1),
        }


def main(argv) -> int:
    if len(argv) >= 3 and argv[1] == "catalogue":
        print(json.dumps(catalogue(argv[2])))
        return 0
    if len(argv) == 4 and argv[1] == "run":
        job = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
        try:
            result = run(job)
        except Exception as err:
            result = {
                "day": job.get("day"),
                "status": "failed",
                "reason": f"{type(err).__name__}: {err}",
                "trace": traceback.format_exc()[-1500:],
            }
        tmp = f"{argv[3]}.tmp"
        Path(tmp).write_text(json.dumps(result, separators=(",", ":"), default=str), encoding="utf-8")
        os.replace(tmp, argv[3])
        return 0 if result.get("status") in ("ok", "no_snapshot", "incomplete") else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
