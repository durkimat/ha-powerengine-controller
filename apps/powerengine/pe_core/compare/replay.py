"""One day through the whole app, closed loop, in the demo world: engine v1, engine v2, plain self-use and a
perfect-foresight bound, each costed from the world's own flows (`tools/engine_compare.py`'s method, which the demo
days proved; docs/plans/engine-pages-and-comparison.md, 2.1).

For each engine the WHOLE app runs in demo mode (a fake AppDaemon and a fake clock; the demo world plays the day's
house, sun, prices, smart slots and grid events and simulates the battery), Active, RAM remote control. The app's own
run_every callbacks (the 30 s cycle, v2's tick, the 5 min input check, the heartbeat) are called at their intervals and
the world is stepped every 10 s. Each run is costed from the world's flows:

    grid import x the import rate in force (smart-slot rates included)
    - grid export x the export rate
    - battery energy exported in a grid event x the event pay (axle_value + export rate, the same for both engines)

The end battery level is valued at the day's cheapest import rate: "endval" is (end level - self-use's end level) x
capacity x that rate, and the "adjusted" figures add it to the cash ones.

A `Scenario` says what to play: a pack (the demo's, or a real day as a one-day pack), the world's battery, an optional
snapshot feed (the forecasts as they were), the house profile and smart-slot first-seen times as they were, and the
settings to run with. The replayed app learns nothing from the world's history when a profile is given.
"""

from __future__ import annotations

import contextlib
import pathlib
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from unittest import mock

from ..demo import world as worldmod
from .harness import Clock, loaded_app

TICK_S = 10                                # the world and the callbacks advance in 10 s steps
PEAK_P = 0.20                              # GBP/kWh: "bought at the peak rate"
FLIP_WINDOW = timedelta(minutes=10)
RUN_NAMES = {"_cycle", "_engine_tick", "_beat", "<lambda>"}     # the run_every callbacks the harness calls
BACKFILL_TIMERS = ("_backfill", "_backfill_day")
LEARN_TIMERS = ("_learn_load", "_learn_load_day")


@dataclass
class Scenario:
    pack: dict
    day: str
    tz: object
    start: datetime
    hours: float = 24.0
    backfill: bool = True                  # the app's 14-day cost backfill at start (about 30 s a run)
    config_text: object = None             # callable(engine) -> config.yaml text; None: the demo template
    battery: dict = field(default_factory=dict)       # DemoWorld keywords: capacity_kwh, efficiency, limits, floor
    feed: object = None                    # SnapshotFeed: the forecasts, rates and slots as they were
    profile_at: object = None              # callable(when) -> LoadProfile | None: the house profile as it was
    first_seen_at: object = None           # callable(when) -> {slot start iso: first seen iso}
    extra_files: dict = field(default_factory=dict)   # {relative path in the settings folder: source file}
    real_pack: bool = False                # the pack is not the shipped one: the app must be given it

    def world(self, now_fn=None, feed=True):
        return worldmod.DemoWorld(self.pack, self.day, self.tz, now_fn or (lambda: Clock.now), **self.battery,
                                  feed=self.feed if feed else None)

    @property
    def capacity_kwh(self):
        return self.battery.get("capacity_kwh", worldmod.CAPACITY_KWH)


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

    def __init__(self, world, start, tz):
        self.world, self.tz = world, tz
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
            self.trace.append((t.astimezone(self.tz).strftime("%H:%M"), round(w.soc, 1), key, w.charge_w,
                               w.discharge_w))
            self._next_trace = t + timedelta(minutes=5)

    def flip_flops(self) -> int:
        keys = ["self"] + [k for _, k in self.changes]
        times = [None] + [t for t, _ in self.changes]
        return sum(1 for j in range(2, len(keys)) if keys[j] == keys[j - 2] and times[j] - times[j - 1] <= FLIP_WINDOW)


# --- the replayed app's inputs as they were ---------------------------------------------------------------------------

