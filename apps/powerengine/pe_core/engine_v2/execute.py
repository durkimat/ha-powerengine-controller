"""Layer 5: execute. One mode at a time, kept until a condition ends it; never decided on a timer.

The mode is chosen by comparing the live prices with the value of a stored kWh at the real level (`Lines`), inside
what layer 4 allows or forces (docs/plans/engine-v2.md, section 8):

  charge     while a stored kWh is worth more than the import price after losses, up to the charge target
  export     while a stored kWh is worth less than the export price after losses, down to the sell floor
  self-use   the battery covers the house (or stores spare sun) while that beats the grid / the export price
  hold       otherwise: the grid runs the house

Hysteresis: the price band decides how much better another mode must be to start it and how much worse the current one
may get before it stops (the same band, either side of the line); the level band stops a charge restarting at its
target. Minimum time in a mode applies to opportunity changes only: a mode that must end (it is no longer allowed, its
level target is reached, its price is no longer worth it) ends at once, and forced modes and urgent events are never
held back. A deadline (the expected end plus a grace) raises an event so the plan is looked at again; it never stops a
mode. Missing data gives Self-use, the inverter's own safe state.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from .. import decide as v1d
from ..names import N, current
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
from .value import KIND, choice_now, switch_cost

FLIP_FLOP_S = 600
LEVEL_EPS = 0.1               # points: a mode that runs "until" a level is done within this of it. The filtered level
                              # only approaches a reading (99.999...% at 100%), so a bare `level < target` never ends it


@dataclass
class _Pick:
    mode: str
    rule: str
    why: str
    target_soc: float | None = None
    power_w: float | None = None
    forced: bool = False           # a forced or safety pick: no minimum time
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


class Executor:
    def __init__(self, settings: V2Settings, state: dict | None = None):
        self.s = settings
        st = dict(state or {})
        self.mode: str | None = st.get("mode")
        self.since: float | None = st.get("since")
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
        lines = st.get("lines")
        self.lines: Lines | None = Lines(**lines) if lines else None
        self.flip_flop_now = False                                   # the last step was an A to B to A change
        self._tz = None
        self._plan_target: float | None = None
        self._plan_sell: float | None = None
        self._urgent = False                                         # an urgent event is being handled this step
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
        urgent = self._urgent = any(e.urgent for e in events)
        self._plan_target = self._plan_leg_end(vr, now, CHARGE)
        self._plan_sell = self._plan_leg_end(vr, now, EXPORT)

        pick = self._pick_now(now, obs, lim, ln, vr, level, facts)
        cur = self.mode
        if cur is not None and pick.mode in (CHARGE, EXPORT) and pick.mode != cur and not pick.forced and not urgent:
            pick = self._with_the_programmes_choice(pick, now, lim, ln, vr, level, facts)
        if cur is not None and pick.mode != cur and not pick.forced and not urgent \
                and self.since is not None and ts - self.since < self.s.min_dwell_s \
                and not self._must_exit(cur, lim, ln, level):
            pick = self._pick_for(cur, lim, ln, level, facts, vr)        # minimum time: stay a little longer
        elif cur is not None and pick.mode != cur and not pick.forced and not urgent \
                and not self._must_exit(cur, lim, ln, level) and not self._worth_the_change(cur, pick, lim, ln, level,
                                                                                            facts, obs):
            pick = self._pick_for(cur, lim, ln, level, facts, vr)        # a change must pay for itself

        changed = cur != pick.mode
        if changed:
            chosen_by = pick.chosen_by or self._cause(events, cur, level, lim, ln)
            self._note_change(ts, cur, pick.mode)
            prior = self.target_soc
            if cur == CHARGE and level is not None and prior is not None and level >= prior - LEVEL_EPS:
                new_events.append(Event(now, "level", f"The battery reached {level:.0f}%: the charge ends"))
            elif cur == EXPORT and level is not None \
                    and level <= (lim.floor_soc if prior is None else prior) + LEVEL_EPS:
                new_events.append(Event(now, "level", f"The battery reached {level:.0f}%: the sale ends"))
            self.mode, self.since, self.chosen_by = pick.mode, ts, chosen_by
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
        self.exits = [self._exit_dict(e) for e in self._exits(now, lim, ln, vr, level)]

        decision = self._decision(pick, level, ln)
        self.last_decision = decision
        return self.mode_state(), decision, tuple(new_events), changed

    # ---- which mode now -------------------------------------------------------------------------
    def _pick_now(self, now, obs, lim, ln, vr, level, facts) -> _Pick:
        if not obs.data_ok or level is None:
            miss = ", ".join(obs.missing) or "the battery level"
            return _Pick(SELF_USE, "data_missing", f"Readings are missing ({miss}): the battery is handed back to the "
                                                   "inverter's own self-use until they return", forced=True,
                         chosen_by="data_missing")
        if lim.forced is not None:
            return self._forced(lim, ln, level, facts)
        self.event_floor_hit = False
        if vr is None:
            return _Pick(SELF_USE, "starting", "The first values are still being worked out: the battery stays on "
                                               "self-use until they are ready", forced=True, chosen_by="start")
        if ln is None:
            return _Pick(SELF_USE, "no_lines", "The live comparison could not be made: the battery stays on self-use",
                         forced=True)
        return self._pick_for(self._candidate(lim, ln, level, vr, now), lim, ln, level, facts, vr)

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
            hold_at = lim.floor_soc + (s.level_band_pct if self.reserve_latched else 0.0)
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
        if level < (target - LEVEL_EPS if staying else target - self.s.level_band_pct):
            return _Pick(forced_mode, rule, why, target_soc=target, power_w=_w(lim.charge_cap_kw), forced=True)
        return _Pick(HOLD, rule, f"{why.split(':')[0]}: the battery has reached {target:.0f}%, so it holds",
                     target_soc=target, forced=True)

    # the mode the prices and levels ask for, with hysteresis from the mode now
    def _candidate(self, lim: Limits, ln: Lines, level: float, vr: ValueResult | None = None,
                   now: datetime | None = None) -> str:
        cur, band = self.mode, self.s.price_band_p
        can_c = self._can_charge(lim, ln, level, staying=cur == CHARGE)
        can_e = self._can_export(lim, ln, level, staying=cur == EXPORT)
        if can_c and can_e:
            # Both pay (the value is between the buy and sell lines: a cycle). Charging first would stop every sale one
            # level band under the top and the two would alternate. The plan has priced the whole cycle, so it says
            # which comes now; with no plan item, the mode already running goes on, else the sale. A leg that is
            # running goes on to the end of its plan step (a revaluation can't turn it round part-way: the lines
            # compare one step at a time, and what is left of the cycle is priced in the plan); the exits and an
            # urgent event still end it.
            if cur in (CHARGE, EXPORT) and not self._urgent and self._leg_going(cur, level, now):
                return cur
            planned = self._planned_mode(vr, now)
            if planned in (CHARGE, EXPORT):
                return planned
            return cur if cur in (CHARGE, EXPORT) else EXPORT
        if can_c:
            return CHARGE
        if can_e:
            return EXPORT
        if SELF_USE not in lim.allowed:
            return HOLD if HOLD in lim.allowed else sorted(lim.allowed)[0]
        staying = cur == SELF_USE
        if self.sun_spare:
            ok = ln.value_p > ln.store_sun_line_p + (-band if staying else band)
        else:
            ok = ln.value_p < ln.use_line_p + (band if staying else -band)
        if ok:
            return SELF_USE
        return HOLD if HOLD in lim.allowed else SELF_USE

    def _leg_going(self, mode: str, level: float, now: datetime | None) -> bool:
        """The charge or sale running now has not reached the end of its plan step (the step covering now, merged with
        the following steps of the same mode; the level where that merged step ends). With no such step the leg goes on
        until the lines end it (`_must_exit`)."""
        end = self._plan_target if mode == CHARGE else self._plan_sell
        if level is None:
            return False
        if end is None:                      # no step of this mode covers now: nothing in the plan ends the leg early
            return True
        return level < end - LEVEL_EPS if mode == CHARGE else level > end + LEVEL_EPS

    @staticmethod
    def _planned_mode(vr: ValueResult | None, now: datetime | None) -> str | None:
        """The mode of the expected timeline's item that covers `now`."""
        if vr is None or now is None:
            return None
        for it in vr.timeline:
            if it.start <= now < it.end:
                return it.mode
        return None

    def _with_the_programmes_choice(self, pick: _Pick, now: datetime, lim: Limits, ln: Lines | None, vr, level,
                                    facts: BatteryFacts) -> _Pick:
        """A charge or a sale starts only when the value programme itself would start it here, from the mode now. The
        lines compare one step at a time, and a sale that looks good now can be one the plan has priced as a worse
        way to run the cycle (it sells a point, has to buy it back, and the two alternate every two minutes)."""
        if vr is None or ln is None or level is None:
            return pick
        try:
            choice = choice_now(vr, now, level, ln.import_p, facts, self.s, self.mode)
        except Exception:
            return pick
        if choice is None or choice[0] == pick.mode or choice[0] not in lim.allowed:
            return pick
        return self._pick_for(choice[0], lim, ln, level, facts, vr)

    def _worth_the_change(self, cur: str, pick: _Pick, lim: Limits, ln: Lines | None, level, facts,
                          obs: Observation) -> bool:
        """A change the plan did not ask for must earn more than it costs: how far the value is past the line, times
        the energy the mode would move. A change into a mode is followed by a change out of it, so it has to cover
        both. (`charge_now` is the plan's own run, and changes the rules or the levels force, safety events and forced
        modes never come here.) A charge or a sale moves the energy up to its own exit level; self-use and hold move
        what the house would draw or the sun supply in the next half-hour."""
        cost = 2 * switch_cost(KIND[cur], KIND[pick.mode], self.s.switch_cost_p, self.s.reversal_cost_p)
        if cost <= 0 or ln is None or level is None:
            return True
        cap = facts.capacity_kwh
        if pick.mode == CHARGE and not ln.charge_now:
            move = max(0.0, (pick.target_soc if pick.target_soc is not None else lim.ceiling_soc) - level)
            return (ln.value_p - ln.buy_line_p) * move / 100 * cap >= cost
        if pick.mode == EXPORT:
            move = max(0.0, level - (pick.target_soc if pick.target_soc is not None else lim.floor_soc))
            return (ln.sell_line_p - ln.value_p) * move / 100 * cap >= cost
        if {pick.mode, cur} == {SELF_USE, HOLD} and obs.net_load_kw is not None:
            kwh = abs(obs.net_load_kw) * 0.5
            line = ln.store_sun_line_p if self.sun_spare else ln.use_line_p
            margin = (ln.value_p - line) if self.sun_spare else (line - ln.value_p)    # in favour of self-use
            return (margin if pick.mode == SELF_USE else -margin) * kwh >= cost
        return True

    @staticmethod
    def _plan_leg_end(vr: ValueResult | None, now: datetime, mode: str) -> float | None:
        """Where the expected timeline says the charge (or sale) covering now ends: the level at the end of that item,
        with the consecutive items of the same mode merged. None when no item of that mode covers now."""
        if vr is None:
            return None
        level = None
        end = None
        for it in vr.timeline:
            if it.mode == mode and it.start <= now < it.end and level is None:
                level, end = it.level_end, it.end
            elif level is not None and it.mode == mode and it.start <= end:
                level, end = it.level_end, it.end
        return level

    def _charge_target(self, lim: Limits, ln: Lines, staying: bool = False) -> float | None:
        """Where a charge stops: where buying stops being worth it now (the lines), and while a charge is running also
        where the expected timeline ends it. On a flat stretch of the value curve (the same cheap price later) the
        lines' target creeps up with each segment, so alone it would stop and restart the charge every half-hour."""
        target = ln.charge_target_soc
        if staying and self._plan_target is not None:
            target = self._plan_target if target is None else max(target, self._plan_target)
        return None if target is None else min(target, lim.ceiling_soc)

    def _can_charge(self, lim, ln, level, staying: bool) -> bool:
        if CHARGE not in lim.allowed:
            return False
        target = self._charge_target(lim, ln, staying)
        if target is None:
            return False
        band = self.s.price_band_p
        # charge_now: the plan charges in this stretch at one price, so the charge starts now rather than at its end
        # (the same cost, and room left if the charge runs slower than modelled); the value test covers the rest
        if not (ln.charge_now or ln.value_p > ln.buy_line_p + (-band if staying else band)):
            return False
        return level < (target - LEVEL_EPS if staying else target - self.s.level_band_pct)

    def _sell_floor(self, lim: Limits, ln: Lines, staying: bool = False) -> float:
        """Where a sale stops: where selling stops being worth it now (the lines), and while a sale is running also
        where the expected timeline ends it (the lower of the two, as a charge takes the higher), never under the
        floor."""
        floor = ln.sell_floor_soc if ln.sell_floor_soc is not None else lim.floor_soc
        if staying and self._plan_sell is not None:
            floor = min(floor, self._plan_sell)
        return max(floor, lim.floor_soc)

    def _can_export(self, lim, ln, level, staying: bool) -> bool:
        if EXPORT not in lim.allowed:
            return False
        band = self.s.price_band_p
        if not ln.value_p < ln.sell_line_p + (band if staying else -band):
            return False
        floor = self._sell_floor(lim, ln, staying)
        return level > (floor + LEVEL_EPS if staying else floor + self.s.level_band_pct)

    def _must_exit(self, cur: str, lim: Limits, ln: Lines | None, level) -> bool:
        """The mode now can't go on whatever the minimum time says."""
        if cur in (EVENT, FREE) or cur not in lim.allowed:
            return True
        if ln is None or level is None:
            return False
        if cur == CHARGE:
            return not self._can_charge(lim, ln, level, staying=True)
        if cur == EXPORT:
            return not self._can_export(lim, ln, level, staying=True)
        return False

    # ---- the sentence and numbers of a mode -----------------------------------------------------
    def _pick_for(self, mode: str, lim: Limits, ln: Lines, level: float, facts: BatteryFacts, vr) -> _Pick:
        v, imp, exp = ln.value_p, ln.import_p, ln.export_p
        if mode == CHARGE:
            target = self._charge_target(lim, ln, self.mode == CHARGE)
            if target is None:                            # the mode is kept by minimum time only
                target = lim.ceiling_soc
            cap = f", limited to {lim.charge_cap_kw:.1f} kW" if lim.charge_cap_kw is not None else ""
            return _Pick(CHARGE, "value", f"Import is {imp:.2f}p and a stored kWh is worth {v:.1f}p here (buying pays "
                         f"while it is worth more than {ln.buy_line_p:.2f}p): charging to {target:.0f}%{cap}",
                         target_soc=target, power_w=_w(lim.charge_cap_kw))
        if mode == EXPORT:
            floor = self._sell_floor(lim, ln, self.mode == EXPORT)
            return _Pick(EXPORT, "value", f"Export pays {exp:.2f}p and a stored kWh is worth {v:.1f}p (selling pays "
                         f"while it is worth less than {ln.sell_line_p:.2f}p): selling down to {floor:.0f}%",
                         target_soc=floor, power_w=_w(lim.discharge_cap_kw))
        if mode == SELF_USE:
            if self.sun_spare:
                why = (f"Spare sun goes into the battery: a stored kWh is worth {v:.1f}p, more than the {exp:.2f}p "
                       f"exporting it pays (storing pays while it is worth more than {ln.store_sun_line_p:.2f}p)")
            else:
                why = (f"The battery covers the house: a stored kWh is worth {v:.1f}p, less than the {imp:.2f}p "
                       f"import (the battery wins while it is worth less than {ln.use_line_p:.2f}p)")
            return _Pick(SELF_USE, "value", why)
        # hold
        if lim.rule == "car":
            return _Pick(HOLD, "car", f"The car is charging: the battery holds and the grid covers house and car at "
                                      f"{imp:.2f}p")
        if lim.rule == "reserve":
            return _Pick(HOLD, "reserve", f"{lim.reason}: the grid covers the house at {imp:.2f}p")
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
        if cur == CHARGE and ln is not None and level is not None and ln.charge_target_soc is not None \
                and level >= min(ln.charge_target_soc, lim.ceiling_soc) - 1e-9:
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
            dl = (end + timedelta(minutes=self.s.deadline_grace_min)).timestamp() if end is not None else None
            if dl is not None and dl > ts:
                self.deadline_fired = False
            self.deadline = dl
        if self.deadline is not None and ts >= self.deadline and not self.deadline_fired:
            self.deadline_fired = True
            word = "charge" if self.mode == CHARGE else "sale"
            new_events.append(Event(now, "deadline", f"The {word} has run past its expected end: the plan is looked "
                                                     "at again (it carries on while it still pays)"))

    def _exits(self, now: datetime, lim: Limits, ln: Lines | None, vr, level) -> list[Exit]:
        mode, out = self.mode, []
        if mode is None:
            return out
        end, until = self._expected_end(vr, now, mode)
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
        return {"mode": self.mode, "since": self.since, "why": self.why, "rule": self.rule,
                "chosen_by": self.chosen_by, "target_soc": self.target_soc, "power_w": self.power_w,
                "deadline": self.deadline, "deadline_fired": self.deadline_fired, "dl_key": self.dl_key,
                "sun_spare": self.sun_spare, "reserve_latched": self.reserve_latched,
                "event_floor_hit": self.event_floor_hit, "prev_rule": self.prev_rule, "changes": list(self.changes),
                "exits": json.loads(json.dumps(self.exits)), "lines": asdict(self.lines) if self.lines else None}
