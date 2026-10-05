#!/usr/bin/env python3
"""Engine v1 against engine v2 on the demo pack's recorded days, closed loop (engine v2 build, work package E).

For each day the WHOLE app runs in demo mode (a fake AppDaemon and a fake clock; the demo world plays the recorded
house, sun, prices, smart slots and grid events and simulates the battery), Active, RAM remote control, from the same
start, once with engine v1 and once with engine v2. The app's own run_every callbacks (the 30 s cycle, v2's 10 s tick,
the 5 min input check, the heartbeat) are called at their intervals, and the world is stepped every 10 s, so both
engines are judged on the same outside world. Each run is costed from the world's own flows:

    grid import x the import rate in force (smart-slot rates included)
    - grid export x the export rate
    - battery energy exported in a grid event x the event pay (axle_value + export rate, the same for both engines)

Also: plain self-use (the world with no commands), and a perfect-foresight bound: engine v2's value.solve on the
actual day (recorded house, sun and prices as the forecast with no spread, slots as they happened, the car held
against the battery as the rules say) and its forward run.

The end battery level is valued at the day's cheapest import rate (a run that ends fuller is not penalised): "endval"
is (end level - self-use's end level) x capacity x that rate, and the "adjusted" figures add it to the cash ones.

    tools/engine_compare.py [--days sunny dull axle car] [--hours 24] [--start HH:MM] [--json]
"""

from __future__ import annotations