class SlotSeen:
    """Puts the smart slots' first-seen times (when the supplier announced them) on the app's slot records: the replayed
    app sees a slot announced the day before for the first time at midnight."""

    def __init__(self, source):
        self.source, self._cache_id, self._by_start = source, None, {}

    def apply(self, app, when):
        if self.source is None:
            return
        seen = self.source(when)
        if id(seen) != self._cache_id:
            self._cache_id = id(seen)
            self._by_start = {}
            for start, first in seen.items():
                with contextlib.suppress(ValueError, TypeError):
                    self._by_start[datetime.fromisoformat(start)] = first
        if not self._by_start:
            return
        for rec in app.slots.slots.values():
            try:
                first = self._by_start.get(datetime.fromisoformat(rec["start"]))
            except (KeyError, ValueError):
                continue
            if first is not None and rec.get("first_seen") != first:
                rec["first_seen"] = first


def _app_setup(sc: Scenario):
    """What the replayed app is given: the world with the scenario's battery and feed, and a house profile that comes
    from the snapshot (the app's own `_rebuild_profile`, which learns it from the world's history, is replaced)."""
    def setup(powerengine, stack):
        def factory(pack, day, tz, now_fn):
            return worldmod.DemoWorld(pack, day, tz, now_fn, **sc.battery, feed=sc.feed)
        stack.enter_context(mock.patch.object(powerengine, "DemoWorld", factory))
        if sc.profile_at is not None:
            def rebuild(self):                                # never from the world's history: the snapshot's, or none
                prof = sc.profile_at(Clock.now)
                if prof is not None and prof is not self.profile:
                    self.profile = prof
                    self._plan_sig = None
            stack.enter_context(mock.patch.object(powerengine.PowerEngine, "_rebuild_profile", rebuild))
    return setup


# --- one run of the whole app -----------------------------------------------------------------------------------------

def run_app(sc: Scenario, engine: str, on_tick=None) -> dict:
    t_wall = time.perf_counter()
    skip = set(LEARN_TIMERS if sc.profile_at is not None else ())
    if not sc.backfill:
        skip |= set(BACKFILL_TIMERS)
    with tempfile.TemporaryDirectory() as tmp, loaded_app(
            sc.config_text, engine, pack=sc.pack, extra_files=sc.extra_files, setup=_app_setup(sc)) as powerengine:
        folder = pathlib.Path(tmp) / "powerengine"
        folder.mkdir()
        (folder / "config.yaml").write_text("# the owner's config\n")
        app = powerengine.PowerEngine()
        app.args = {"demo": sc.day, "settings_file": str(folder / "config.yaml")}
        start = sc.start
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
        track = Tracker(world, start, sc.tz)
        seen = SlotSeen(sc.first_seen_at)
        last_profile = None
        calls = [[cb, iv, start + timedelta(seconds=iv)] for cb, iv in app.every
                 if getattr(cb, "__name__", "") in RUN_NAMES]
        end = start + timedelta(hours=sc.hours)
        t = start
        v2_rows, v2_last, causes, seen_causes, seen_day = [], None, {}, {}, None
        while t < end:
            t += timedelta(seconds=TICK_S)
            Clock.now = t
            world.step(t)
            if sc.profile_at is not None:
                prof = sc.profile_at(t)
                if prof is not None and prof is not last_profile:
                    last_profile = prof
                    app.profile, app._plan_sig = prof, None
            seen.apply(app, t)
            due = sorted((x for x in app.timers if x[0] <= t), key=lambda x: x[0])
            app.timers = [x for x in app.timers if x[0] > t]
            for _, cb, kw, _h in due:
                if getattr(cb, "__name__", "") not in skip:
                    cb(kw)
            for c in calls:
                while c[2] <= t:
                    c[2] += timedelta(seconds=c[1])
                    c[0]({})
            seen.apply(app, t)
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
            engine=engine, day=sc.day, mode_changes=len(track.changes), flip_flops=track.flip_flops(),
            commands=track.commands, failsafes=len(world.events),
            real_calls=len(app.real_calls) + len(app.real_events), trace=track.trace,
            changes=[(c[0].astimezone(sc.tz).strftime("%H:%M:%S"), c[1]) for c in track.changes],
            wall_s=round(time.perf_counter() - t_wall, 1), event_pay=pay_v1,
            profile_days=round(app.profile.days, 1) if getattr(app, "profile", None) else None,
        )
        if engine == "v2":
            res["revalues"] = sum(causes.values())
            res["revalue_causes"] = dict(sorted(causes.items(), key=lambda kv: -kv[1]))
            res["journal"] = v2_rows
            res["errors"] = sum(1 for r in v2_rows if r.get("kind") == "error")
            res["facts"] = app._v2.last_facts
            res["settings"] = app._v2.s
        return res


