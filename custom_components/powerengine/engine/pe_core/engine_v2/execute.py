"""Layer 5: execute. One mode at a time; the mode is the plan's own decision.

The plan and the executor make one decision, in one place (`value._choose`): the expected timeline is that decision
stepped forward over the forecast, and the executor makes the same call (`value.decide`) from the live level, the live
limits and the mode actually running, so the two differ only where the inputs do (docs/plans/engine-v2.md, section 8).

The executor decides at the start of each segment of the forecast, when the values are worked out again, and when the
rules change what is allowed or forced; in between it carries the decision out. A charge or a sale that stops inside
its segment runs until the level reaches where it stops, then the rest of the segment is the idle mode (Hold where it
is allowed, else Self-use), as the plan has it. Layer 4's forced modes (grid events, free power, the manual override,
the reserve) come first, and missing data gives Self-use, the inverter's own safe state. A deadline (the expected end
plus a grace) raises an event so the plan is looked at again; it never stops a mode.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timedelta, timezone

from .. import decide as v1d
from ..names import N, current
from .rules import LEVEL_BAND_PCT
from .settings import V2Settings
from .types import (
    CHARGE,
    EVENT,
    EXPORT,
    FREE,
    HOLD,
    MODE_ACTION,
    SELF_USE,
    BatteryFacts,
    Event,
    Exit,
    Limits,
    Lines,
    ModeState,
    Observation,
    ValueResult,
)
from .value import LEVEL_TOL_PCT, MAX_PIECES, decide

FLIP_FLOP_S = 600
LEVEL_EPS = LEVEL_TOL_PCT     # points: a mode that runs "until" a level is done within this of it. The filtered level
                              # only approaches a reading (99.999...% at 100%), so a bare `level < target` never ends it

DEADLINE_GRACE_MIN = 10            # how long a run may pass its expected end before a re-plan


@dataclass
class _Pick:
    mode: str
    rule: str
    why: str
    target_soc: float | None = None
    power_w: float | None = None
    forced: bool = False           # a forced or safety pick
    chosen_by: str | None = None


def _w(kw: float | None) -> float | None:
    return None if kw is None else float(round(kw * 1000))


def sentence_case_off(why: str) -> str:
    """The reason as v1's sentences read (lower-case start), unless it opens with a name from the names map."""
    if not why:
        return why
    names = {v.split()[0] for v in current().values() if v}
    first = why.split()[0]
    if first in names or why[:2].isupper() or first == "PowerEngine":
        return why
    return why[0].lower() + why[1:]


def _iso(ts: float | None) -> str | None:
    return None if ts is None else datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


def _lim_key(lim: Limits) -> list:
    """What of the rules a decision depends on: when any of it changes, the executor decides again."""
    return [sorted(lim.allowed), lim.forced, lim.rule, round(lim.floor_soc, 1), round(lim.ceiling_soc, 1)]