import argparse
import contextlib
import json
import pathlib
import shutil
import sys
import tempfile
import time
import types
from datetime import date, datetime, timedelta, timezone
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (ROOT / "apps" / "powerengine", ROOT / "tests"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from replay_harness import Clock, FrozenDatetime, _fake_appdaemon  # noqa: E402

BST = timezone(timedelta(hours=1))
DAY = date(2026, 10, 6)                    # a BST day; every pack day is played on it
TICK_S = 10                                # the world and the callbacks advance in 10 s steps
PEAK_P = 0.20                              # GBP/kWh: "bought at the peak rate"
FLIP_WINDOW = timedelta(minutes=10)
DAYS = ("sunny", "dull", "axle", "car")
RUN_NAMES = {"_cycle", "_engine_tick", "_beat", "<lambda>"}     # the run_every callbacks the harness calls


# --- the app, against a fake AppDaemon and a fake clock ---------------------------------------------------------------

def _hass_class():
    """The fake AppDaemon Hass of tests/replay_harness.py, recording what the app registers with run_every and anything
    that would reach the real Home Assistant."""
    Base = _fake_appdaemon()

    class Hass(Base):
        def __init__(self):
            super().__init__()
            self.every, self.real_calls, self.real_events, self.real_sets = [], [], [], []

        def run_every(self, callback, start=None, interval=None, **kw):
            self.every.append((callback, float(interval)))
            return f"every-{len(self.every)}"

        def set_state(self, entity_id, state=None, attributes=None, **kw):
            self.real_sets.append(entity_id)
            self.set_fake(entity_id, state, attributes)

        def call_service(self, service, **kw):
            self.real_calls.append(service)

        def fire_event(self, event, **kw):
            self.real_events.append(event)

        def get_plugin_api(self, name):
            raise AssertionError("demo mode must not look for the MQTT plugin")

    return Hass


@contextlib.contextmanager
def loaded_app(engine: str):
    """`powerengine` imported against the fake AppDaemon, the clock patched in, and the demo config copied with the
    engine chosen (Active and RAM remote control are the demo template's own settings; checked by the caller)."""
    stubs = {}
    hassapi = types.ModuleType("appdaemon.plugins.hass.hassapi")
    hassapi.Hass = _hass_class()
    for name in ("appdaemon", "appdaemon.plugins", "appdaemon.plugins.hass"):
        stubs[name] = types.ModuleType(name)
    stubs["appdaemon.plugins.hass.hassapi"] = hassapi
    saved = {name: sys.modules.get(name) for name in (*stubs, "powerengine")}
    real_copy = shutil.copyfile

    def copy(src, dst, **kw):
        if str(src).endswith("config.template"):
            text = open(src, encoding="utf-8").read()
            if engine == "v2":
                text = text.replace("  publisher: direct\n", "  publisher: direct\n  engine: v2\n")
            with open(dst, "w", encoding="utf-8") as fh:
                fh.write(text)
            return dst
        return real_copy(src, dst, **kw)

    with contextlib.ExitStack() as stack:
        sys.modules.update(stubs)
        sys.modules.pop("powerengine", None)
        stack.callback(lambda: [sys.modules.pop(n, None) if v is None else sys.modules.__setitem__(n, v)
                                for n, v in saved.items()])
        stack.enter_context(mock.patch.object(shutil, "copyfile", copy))
        import powerengine
        for name, mod in list(sys.modules.items()):
            if (name == "powerengine" or name.startswith("pe_core")) and getattr(mod, "datetime", None) is datetime:
                stack.enter_context(mock.patch.object(mod, "datetime", FrozenDatetime))
        yield powerengine


# --- costing from the world's own flows -------------------------------------------------------------------------------

class Account:
    """Hooks a DemoWorld's `_integrate` (called once per world step with the flows that were just applied) and costs
    each step at the rates of that half-hour."""

    def __init__(self, world, event_value: float, event_plus_export: bool):
        self.world, self.event_value, self.plus = world, event_value, event_plus_export
        self.import_kwh = self.export_kwh = self.event_kwh = self.cost = 0.0
        self.event_pay = self.peak_charge_kwh = 0.0
        self.min_soc = world.soc
        self.soc0 = world.soc
        orig = world._integrate

        def integrate(dt_h):
            self._add(dt_h)
            orig(dt_h)
            self.min_soc = min(self.min_soc, world.soc)
        world._integrate = integrate

    def _add(self, dt_h):
        w = self.world
        f, row = w.flows, w._row_now
        imp = max(f["grid_w"], 0.0) * dt_h / 1000
        exp = max(-f["grid_w"], 0.0) * dt_h / 1000
        dis = f["discharge_w"] * dt_h / 1000
        act, exr = row["act"], row["exp"]
        ev = min(exp, dis) if row["axle"] else 0.0            # battery energy sold into a grid event
        pay = self.event_value + (exr if self.plus else 0.0)
        self.import_kwh += imp
        self.export_kwh += exp
        self.event_kwh += ev
        self.event_pay += ev * pay
        self.cost += imp * act - (exp - ev) * exr - ev * pay
        if act >= PEAK_P:                                     # grid energy that went into the battery, at the peak
            self.peak_charge_kwh += min(f["charge_w"] * dt_h / 1000, imp)

    def summary(self):
        return {"cost_gbp": self.cost, "import_kwh": self.import_kwh, "export_kwh": self.export_kwh,
                "event_kwh": self.event_kwh, "event_pay_gbp": self.event_pay, "peak_charge_kwh": self.peak_charge_kwh,
                "min_soc": self.min_soc, "end_soc": self.world.soc, "start_soc": self.soc0}


def mode_key(world) -> str:
    if world.option == "Force charge":
        return "hold" if world.charge_w <= 0 else "charge"
    return "discharge" if world.option == "Force discharge" else "self"


class Tracker:
    """What the inverter was told to do: mode changes, command changes (a new mode or a new power: what the app's
    RAM controller counts as a change, kept here because it prunes its own count at midnight), flip-flops (A -> B -> A,
    B lasting at most 10 min) and a 5-minute trace of the level."""

    def __init__(self, world, start):
        self.world = world
        self.changes: list[tuple[datetime, str]] = []
        self.last = mode_key(world)
        self.command = (world.option, world.charge_w, world.discharge_w)
        self.commands = 0
        self.trace: list[tuple[str, float, str, float, float]] = []
        self._next_trace = start

    def look(self, t):
        w = self.world
        key = mode_key(w)
        command = (w.option, w.charge_w, w.discharge_w)
        if command != self.command:
            self.commands += 1
            self.command = command
        if key != self.last:
            self.changes.append((t, key))
            self.last = key
        if t >= self._next_trace:
            self.trace.append((t.astimezone(BST).strftime("%H:%M"), round(w.soc, 1), key, w.charge_w, w.discharge_w))
            self._next_trace = t + timedelta(minutes=5)

    def flip_flops(self) -> int:
        keys = ["self"] + [k for _, k in self.changes]
        times = [None] + [t for t, _ in self.changes]
        return sum(1 for j in range(2, len(keys)) if keys[j] == keys[j - 2] and times[j] - times[j - 1] <= FLIP_WINDOW)


# --- one run of the whole app -----------------------------------------------------------------------------------------

def run_app(day: str, engine: str, start: datetime, hours: float, backfill: bool = True, on_tick=None) -> dict:
    t_wall = time.perf_counter()
    with tempfile.TemporaryDirectory() as tmp, loaded_app(engine) as powerengine:
        folder = pathlib.Path(tmp) / "powerengine"
        folder.mkdir()
        (folder / "config.yaml").write_text("# the owner's config\n")
        app = powerengine.PowerEngine()
        app.args = {"demo": day, "settings_file": str(folder / "config.yaml")}
        Clock.now, app.tz = start, None
        app.initialize()
        if app.mode.effective != "active" or app.cfg.system.get("control_method") != "ram_remote":
            raise RuntimeError(f"the demo config is not Active on RAM control ({app.mode.effective}, "
                               f"{app.cfg.system.get('control_method')})")
        if app._engine_name() != engine:
            raise RuntimeError(f"engine {engine} was not chosen (the app runs {app._engine_name()})")
        world = app._demo_world()
        p = app._params()
        v2s = app.cfg.engine_v2
        pay_v1 = (p.axle_value * 100, p.axle_plus_export)
        pay_v2 = (v2s.event_value_p, v2s.event_plus_export)
        if pay_v1 != pay_v2:
            raise RuntimeError(f"the engines are told different event pay: v1 {pay_v1}, v2 {pay_v2}")
        acc = Account(world, pay_v1[0] / 100, pay_v1[1])
        track = Tracker(world, start)
        calls = [[cb, iv, start + timedelta(seconds=iv)] for cb, iv in app.every
                 if getattr(cb, "__name__", "") in RUN_NAMES]
        end = start + timedelta(hours=hours)
        t = start
        v2_rows, v2_last, causes, seen_causes, seen_day = [], None, {}, {}, None
        while t < end:
            t += timedelta(seconds=TICK_S)
            Clock.now = t
            world.step(t)
            due = sorted((x for x in app.timers if x[0] <= t), key=lambda x: x[0])
            app.timers = [x for x in app.timers if x[0] > t]
            for _, cb, kw, _h in due:
                if backfill or getattr(cb, "__name__", "") not in ("_backfill", "_backfill_day"):
                    cb(kw)
            for c in calls:
                while c[2] <= t:
                    c[2] += timedelta(seconds=c[1])
                    c[0]({})
            track.look(t)
            if on_tick is not None:                              # a hook for looking inside the app (investigations)
                on_tick(app, world, t)
            if engine == "v2" and app._v2 is not None:
                out = app._v2.last_output
                if out is not None and out is not v2_last:
                    v2_last = out
                    v2_rows.extend(out.journal)
                today = app._v2.triggers.today
                if today.get("day") != seen_day:
                    seen_day, seen_causes = today.get("day"), {}
                for k, v in today.get("causes", {}).items():
                    causes[k] = causes.get(k, 0) + v - seen_causes.get(k, 0)
                    seen_causes[k] = v
        res = acc.summary()
        res.update(
            engine=engine, day=day, mode_changes=len(track.changes), flip_flops=track.flip_flops(),
            commands=track.commands, failsafes=len(world.events),
            real_calls=len(app.real_calls) + len(app.real_events), trace=track.trace,
            changes=[(c[0].astimezone(BST).strftime("%H:%M:%S"), c[1]) for c in track.changes],
            wall_s=round(time.perf_counter() - t_wall, 1), event_pay=pay_v1,
        )
        if engine == "v2":
            res["revalues"] = sum(causes.values())
            res["revalue_causes"] = dict(sorted(causes.items(), key=lambda kv: -kv[1]))
            res["journal"] = v2_rows
            res["errors"] = sum(1 for r in v2_rows if r.get("kind") == "error")
            res["facts"] = app._v2.last_facts
            res["settings"] = app._v2.s
        return res


def run_selfuse(day: str, start: datetime, hours: float, event_pay: tuple[float, bool]) -> dict:
    """Plain self-use: the demo world with no commands at all."""
    from pe_core.demo import pack as packmod
    from pe_core.demo import world as worldmod
    pack = packmod.load_pack()
    Clock.now = start
    world = worldmod.DemoWorld(pack, day, BST, lambda: Clock.now)
    acc = Account(world, event_pay[0] / 100, event_pay[1])
    track = Tracker(world, start)
    t, end = start, start + timedelta(hours=hours)
    while t < end:
        t += timedelta(seconds=TICK_S)
        Clock.now = t
        world.step(t)
        track.look(t)
    res = acc.summary()
    res.update(engine="selfuse", day=day, mode_changes=0, flip_flops=0, commands=0, failsafes=0, trace=track.trace)
    return res


# --- the perfect-foresight bound --------------------------------------------------------------------------------------

def perfect_bound(day: str, start: datetime, hours: float, facts, settings) -> dict:
    """Engine v2's value.solve on the actual day: house, sun and prices as they happened as the forecast with no
    spread, the smart slots as they were (their price is the price), grid events as they were, the car as drawn (the
    rules hold the battery while it charges). Cost in cash from the forward run's own records, plus the figure
    value.solve gives."""
    from pe_core.demo import pack as packmod
    from pe_core.demo import world as worldmod
    from pe_core.engine_v2 import rules, value
    from pe_core.engine_v2.types import Forecast, Segment, Spread
    pack = packmod.load_pack()
    Clock.now = start
    world = worldmod.DemoWorld(pack, day, BST, lambda: Clock.now)
    t0 = start.astimezone(timezone.utc)
    t1 = t0 + timedelta(hours=hours)
    rows, d = [], world._local(t0).date()
    while world._local(t0 + timedelta(seconds=1)).date() <= d <= world._local(t1).date():
        rows += world.rows_for(d)
        d += timedelta(days=1)
    segs = []
    for row in rows:
        a, b = max(row["start"].astimezone(timezone.utc), t0), min(row["end"].astimezone(timezone.utc), t1)
        if b <= a:
            continue
        span = (row["end"] - row["start"]).total_seconds()
        frac = (b - a).total_seconds() / span
        hours_ = (b - a).total_seconds() / 3600
        exp_p = row["exp"] * 100
        is_event = bool(row["axle"] and settings.events)
        is_free = bool(row["free"] and settings.free_power)
        segs.append(Segment(
            start=a, end=b, import_p=row["act"] * 100, export_p=exp_p,
            solar_kwh=Spread(*(row["solar"] * frac,) * 3), load_kwh=Spread(*(row["house"] * frac,) * 3),
            event=is_event, event_p=(settings.event_value_p + (exp_p if settings.event_plus_export else 0.0))
            if is_event else 0.0, free=is_free, car_kw=(row["car"] * frac / hours_) if row["car"] > 1e-9 else 0.0))
    fc = Forecast(made_at=t0, segments=tuple(segs), notes=("perfect foresight",))
    soc0 = world.soc

    def limits_for(seg):
        return rules.limits_for(seg, facts, settings, house_load_includes_ev=True)

    t_wall = time.perf_counter()
    vr = value.solve(fc, soc0, facts, settings, limits_for, t0, "perfect foresight", BST)
    out = {"cost_expected_gbp": vr.cost_expected_p / 100, "selfuse_model_gbp": vr.cost_selfuse_p / 100,
           "start_soc": soc0, "timeline": [(i.mode, i.start.astimezone(BST).strftime("%H:%M"),
                                           i.end.astimezone(BST).strftime("%H:%M"), i.level_start, i.level_end)
                                          for i in vr.timeline]}
    try:                                                      # the forward run's own records: cash without the credit
        core = value._backward(fc, facts, settings, limits_for, t0, settings.level_step_kwh, value.COARSE_STEP_KWH)
        n_fine, step = value._grid(facts.capacity_kwh, settings.level_step_kwh)
        lam = tuple(value._fine_lam(core.V[k], n_fine, step) for k in range(len(core.segs)))
        recs, total = value._forward(core, lam, step, soc0 / 100 * facts.capacity_kwh, "mid", settings.price_band_p)
        cash = 0.0
        peak = 0.0
        for rec in recs:
            S = core.segs[rec["k"]]
            cash += rec["imp"] * rec["imp_p"] - (rec["exp"] - rec["evx"]) * S.export_p - rec["evx"] * S.event_p
            if rec["imp_p"] >= PEAK_P * 100:
                peak += max(0.0, rec["gtb"])
        out.update(cost_gbp=cash / 100, end_soc=recs[-1]["e1"] / facts.capacity_kwh * 100, peak_charge_kwh=peak,
                   mode_changes=sum(1 for a, b in zip(recs, recs[1:], strict=False) if a["mode"] != b["mode"]),
                   min_soc=min(min(r["e0"], r["e1"]) for r in recs) / facts.capacity_kwh * 100, exact=True,
                   check_total=abs(total - vr.cost_expected_p))
    except Exception as err:                                  # the private records changed: use the public figure
        tv = value._terminal_p(fc, facts, settings)
        end_soc = vr.path["mid"][-1] if vr.path.get("mid") else soc0
        out.update(cost_gbp=vr.cost_expected_p / 100 + tv * end_soc / 100 * facts.capacity_kwh / 100,
                   end_soc=end_soc, peak_charge_kwh=None, mode_changes=None, min_soc=None, exact=False,
                   note=f"records not available ({err!r}); wear and comfort not separated")
    out["wall_s"] = round(time.perf_counter() - t_wall, 1)
    return out


# --- a day, the table -------------------------------------------------------------------------------------------------

def compare_day(day: str, start: datetime, hours: float, say=lambda *a: None, backfill: bool = True) -> dict:
    say(f"{day}: engine v1 ...")
    v1 = run_app(day, "v1", start, hours, backfill)
    say(f"{day}: engine v2 ...")
    v2 = run_app(day, "v2", start, hours, backfill)
    say(f"{day}: self-use and the bound ...")
    su = run_selfuse(day, start, hours, v1["event_pay"])
    bound = perfect_bound(day, start, hours, v2["facts"], v2["settings"])
    from pe_core.demo import pack as packmod
    from pe_core.demo import world as worldmod
    cap = worldmod.CAPACITY_KWH
    pack = packmod.load_pack()
    cheapest = min(pack["days"][day]["act"])
    out = {"day": day, "cheapest_rate": cheapest, "runs": {"selfuse": su, "v1": v1, "v2": v2, "bound": bound}}
    for run in out["runs"].values():
        run["endval_gbp"] = (run["end_soc"] - su["end_soc"]) / 100 * cap * cheapest
        run["adj_cost_gbp"] = run["cost_gbp"] - run["endval_gbp"]
        run["save_gbp"] = su["cost_gbp"] - run["cost_gbp"]
    for run in out["runs"].values():
        run["adj_save_gbp"] = su["adj_cost_gbp"] - run["adj_cost_gbp"]
        run["gap_to_bound_gbp"] = run["adj_cost_gbp"] - bound["adj_cost_gbp"]
    return out


COLUMNS = (("day", 6), ("run", 8), ("cost", 8), ("save", 7), ("end%", 6), ("endval", 7), ("adjsave", 8),
           ("cmds", 5), ("flips", 5), ("peakkWh", 8), ("min%", 6), ("gap", 7), ("revals", 6))


def table(results: list[dict]) -> str:
    def line(cells):
        pairs = enumerate(zip(cells, COLUMNS, strict=True))
        return "  ".join(str(c).rjust(w) if i > 1 else str(c).ljust(w) for i, (c, (_, w)) in pairs)

    def f(x, n=2, sign=False):
        return "-" if x is None else (f"{x:+.{n}f}" if sign else f"{x:.{n}f}")
    lines = [line([n for n, _ in COLUMNS])]
    totals: dict[str, dict] = {}
    for res in results:
        for name, run in res["runs"].items():
            if name == "bound":
                cmds, flips = run.get("mode_changes"), 0
            else:
                cmds, flips = run.get("commands"), run.get("flip_flops")
            lines.append(line([res["day"] if name == "selfuse" else "", name, f(run["cost_gbp"]),
                               f(run["save_gbp"], 2, True), f(run["end_soc"], 1), f(run["endval_gbp"], 2, True),
                               f(run["adj_save_gbp"], 2, True),
                               "-" if cmds is None else cmds, "-" if name in ("bound", "selfuse") else flips,
                               f(run.get("peak_charge_kwh"), 1), f(run.get("min_soc"), 1),
                               f(run["gap_to_bound_gbp"], 2, True), run.get("revalues", "-")]))
            t = totals.setdefault(name, {"cost": 0.0, "save": 0.0, "endval": 0.0, "adj": 0.0, "cmds": 0, "flips": 0,
                                         "peak": 0.0, "gap": 0.0, "rev": 0})
            t["cost"] += run["cost_gbp"]
            t["save"] += run["save_gbp"]
            t["endval"] += run["endval_gbp"]
            t["adj"] += run["adj_save_gbp"]
            t["cmds"] += cmds or 0
            t["flips"] += flips or 0
            t["peak"] += run.get("peak_charge_kwh") or 0.0
            t["gap"] += run["gap_to_bound_gbp"]
            t["rev"] += run.get("revalues", 0)
    if len(results) > 1:
        lines.append("")
        for i, (name, t) in enumerate(totals.items()):
            lines.append(line(["TOTAL" if i == 0 else "", name, f(t["cost"]), f(t["save"], 2, True), "",
                               f(t["endval"], 2, True), f(t["adj"], 2, True), t["cmds"],
                               "-" if name in ("bound", "selfuse") else t["flips"], f(t["peak"], 1), "",
                               f(t["gap"], 2, True), t["rev"] or "-"]))
    lines += ["", "cost: GBP from the world's flows (no standing charge); save: against self-use; endval: end level "
              "against self-use's, at the day's cheapest import rate;",
              "adjsave: save + endval; cmds: RAM command changes sent (bound: mode changes of its run); flips: A->B->A "
              "within 10 min as the inverter saw it;",
              "peakkWh: grid energy put into the battery at 20p or more; gap: adjusted cost minus the bound's."]
    for res in results:
        v2 = res["runs"]["v2"]
        lines.append(f"{res['day']}: v2 revalues by cause {v2['revalue_causes']} (errors {v2['errors']})")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", nargs="+", default=list(DAYS), choices=DAYS)
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--start", default="00:00", help="local start time, HH:MM (default 00:00)")
    ap.add_argument("--no-backfill", action="store_true",
                    help="skip the app's 14-day cost backfill at start (about 30 s a run; the load profile is kept)")
    ap.add_argument("--json", action="store_true", help="print the full result as JSON")
    args = ap.parse_args(argv)
    hh, mm = (int(x) for x in args.start.split(":"))
    start = datetime(DAY.year, DAY.month, DAY.day, hh, mm, tzinfo=BST)
    say = (lambda *a: None) if args.json else (lambda *a: print(*a, file=sys.stderr, flush=True))
    results = [compare_day(day, start, args.hours, say, not args.no_backfill) for day in args.days]
    if args.json:
        def clean(o):
            if isinstance(o, dict):
                return {k: clean(v) for k, v in o.items() if k not in ("facts", "settings")}
            if isinstance(o, (list, tuple)):
                return [clean(v) for v in o]
            return o if isinstance(o, (int, float, str, bool, type(None))) else str(o)
        print(json.dumps(clean(results), indent=1))
    else:
        print(table(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