def run_selfuse(sc: Scenario, event_pay: tuple[float, bool]) -> dict:
    """Plain self-use: the demo world with no commands at all."""
    start = sc.start
    Clock.now = start
    world = sc.world(feed=False)
    acc = Account(world, event_pay[0] / 100, event_pay[1])
    track = Tracker(world, start, sc.tz)
    t, end = start, start + timedelta(hours=sc.hours)
    while t < end:
        t += timedelta(seconds=TICK_S)
        Clock.now = t
        world.step(t)
        track.look(t)
    res = acc.summary()
    res.update(engine="selfuse", day=sc.day, mode_changes=0, flip_flops=0, commands=0, failsafes=0, trace=track.trace)
    return res


# --- the perfect-foresight bound --------------------------------------------------------------------------------------

def perfect_bound(sc: Scenario, facts, settings) -> dict:
    """Engine v2's value.solve on the actual day: house, sun and prices as they happened as the forecast with no
    spread, the smart slots as they were (their price is the price), grid events as they were, the car as drawn (the
    rules hold the battery while it charges). Cost in cash from the forward run's own records, plus the figure
    value.solve gives."""
    from ..engine_v2 import rules, value
    from ..engine_v2.types import Forecast, Segment, Spread
    start, hours = sc.start, sc.hours
    Clock.now = start
    world = sc.world(feed=False)
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
    vr = value.solve(fc, soc0, facts, settings, limits_for, t0, "perfect foresight", sc.tz)
    out = {"cost_expected_gbp": vr.cost_expected_p / 100, "selfuse_model_gbp": vr.cost_selfuse_p / 100,
           "start_soc": soc0, "timeline": [(i.mode, i.start.astimezone(sc.tz).strftime("%H:%M"),
                                           i.end.astimezone(sc.tz).strftime("%H:%M"), i.level_start, i.level_end)
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


# --- a day ------------------------------------------------------------------------------------------------------------

def compare_day(sc: Scenario, say=lambda *a: None) -> dict:
    say(f"{sc.day}: engine v1 ...")
    v1 = run_app(sc, "v1")
    say(f"{sc.day}: engine v2 ...")
    v2 = run_app(sc, "v2")
    say(f"{sc.day}: self-use and the bound ...")
    su = run_selfuse(sc, v1["event_pay"])
    bound = perfect_bound(sc, v2["facts"], v2["settings"])
    cap = sc.capacity_kwh
    cheapest = min(sc.pack["days"][sc.day]["act"])
    out = {"day": sc.day, "cheapest_rate": cheapest, "runs": {"selfuse": su, "v1": v1, "v2": v2, "bound": bound}}
    for run in out["runs"].values():
        run["endval_gbp"] = (run["end_soc"] - su["end_soc"]) / 100 * cap * cheapest
        run["adj_cost_gbp"] = run["cost_gbp"] - run["endval_gbp"]
        run["save_gbp"] = su["cost_gbp"] - run["cost_gbp"]
    for run in out["runs"].values():
        run["adj_save_gbp"] = su["adj_cost_gbp"] - run["adj_cost_gbp"]
        run["gap_to_bound_gbp"] = run["adj_cost_gbp"] - bound["adj_cost_gbp"]
    return out