class Executor:
    def __init__(self, settings: V2Settings, state: dict | None = None):
        self.s = settings
        st = dict(state or {})
        self.mode: str | None = st.get("mode")
        self.since: float | None = st.get("since")
        self.leg_from: float | None = st.get("leg_from")           # the level the charge or sale running now began at
        self.why: str = st.get("why", "")
        self.rule: str = st.get("rule", "")
        self.chosen_by: str = st.get("chosen_by", "start")
        self.target_soc: float | None = st.get("target_soc")
        self.power_w: float | None = st.get("power_w")
        self.deadline: float | None = st.get("deadline")
        self.deadline_fired: bool = st.get("deadline_fired", False)
        self.dl_key: list | None = st.get("dl_key")
        self.sun_spare: bool | None = st.get("sun_spare")
        self.reserve_latched: bool = st.get("reserve_latched", False)
        self.event_floor_hit: bool = st.get("event_floor_hit", False)
        self.prev_rule: str = st.get("prev_rule", "")
        self.changes: list = list(st.get("changes") or [])         # [ts, from, to] of recent mode changes
        self.exits: list = list(st.get("exits") or [])
        self.dec: dict | None = st.get("dec")                      # the decision being carried out (`_follow`)
        lines = st.get("lines")
        known = {f.name for f in fields(Lines)}                    # a field an earlier release saved is dropped
        self.lines: Lines | None = Lines(**{k: v for k, v in lines.items() if k in known}) if lines else None
        self.flip_flop_now = False                                   # the last step was an A to B to A change
        self._tz = None
        self._ended = False                                          # this step's run reached its level
        self.last_decision: v1d.Decision | None = None

    # ---- the step -------------------------------------------------------------------------------
    def step(self, now: datetime, obs: Observation, lim: Limits, ln: Lines | None, vr: ValueResult | None,
             events: tuple[Event, ...], facts: BatteryFacts) -> tuple[ModeState, v1d.Decision, tuple[Event, ...], bool]:
        ts = now.timestamp()
        self.flip_flop_now = False
        new_events: list[Event] = []
        level = obs.level_filtered if obs.level_filtered is not None else obs.level_reported
        for e in events:
            if e.kind == "short_to_sun":
                self.sun_spare = True
            elif e.kind == "sun_to_short":
                self.sun_spare = False
        if self.sun_spare is None:
            self.sun_spare = obs.net_load_kw is not None and obs.net_load_kw < 0

        pick = self._pick_now(now, obs, lim, ln, vr, level, facts)
        cur = self.mode
        changed = cur != pick.mode
        if changed:
            chosen_by = pick.chosen_by or self._cause(events, cur, level, lim, ln)
            self._note_change(ts, cur, pick.mode)
            prior = self.target_soc
            if cur == CHARGE and level is not None and prior is not None and level >= prior - LEVEL_EPS:
                new_events.append(Event(now, "level_edge", f"The battery reached {level:.0f}%: the charge ends"))
            elif cur == EXPORT and level is not None \
                    and level <= (lim.floor_soc if prior is None else prior) + LEVEL_EPS:
                new_events.append(Event(now, "level_edge", f"The battery reached {level:.0f}%: the sale ends"))
            self.mode, self.since, self.chosen_by = pick.mode, ts, chosen_by
            self.leg_from = level if pick.mode in (CHARGE, EXPORT) else None
            self.deadline_fired = False
            self.dl_key = None
        self.why, self.rule = pick.why, "v2_" + pick.rule
        self.target_soc, self.power_w = pick.target_soc, pick.power_w
        if lim.rule == "reserve" and self.prev_rule != "reserve":
            new_events.append(Event(now, "reserve", lim.reason))
        if pick.rule == "event_floor" and self.prev_rule != "event_floor":
            new_events.append(Event(now, "reserve", pick.why))
        self.prev_rule = pick.rule if pick.rule in ("event_floor",) else lim.rule
        self.reserve_latched = lim.rule == "reserve" or pick.rule == "override_reserve"
        self.lines = ln

        self._deadline(now, ts, vr, facts, level, new_events)
        self.exits = [self._exit_dict(e) for e in self._exits(now, lim, ln, vr, level, facts)]

        decision = self._decision(pick, level, ln)
        self.last_decision = decision
        return self.mode_state(), decision, tuple(new_events), changed

    # ---- which mode now -------------------------------------------------------------------------
    def _pick_now(self, now, obs, lim, ln, vr, level, facts) -> _Pick:
        self._ended = False
        if not obs.data_ok or level is None:
            miss = ", ".join(obs.missing) or "the battery level"
            self.dec = None
            return _Pick(SELF_USE, "data_missing", f"Readings are missing ({miss}): the battery is handed back to the "
                                                   "inverter's own self-use until they return", forced=True,
                         chosen_by="data_missing")
        if lim.forced is not None:
            self.dec = None
            return self._forced(lim, ln, level, facts)
        self.event_floor_hit = False
        if vr is None:
            return _Pick(SELF_USE, "starting", "The first values are still being worked out: the battery stays on "
                                               "self-use until they are ready", forced=True, chosen_by="start")
        if ln is None:
            return _Pick(SELF_USE, "no_lines", "The live comparison could not be made: the battery stays on self-use",
                         forced=True)
        mode, target = self._follow(now, lim, vr, ln, level, facts)
        return self._pick_for(mode, lim, ln, target)

    def _follow(self, now: datetime, lim: Limits, vr: ValueResult, ln: Lines, level: float,
                facts: BatteryFacts) -> tuple[str, float | None]:
        """The plan's decision, carried out: (mode, the level it runs to). A decision stands until something changes:
        a charge or a sale runs to its level (across segments, until its stretch of one price ends), and reaching it
        brings the next decision at once (a new run, else the idle mode for the rest of the segment); idle time is
        decided again at each segment. New values, or rules that change what is allowed, bring a new decision at once.
        This is the plan's own walk (`value._walk`), at the live level."""
        seg = next((g for g in vr.forecast.segments if g.start <= now < g.end), None)
        if seg is None:
            self.dec = None
            return self._fallback(lim), None
        ts, seg_ts = now.timestamp(), seg.start.timestamp()
        key = [vr.made_at.isoformat(), _lim_key(lim), round(ln.import_p, 2), round(ln.export_p, 2)]
        d = self.dec
        if d is None or d.get("key") != key or ts >= d["until"]:
            running = self.mode if self.mode in (CHARGE, EXPORT) else None
            d = decide(vr, now, level, lim, facts, self.s, self.mode, self.leg_from if running else None,
                       import_p=ln.import_p, export_p=ln.export_p)
            if d is None:
                self.dec = None
                return self._fallback(lim), None
            self.dec = d = {"key": key, **d, "seg": seg_ts, "used": 0, "reached": False}
        elif d["seg"] != seg_ts:                           # a run going on into the next segment: its first mode there
            d["seg"], d["used"] = seg_ts, 0
        for _ in range(MAX_PIECES):
            d = self.dec
            mode, stop = d["mode"], d["stop"]
            if mode not in lim.allowed:                    # a mode the forecast forced but the rules now don't
                return self._fallback(lim, d["rest"]), None
            if mode not in (CHARGE, EXPORT):
                return mode, None
            if mode == CHARGE:
                edge = lim.ceiling_soc if stop is None else min(stop, lim.ceiling_soc)
                short = level < edge - LEVEL_EPS
            else:
                edge = lim.floor_soc if stop is None else max(stop, lim.floor_soc)
                short = level > edge + LEVEL_EPS
            if short and not d["reached"]:
                return mode, edge
            d["reached"] = True
            self._ended = True
            idle = {"mode": d["rest"], "stop": None, "until": seg.end.timestamp()}
            if d["used"] + 1 >= MAX_PIECES - 1:                # the segment's last mode: the rest of it idles
                self.dec = {**d, **idle, "used": d["used"] + 1, "reached": False}
                continue
            # the run reached its level: the plan's next decision, now (`value._walk`)
            # the run reached its level, so its shortest run is done (the walk does the same: `leg` ends there)
            nd = decide(vr, now, level, lim, facts, self.s, mode, None, used=d["used"] + 1, last_run=mode,
                        import_p=ln.import_p, export_p=ln.export_p)
            self.dec = {"key": key, **(nd or {**d, **idle}), "seg": seg_ts, "used": d["used"] + 1, "reached": False}
        return self._fallback(lim, self.dec["rest"]), None

    @staticmethod
    def _fallback(lim: Limits, want: str | None = None) -> str:
        if want is not None and want in lim.allowed:
            return want
        if SELF_USE in lim.allowed:
            return SELF_USE
        return HOLD if HOLD in lim.allowed else sorted(lim.allowed)[0]

    def _forced(self, lim: Limits, ln, level: float, facts) -> _Pick:
        s = self.s
        why0 = lim.reason
        if lim.forced == EVENT:
            floor = lim.floor_soc
            if level <= floor + 1e-9:
                self.event_floor_hit = True
            if self.event_floor_hit:
                return _Pick(HOLD, "event_floor", f"{N('event')} event: the battery is at {level:.0f}%, the lowest "
                             f"it may go ({floor:.0f}%, the hard floor plus a margin): holding so the battery's own "
                             "cut-off never ends it", target_soc=floor, forced=True)
            pay = s.event_value_p + (ln.export_p if (ln is not None and s.event_plus_export) else 0.0)
            return _Pick(EVENT, "event", f"{N('event')} event in progress (pays {pay:.0f}p per kWh exported): selling "
                         f"from the battery down to {floor:.0f}%", target_soc=floor, power_w=_w(lim.discharge_cap_kw),
                         forced=True)
        self.event_floor_hit = False
        if lim.forced == FREE:
            return self._forced_charge(lim, level, "free", "Free-power session: filling the battery to "
                                       f"{lim.ceiling_soc:.0f}%", forced_mode=FREE)
        if lim.forced == HOLD:
            price = f" at {ln.import_p:.2f}p" if ln is not None else ""
            return _Pick(HOLD, "override", f"{why0}: the grid runs the house{price}", forced=True)
        if lim.forced in (SELF_USE, EXPORT):
            hold_at = lim.floor_soc + (LEVEL_BAND_PCT if self.reserve_latched else 0.0)
            if level <= hold_at:
                return _Pick(HOLD, "override_reserve", f"{why0}, but the battery is at its {lim.floor_soc:.0f}% "
                             "reserve: it holds", forced=True)
            what = "the battery covers the house" if lim.forced == SELF_USE else "selling from the battery"
            return _Pick(lim.forced, "override", f"{why0}: {what}", forced=True,
                         power_w=_w(lim.discharge_cap_kw) if lim.forced == EXPORT else None,
                         target_soc=lim.floor_soc if lim.forced == EXPORT else None)
        if lim.forced == CHARGE:
            return self._forced_charge(lim, level, "override", f"{why0}: charging to {lim.ceiling_soc:.0f}%")
        return _Pick(SELF_USE, "no_lines", "No rule fits: the battery stays on self-use", forced=True)

    def _forced_charge(self, lim: Limits, level: float, rule: str, why: str, forced_mode: str = CHARGE) -> _Pick:
        target = lim.ceiling_soc
        staying = self.mode == forced_mode
        if level < (target - LEVEL_EPS if staying else target - LEVEL_BAND_PCT):
            return _Pick(forced_mode, rule, why, target_soc=target, power_w=_w(lim.charge_cap_kw), forced=True)
        return _Pick(HOLD, rule, f"{why.split(':')[0]}: the battery has reached {target:.0f}%, so it holds",
                     target_soc=target, forced=True)

    # ---- the sentence and numbers of a mode -----------------------------------------------------
    def _pick_for(self, mode: str, lim: Limits, ln: Lines, target: float | None) -> _Pick:
        v, imp, exp = ln.value_p, ln.import_p, ln.export_p
        if mode == CHARGE:
            cap = f", limited to {lim.charge_cap_kw:.1f} kW" if lim.charge_cap_kw is not None else ""
            return _Pick(CHARGE, "value", f"Import is {imp:.2f}p and a stored kWh is worth {v:.1f}p here (buying pays "
                         f"while it is worth more than {ln.buy_line_p:.2f}p): charging to {target:.0f}%{cap}",
                         target_soc=target, power_w=_w(lim.charge_cap_kw))
        if mode == EXPORT:
            return _Pick(EXPORT, "value", f"Export pays {exp:.2f}p and a stored kWh is worth {v:.1f}p (selling pays "
                         f"while it is worth less than {ln.sell_line_p:.2f}p): selling down to {target:.0f}%",
                         target_soc=target, power_w=_w(lim.discharge_cap_kw))
        if mode == SELF_USE:
            if self.sun_spare:
                why = (f"Spare sun goes into the battery: a stored kWh is worth {v:.1f}p, more than the {exp:.2f}p "
                       f"exporting it pays (storing pays while it is worth more than {ln.store_sun_line_p:.2f}p)")
            elif v < ln.use_line_p:
                why = (f"The battery covers the house: a stored kWh is worth {v:.1f}p, less than the {imp:.2f}p "
                       f"import (the battery wins while it is worth less than {ln.use_line_p:.2f}p)")
            else:                                   # the plan's choice, not this line: say so rather than misstate it
                why = (f"The battery covers the house at {imp:.2f}p: a stored kWh is worth {v:.1f}p, more than the "
                       f"{ln.use_line_p:.2f}p it saves now, but "
                       + ("Hold isn't allowed at this time and the plan finds charging or selling doesn't pay here"
                          if HOLD not in lim.allowed else "the plan expects that to cost less overall than holding"))
            return _Pick(SELF_USE, "value", why)
        # hold
        if lim.rule == "car":
            return _Pick(HOLD, "car", f"The car is charging: the battery holds and the grid covers house and car at "
                                      f"{imp:.2f}p")
        if lim.rule == "reserve":
            return _Pick(HOLD, "reserve", f"{lim.reason}: the grid covers the house at {imp:.2f}p")
        if self.dec is not None and self.dec.get("reached"):
            word = "charge" if self.dec["mode"] == CHARGE else "sale"
            return _Pick(HOLD, "value", f"The {word} has reached where the plan stops it: the battery holds until "
                                        f"the next step (a stored kWh is worth {v:.1f}p, import {imp:.2f}p)")
        if self.sun_spare and SELF_USE in lim.allowed:
            why = (f"Spare sun is not worth storing: a stored kWh is worth {v:.1f}p, under the "
                   f"{ln.store_sun_line_p:.2f}p it needs, so the battery holds and the sun is exported at {exp:.2f}p")
        else:
            why = (f"A stored kWh is worth {v:.1f}p, more than the {ln.use_line_p:.2f}p the battery saves by "
                   f"covering the house at {imp:.2f}p: the grid runs the house")
        return _Pick(HOLD, "value", why)

    def _cause(self, events, cur, level, lim, ln) -> str:
        for e in events:
            if e.urgent:
                return e.kind
        if events:
            return events[0].kind
        if self._ended:                                    # the run reached its level
            return "level"
        return "price"

    def _note_change(self, ts: float, old: str | None, new: str) -> None:
        if old is not None:
            if self.changes and self.changes[-1][2] == old and self.changes[-1][1] == new \
                    and ts - self.changes[-1][0] < FLIP_FLOP_S:
                self.flip_flop_now = True
            self.changes.append([ts, old, new])
            self.changes = self.changes[-6:]

    # ---- deadline and exits ---------------------------------------------------------------------
    def _expected_end(self, vr: ValueResult | None, now: datetime, mode: str) -> tuple[datetime | None, str | None]:
        if vr is None:
            return None, None
        end, until, started = None, None, False
        for it in vr.timeline:
            if it.mode == mode and it.start <= now < it.end:
                end, until, started = it.end, it.until, True
            elif started and it.mode == mode and end is not None and it.start <= end:
                end, until = it.end, it.until
        return end, until

    def _finish(self, vr, now: datetime, level, facts) -> datetime | None:
        """When the charge or sale now running is expected to end: the end of the plan's step for it, or when the power
        in use gets the battery from its level to the target, whichever is later. The plan's step can end well before
        the target (it alternates charge and sale half-hour by half-hour while the live decision runs to the line), and
        a 'reaches 32% at 18:00' that the power cannot do by then is not an expectation."""
        end, _ = self._expected_end(vr, now, self.mode)
        if level is None or self.target_soc is None or self.mode not in (CHARGE, EXPORT):
            return end
        cap = facts.capacity_kwh
        gap = abs(self.target_soc - level) / 100 * cap
        kw = (self.power_w / 1000 if self.power_w else (facts.max_charge_kw if self.mode == CHARGE
                                                         else facts.max_discharge_kw))
        if kw <= 0:
            return end
        hours = gap / (kw * facts.eta_charge) if self.mode == CHARGE else gap * facts.eta_discharge / kw
        by_power = now + timedelta(hours=hours)
        return by_power if end is None or by_power > end else end

    def _deadline(self, now, ts, vr, facts, level, new_events) -> None:
        if self.mode not in (CHARGE, EXPORT):
            self.deadline = None
            return
        key = [self.mode, vr.made_at.isoformat() if vr is not None else None]
        if key != self.dl_key:
            self.dl_key = key
            end, _ = self._expected_end(vr, now, self.mode)
            if end is None and level is not None and self.target_soc is not None:
                gap = abs(self.target_soc - level) / 100 * facts.capacity_kwh
                kw = max(0.5, (facts.max_charge_kw if self.mode == CHARGE else facts.max_discharge_kw))
                end = now + timedelta(hours=1.5 * gap / kw)
            dl = (end + timedelta(minutes=DEADLINE_GRACE_MIN)).timestamp() if end is not None else None
            if dl is not None and dl > ts:
                self.deadline_fired = False
            self.deadline = dl
        if self.deadline is not None and ts >= self.deadline and not self.deadline_fired:
            self.deadline_fired = True
            word = "charge" if self.mode == CHARGE else "sale"
            new_events.append(Event(now, "deadline", f"The {word} has run past its expected end: the plan is looked "
                                                     "at again (it carries on while it still pays)"))

    def _exits(self, now: datetime, lim: Limits, ln: Lines | None, vr, level, facts=None) -> list[Exit]:
        mode, out = self.mode, []
        if mode is None:
            return out
        end, until = self._expected_end(vr, now, mode)
        if facts is not None and mode in (CHARGE, EXPORT):
            end = self._finish(vr, now, level, facts)
        if mode == HOLD and self._car_on():                # the car is what holds the battery, so its end is the exit
            out.append(Exit("car", "The car stops charging", None))
        if mode == CHARGE and self.target_soc is not None:
            out.append(Exit("level", f"The battery reaches {self.target_soc:.0f}%", end))
        elif mode == EXPORT and self.target_soc is not None:
            out.append(Exit("level", f"The battery reaches {self.target_soc:.0f}%", end))
        elif mode == EVENT and self.target_soc is not None:
            out.append(Exit("level", f"The battery reaches {self.target_soc:.0f}%", None))
        if mode in (CHARGE, EXPORT, SELF_USE, HOLD) and vr is not None:
            for seg in vr.forecast.segments:
                if seg.start <= now:
                    continue
                if seg.event and lim.forced != EVENT:
                    out.append(Exit("event", f"A grid event starts at {self._hm(seg.start)}", seg.start))
                    break
            cur_seg = next((g for g in vr.forecast.segments if g.start <= now < g.end), None)
            if cur_seg is not None:
                for seg in vr.forecast.segments:
                    if seg.start >= cur_seg.end and (abs(seg.import_p - cur_seg.import_p) > 1e-6
                                                     or abs(seg.export_p - cur_seg.export_p) > 1e-6):
                        out.append(Exit("price", f"The price changes to {seg.import_p:.2f}p at "
                                                 f"{self._hm(seg.start)}", seg.start))
                        break
        if mode == CHARGE and ln is not None:
            out.append(Exit("price", f"A stored kWh stops being worth more than {ln.buy_line_p:.2f}p", None))
        if mode == EXPORT and ln is not None:
            out.append(Exit("price", f"A stored kWh stops being worth less than {ln.sell_line_p:.2f}p", None))
        if mode in (CHARGE, EXPORT, SELF_USE) and not self._car_on():
            out.append(Exit("car", "The car starts charging", None))
        if self.deadline is not None and mode in (CHARGE, EXPORT):
            dl = datetime.fromtimestamp(self.deadline, tz=timezone.utc)
            out.append(Exit("deadline", f"Not finished by {self._hm(dl)}: the plan is looked at again", dl))
        return out

    def _hm(self, t: datetime) -> str:
        return (t.astimezone(self._tz) if self._tz else t).strftime("%H:%M")

    def set_tz(self, tz) -> None:
        self._tz = tz

    def _car_on(self) -> bool:
        return self.rule == "v2_car"

    @staticmethod
    def _exit_dict(e: Exit) -> dict:
        return {"kind": e.kind, "text": e.text, "at": e.expected_at.timestamp() if e.expected_at else None}

    # ---- the outputs ----------------------------------------------------------------------------
    def _decision(self, pick: _Pick, level, ln) -> v1d.Decision:
        mode = pick.mode
        details = {"engine": "v2", "mode": mode}
        if ln is not None:
            details["value_p"] = round(ln.value_p, 2)
        return v1d.Decision(MODE_ACTION[mode], "v2_" + pick.rule, sentence_case_off(pick.why),
                            target_soc=pick.target_soc if mode in (CHARGE, FREE, HOLD, EXPORT, EVENT) else None,
                            power_w=pick.power_w, details=details,
                            label_target_soc=pick.target_soc if mode == CHARGE else None)

    def mode_state(self) -> ModeState:
        exits = tuple(Exit(d["kind"], d["text"], datetime.fromtimestamp(d["at"], tz=timezone.utc) if d["at"] else None)
                      for d in self.exits)
        since = datetime.fromtimestamp(self.since or 0.0, tz=timezone.utc)
        deadline = datetime.fromtimestamp(self.deadline, tz=timezone.utc) if self.deadline else None
        return ModeState(mode=self.mode or SELF_USE, since=since, why=self.why, rule=self.rule,
                         chosen_by=self.chosen_by, target_soc=self.target_soc, power_w=self.power_w, exits=exits,
                         deadline=deadline, lines=self.lines)

    def state(self) -> dict:
        return {"mode": self.mode, "since": self.since, "leg_from": self.leg_from, "why": self.why, "rule": self.rule,
                "chosen_by": self.chosen_by, "target_soc": self.target_soc, "power_w": self.power_w,
                "deadline": self.deadline, "deadline_fired": self.deadline_fired, "dl_key": self.dl_key,
                "sun_spare": self.sun_spare, "reserve_latched": self.reserve_latched,
                "event_floor_hit": self.event_floor_hit, "prev_rule": self.prev_rule, "changes": list(self.changes),
                "exits": json.loads(json.dumps(self.exits)), "lines": asdict(self.lines) if self.lines else None}
