"""Engine v2, layer 3: the value of stored energy (docs/plans/engine-v2.md section 6; build plan, package A).

Stochastic dynamic programming over the battery level, backwards over the forecast's segments: for every segment and
every level, what one more stored kWh is worth (`lam`, pence per kWh). From that curve come the live comparison
(`lines`), the expected timeline and the level path.

Model, in short (all prices in pence per kWh, energy in kWh stored in the battery):

* `V_k(e)` is the expected cost from segment k on, starting at level e. `V_end(e) = -terminal_value * e`.
* A smart slot that is not certain is one price, the expected one: `slot_prob` x the slot price + (1 - `slot_prob`) x
  the normal price. The certainty sits in the price, so the plan is cautious by itself and cannot count on the slot to
  refill what it sells. A certain slot (`slot_prob` 1) is the slot price. A forced mode (grid event, free power, an
  override) is the only choice.
* The sun and the house are not known in advance: the mode is chosen against the expected cost over three net-load
  scenarios (low, middle, high), each the probability-weighted mean of a third of the nine sun x house combinations.
* Modes: Self-use, Hold, Charge, Export (Event and Free when forced). Self-use and Hold run the whole segment.
  **Charge and Export may stop at any level inside the segment** and hold for the rest: the stop is searched on the
  convex expected cost with a bisection on its slope, then snapped to the grid levels either side.
* Ties are not bought: a charge is only chosen when it is strictly cheaper than holding, so the value curve is flat at
  the buy line wherever charging now or later would cost the same, and `lines.charge_target_soc` is where the value
  stops being above the buy line.

Reductions that keep a 48 h look-ahead under 3 s on this machine (measured 0.5 to 0.8 s):

1. levels: `level_step_kwh` (0.1) up to `FINE_HOURS` ahead and 0.5 kWh beyond (values are interpolated between
   levels; `lam` is published on the fine grid for every segment). With the 200-level grid the whole 48 h solves in
   under a second, so `FINE_HOURS` is 48 (no coarse part); the coarse grid is kept for the comfort-free comparison
   solve and as a lever if a slower machine needs it (set `FINE_HOURS` to 12);
2. three net-load scenarios instead of nine, identical ones merged;
3. a smart slot doubles only its own segments; a forced segment takes one expected price;
4. the partial charge or sale is a bisection on the slope (7 steps), not a scan of every end level.
"""

from __future__ import annotations

import math
import time
from bisect import bisect_right
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from ..names import N
from .settings import V2Settings
from .types import (
    CHARGE,
    EVENT,
    EXPORT,
    FREE,
    HOLD,
    SELF_USE,
    BatteryFacts,
    Forecast,
    Limits,
    Lines,
    Segment,
    Spread,
    TimelineItem,
    ValueResult,
)

EPS = 1e-6                    # pence: a mode must beat the one before it by more than this to be chosen
SLOPE_TOL = 1e-3              # pence: a partial charge stops where its marginal gain falls to this
LINE_TOL = 0.01               # p/kWh: the value must be this far above the buy line to count as "worth buying"
COARSE_STEP_KWH = 0.5
FINE_HOURS = 48.0
PATH_STEP_MIN = 15
BISECT_STEPS = 7
_ORDER = (SELF_USE, HOLD, CHARGE, EXPORT)

# A smooth cost on where the battery sits (docs/plans/engine-v2-level-penalty.md): a rate in pence per kWh per hour
# for the energy in each percent-layer beyond a start level, doubling every `..._DOUBLING_PTS` points. The top curve
# starts at LEVEL_TOP_START_SOC and reaches LEVEL_TOP_RATE_AT_FULL_P at 100%; the bottom one starts at
# LEVEL_BOT_START_SOC and reaches LEVEL_BOT_RATE_AT_FLOOR_P at LEVEL_BOT_ANCHOR_SOC. A rate of 0 switches a curve off
# (both off: the plan is exactly as without them). It is a cost of the level itself, so it enters the value curve and
# the live lines with no rule.
LEVEL_TOP_START_SOC = 90.0
LEVEL_TOP_RATE_AT_FULL_P = 0.0
LEVEL_TOP_DOUBLING_PTS = 2.0
LEVEL_BOT_START_SOC = 70.0
LEVEL_BOT_RATE_AT_FLOOR_P = 0.0
LEVEL_BOT_ANCHOR_SOC = 15.0
LEVEL_BOT_DOUBLING_PTS = 5.0
PEN_RES = 100                 # table entries per kWh


# --- the level penalty ---------------------------------------------------------------------------------------------
class _Pen:
    """Tables over the battery level (kWh, `res` entries per kWh): `phi[i]` the cost in pence per hour of holding that
    level, `dphi[i]` its slope in pence per kWh per hour (the rate of the layer at that level: positive above the top
    start, negative below the bottom start), and the top curve's start."""
    __slots__ = ("phi", "dphi", "res", "top_start_kwh")


_pen_cache: dict = {}


def _layer(x: float, a: float, d: float) -> tuple[float, float]:
    """(rate, cumulative rate) of a curve `x` points past its start: rate a*(2^(x/d)-1), and its integral over them."""
    k = math.log(2.0) / d
    grow = 2.0 ** (x / d) - 1.0
    return a * grow, a * (grow / k - x)


def _level_penalty(cap: float) -> _Pen | None:
    top_on = LEVEL_TOP_RATE_AT_FULL_P > 0.0 and LEVEL_TOP_START_SOC < 100.0
    bot_on = LEVEL_BOT_RATE_AT_FLOOR_P > 0.0 and LEVEL_BOT_START_SOC > LEVEL_BOT_ANCHOR_SOC
    if not (top_on or bot_on):
        return None
    key = (cap, LEVEL_TOP_START_SOC, LEVEL_TOP_RATE_AT_FULL_P, LEVEL_TOP_DOUBLING_PTS, LEVEL_BOT_START_SOC,
           LEVEL_BOT_RATE_AT_FLOOR_P, LEVEL_BOT_ANCHOR_SOC, LEVEL_BOT_DOUBLING_PTS)
    hit = _pen_cache.get(key)
    if hit is not None:
        return hit
    span_top = (100.0 - LEVEL_TOP_START_SOC) / LEVEL_TOP_DOUBLING_PTS
    span_bot = (LEVEL_BOT_START_SOC - LEVEL_BOT_ANCHOR_SOC) / LEVEL_BOT_DOUBLING_PTS
    a_top = LEVEL_TOP_RATE_AT_FULL_P / (2.0**span_top - 1.0) if top_on else 0.0
    a_bot = LEVEL_BOT_RATE_AT_FLOOR_P / (2.0**span_bot - 1.0) if bot_on else 0.0
    n = int(round(cap * PEN_RES))
    phi, rate = [], []
    per_point = cap / 100.0
    for i in range(n + 2):
        pct = i / PEN_RES / cap * 100.0
        r = c = 0.0
        if top_on and pct > LEVEL_TOP_START_SOC:
            r1, c1 = _layer(pct - LEVEL_TOP_START_SOC, a_top, LEVEL_TOP_DOUBLING_PTS)
            r, c = r + r1, c + c1
        if bot_on and pct < LEVEL_BOT_START_SOC:
            r1, c1 = _layer(LEVEL_BOT_START_SOC - pct, a_bot, LEVEL_BOT_DOUBLING_PTS)
            r, c = r - r1, c + c1                              # more energy lowers the bottom penalty: a negative slope
        rate.append(r)
        phi.append(c * per_point)
    pen = _Pen()
    pen.phi, pen.dphi, pen.res = phi, rate, PEN_RES
    pen.top_start_kwh = LEVEL_TOP_START_SOC / 100.0 * cap if top_on else cap
    if len(_pen_cache) > 8:
        _pen_cache.clear()
    _pen_cache[key] = pen
    return pen


def _phi(P: _Pen, e: float) -> float:
    i = int(e * P.res + 0.5)
    t = P.phi
    return t[i] if i < len(t) else t[-1]


def _dphi(P: _Pen, e: float) -> float:
    i = int(e * P.res + 0.5)
    t = P.dphi
    return t[i] if i < len(t) else t[-1]


def _pen_partial(P: _Pen, dt: float, e0: float, ef: float, f: float) -> float:
    """Pence of the level penalty for a charge or sale that moves the level from e0 to ef over the share f of the
    segment and then holds at ef for the rest (Simpson over the move: the cost is convex, trapezoid overstates)."""
    pm = _phi(P, 0.5 * (e0 + ef))
    return dt * (f * (_phi(P, e0) + 4.0 * pm + _phi(P, ef)) / 6.0 + (1.0 - f) * _phi(P, ef))


def _dpen_partial(P: _Pen, dt: float, e0: float, d_e: float, f: float) -> float:
    """The slope of `_pen_partial` in f, when the level reached at f is ef = e0 + f * d_e."""
    ef = e0 + f * d_e
    em = 0.5 * (e0 + ef)
    simpson = (_phi(P, e0) + 4.0 * _phi(P, em) + _phi(P, ef)) / 6.0
    ramp = f * (2.0 * _dphi(P, em) + _dphi(P, ef)) * d_e / 6.0
    return dt * (simpson + ramp - _phi(P, ef) + (1.0 - f) * _dphi(P, ef) * d_e)


# --- level grids ---------------------------------------------------------------------------------------------------
class _Arr:
    """Values on a level grid 0 .. capacity."""
    __slots__ = ("v", "step", "inv", "n")

    def __init__(self, v: list[float], step: float):
        self.v, self.step, self.inv, self.n = v, step, 1.0 / step, len(v) - 1


def _grid(cap: float, step: float) -> tuple[int, float]:
    """Number of level steps and the step (kWh) of a grid 0 .. capacity. The count is a multiple of 100, so every whole
    percent (the floors, the ceiling, the comfort band's edges) falls exactly on a level: a kink between two levels
    would be rounded at every segment and show as wiggles in the value curve. Close to `step` (0.1 kWh on 18 kWh
    gives 200 levels of 0.09 kWh)."""
    n = 100 * max(1, int(round(cap / max(step, 1e-6) / 100)))
    return n, cap / n


def _val(a: _Arr, e: float) -> float:
    x = e * a.inv
    if x <= 0.0:
        return a.v[0]
    if x >= a.n:
        return a.v[a.n]
    j = int(x)
    v = a.v
    return v[j] + (x - j) * (v[j + 1] - v[j])


def _slope(a: _Arr, e: float) -> float:
    j = int(e * a.inv)
    if j < 0:
        j = 0
    elif j >= a.n:
        j = a.n - 1
    return (a.v[j + 1] - a.v[j]) * a.inv


# --- a segment, ready for the arithmetic ---------------------------------------------------------------------------
class _Seg:
    __slots__ = ("seg", "lim", "dt", "cap", "eta_c", "eta_d", "taper", "dtaper", "max_chg", "max_dis", "chg_f",
                 "chg_cap", "dis_cap", "export_limit", "fuse", "floor", "ceil", "car", "export_p", "event_p",
                 "wear_h", "wear_s", "ccost", "topup", "sw", "lo", "hi", "allowed", "forced", "outcomes", "scen",
                 "mid", "late_p", "pen", "fcap")


def _groups(solar: Spread, load: Spread) -> list[tuple[float, float, float]]:
    """Three net-load scenarios (probability, solar kWh, house kWh), lowest net first. The nine sun x house
    combinations are sorted by net load and cut by mass into a low, a middle and a high group (masses from the weights:
    low net = sun high or house low), each group represented by its probability-weighted mean."""
    if solar.low == solar.mid == solar.high and load.low == load.mid == load.high:
        return [(1.0, solar.mid, load.mid)]
    combos = []
    for lv, lw in zip((load.low, load.mid, load.high), load.w, strict=True):
        for sv, sw in zip((solar.low, solar.mid, solar.high), solar.w, strict=True):
            if lw * sw > 0:
                combos.append((lv - sv, lw * sw, sv, lv))
    combos.sort()
    p_lo = (solar.w[2] + load.w[0]) / 2
    p_hi = (solar.w[0] + load.w[2]) / 2
    masses = [p_lo, max(0.0, 1.0 - p_lo - p_hi), p_hi]
    out, i, left = [], 0, combos[0][1] if combos else 0.0
    for mass in masses:
        need, ps, pl, got = mass, 0.0, 0.0, 0.0
        while need > 1e-12 and i < len(combos):
            take = min(need, left)
            ps += take * combos[i][2]
            pl += take * combos[i][3]
            got += take
            need -= take
            left -= take
            if left <= 1e-12:
                i += 1
                left = combos[i][1] if i < len(combos) else 0.0
        if got > 1e-12:
            out.append((got, ps / got, pl / got))
    merged: list[list[float]] = []
    for p, s, h in out:
        if merged and abs((h - s) - (merged[-1][2] - merged[-1][1])) < 1e-9:
            m = merged[-1]
            t = m[0] + p
            m[1], m[2], m[0] = (m[1] * m[0] + s * p) / t, (m[2] * m[0] + h * p) / t, t
        else:
            merged.append([p, s, h])
    tot = sum(m[0] for m in merged) or 1.0
    return [(m[0] / tot, m[1], m[2]) for m in merged]


def _make(seg: Segment, facts: BatteryFacts, settings: V2Settings, lim: Limits) -> _Seg:
    S = _Seg()
    S.seg, S.lim = seg, lim
    S.late_p = 0.0
    S.dt = seg.hours
    S.cap = facts.capacity_kwh
    S.eta_c, S.eta_d = facts.eta_charge, facts.eta_discharge
    S.taper, S.dtaper = facts.taper, facts.dtaper
    S.max_chg, S.max_dis = facts.max_charge_kw, facts.max_discharge_kw
    S.chg_f = seg.charge_factor
    caps = [c for c in (lim.charge_cap_kw, facts.bms_charge_kw) if c is not None]
    S.chg_cap = min(caps) if caps else None
    caps = [c for c in (lim.discharge_cap_kw, facts.bms_discharge_kw) if c is not None]
    S.dis_cap = min(caps) if caps else None
    S.export_limit, S.fuse = facts.export_limit_kw, facts.fuse_kw
    S.floor = lim.floor_soc / 100 * S.cap
    S.ceil = lim.ceiling_soc / 100 * S.cap
    S.car = seg.car_kw * S.dt
    S.export_p, S.event_p = seg.export_p, seg.event_p
    S.wear_h, S.wear_s = settings.wear_house_p, settings.wear_sale_p
    S.ccost = settings.comfort_cost_p
    S.topup = settings.top_up_cost_p
    S.sw = settings.switch_cost_p
    S.lo, S.hi = settings.comfort_low_soc / 100 * S.cap, settings.comfort_high_soc / 100 * S.cap
    S.pen = _level_penalty(S.cap)
    S.fcap = S.hi if (S.ccost or S.topup) else S.cap          # where charging early stops being free (see `_walk`)
    if S.pen is not None:
        S.fcap = min(S.fcap, S.pen.top_start_kwh)
    S.forced = lim.forced or (EVENT if seg.event else FREE if seg.free else seg.manual)
    S.allowed = tuple(m for m in _ORDER if m in lim.allowed) or (SELF_USE,)
    scen = []
    for p, so, ho in _groups(seg.solar_kwh, seg.load_kwh):
        scen.append((p, so, ho, ho + S.car - so))
    S.scen = scen
    S.mid = (seg.solar_kwh.mid, seg.load_kwh.mid)
    if seg.slot_prob is not None and seg.slot_import_p is not None and seg.slot_prob >= 1:
        S.outcomes = [(1.0, seg.slot_import_p)]
    elif seg.slot_prob is not None and seg.slot_import_p is not None and seg.slot_prob > 0:
        # an uncertain smart slot is one price, the expected one: the certainty sits in the price, so the plan is
        # cautious by itself (it cannot count on the slot to refill what it sells)
        S.outcomes = [(1.0, seg.slot_prob * seg.slot_import_p + (1 - seg.slot_prob) * seg.import_p)]
    else:
        S.outcomes = [(1.0, seg.import_p)]
    return S


def _chg_kw(S: _Seg, e: float) -> float:
    kw = S.max_chg * S.chg_f
    soc = e / S.cap * 100
    for soc_from, frac in S.taper:
        if soc >= soc_from:
            kw = min(kw, S.max_chg * frac)
    return kw if S.chg_cap is None else min(kw, S.chg_cap)


def _dis_kw(S: _Seg, e: float) -> float:
    kw = S.max_dis
    if S.dtaper:
        mid = e / S.cap * 100 - 50.0 * kw * S.dt / S.cap
        for below, frac in S.dtaper:
            if mid < below:
                kw = min(kw, S.max_dis * frac)
    return kw if S.dis_cap is None else min(kw, S.dis_cap)


def _phys(S: _Seg, e: float, mode: str, net: float, imp_p: float, ckw: float | None = None,
          dkw: float | None = None) -> tuple:
    """One segment of battery physics, consistent with v1's planner.step. `net` is house + car - sun in kWh (positive:
    the house needs energy). Returns (end kWh, cost p, comfort p, import, export, event export, battery to house,
    battery sold, grid to battery, top-up p, level penalty p), energies in kWh, cost including wear, comfort and the
    top-up. The top-up (`top_up_cost_p`: grid energy charged above the comfort band's top) is counted in the comfort
    figure, element 2, and is also returned alone (element 9), because a part-way charge has to price it from its end
    level; the level penalty is counted there too and returned alone as the last element (10), for the same reason."""
    dt, cap = S.dt, S.cap
    imp = exp = evx = house = sold = gtb = top = 0.0
    end = e
    if mode == HOLD:
        if net > 0:
            imp = net
        else:
            exp = -net                              # on the inverter Hold is a charge at 0 W: surplus sun is sold
    elif mode == SELF_USE:
        if net > 0:
            if dkw is None:
                dkw = _dis_kw(S, e)
            out = min(net, dkw * dt, max(0.0, e - S.floor) * S.eta_d)
            end = e - out / S.eta_d
            imp, house = net - out, out
        else:
            if ckw is None:
                ckw = _chg_kw(S, e)
            into = min(-net, ckw * dt, max(0.0, cap - e) / S.eta_c)
            end = e + into * S.eta_c
            exp = -net - into
    elif mode == CHARGE or mode == FREE:
        if ckw is None:
            ckw = _chg_kw(S, e)
        room = max(0.0, (cap if mode == FREE else S.ceil) - e)
        headroom = S.fuse - (net / dt if net > 0 else 0.0)
        into = min(max(0.0, min(ckw, headroom)) * dt, room / S.eta_c)
        end = e + into * S.eta_c
        flow = net + into
        imp, exp = (flow, 0.0) if flow > 0 else (0.0, -flow)
        gtb = max(0.0, into + min(net, 0.0))
        top = _top_up(S, e, end, gtb / into if into > 1e-12 else 0.0) if mode == CHARGE else 0.0
    else:                                           # EXPORT, EVENT
        if dkw is None:
            dkw = _dis_kw(S, e)
        room_kw = max(0.0, min(dkw, S.export_limit + net / dt))
        out = min(room_kw * dt, max(0.0, e - S.floor) * S.eta_d)
        end = e - out / S.eta_d
        flow = net - out
        imp, exp = (flow, 0.0) if flow > 0 else (0.0, -flow)
        house = min(out, max(0.0, net))
        sold = out - house
        if mode == EVENT:
            evx = sold
    cash = imp * imp_p - (exp - evx) * S.export_p - evx * S.event_p + S.wear_h * house + S.wear_s * sold
    comfort = 0.0
    if S.ccost:
        hi, lo = S.hi, S.lo
        ex0 = (e - hi if e > hi else 0.0) + (lo - e if e < lo else 0.0)
        ex1 = (end - hi if end > hi else 0.0) + (lo - end if end < lo else 0.0)
        comfort = S.ccost * dt * 0.5 * (ex0 + ex1)
    pen = 0.0
    if S.pen is not None:
        pen = _pen_partial(S.pen, dt, e, end, 1.0)
    comfort += top + pen
    return (end, cash + comfort, comfort, imp, exp, evx, house, sold, gtb, top, pen)


def _top_up(S: _Seg, e0: float, e1: float, grid_share: float) -> float:
    """Pence for charging from level e0 to e1 (kWh): `top_up_cost_p` on the grid energy that goes in above the comfort
    band's top, only the part above it. `grid_share` is the share of the charge that came from the grid (the rest is
    sun); the energy is counted as bought, so the charging loss is included."""
    if not S.topup or e1 <= S.hi:
        return 0.0
    return S.topup * grid_share * (e1 - max(e0, S.hi)) / S.eta_c


def _grid_share(S: _Seg, full: tuple, hold: tuple) -> float:
    """The share of a full charge's energy that comes from the grid (from its flows); 0 for anything but a charge."""
    into = (full[0] - hold[0]) / S.eta_c
    return min(1.0, full[8] / into) if into > 1e-12 else 0.0


def _mix(a: tuple, b: tuple, f: float, S: _Seg | None = None) -> tuple:
    """f of the segment in `a` (a charge or a sale), the rest in `b` (hold). With `S`, the top-up is priced from the
    level the part-way charge ends at, not scaled with the share (it only applies above the comfort band's top)."""
    g = 1.0 - f
    out = tuple(f * x + g * y for x, y in zip(a, b, strict=True))
    if S is not None and a[9] > 0.0:
        top = _top_up(S, b[0], out[0], _grid_share(S, a, b))
        out = out[:1] + (out[1] - out[9] + top, out[2] - out[9] + top) + out[3:9] + (top,) + out[10:]
    if S is not None and S.pen is not None:               # the level penalty from the level the part-way step reaches
        pen = _pen_partial(S.pen, S.dt, b[0], out[0], f)
        out = out[:1] + (out[1] - out[10] + pen, out[2] - out[10] + pen) + out[3:10] + (pen,)
    return out


# --- the choice in one segment -------------------------------------------------------------------------------------
def _partial(scen: list, full: list, hold: list, Vn: _Arr, S: _Seg | None = None) -> tuple[float, float] | None:
    """The best stop inside a charge (or a sale): the share f of the segment (0 < f < 1) spent on the mode, the rest
    held. The expected cost is convex in f, so the smallest f whose slope reaches zero is found by bisection, then
    snapped to the grid levels either side of it. Returns (expected cost-to-go, f) or None when full or none is best.
    The cost of a part-way charge is the hold cost plus f of the difference, except the top-up (`_top_up`), which only
    starts above the comfort band's top and so is priced from the level the charge reaches."""
    n = len(scen)
    ef = [x[0] for x in full]
    eh = [x[0] for x in hold]
    d_e = [ef[i] - eh[i] for i in range(n)]
    if max(abs(d) for d in d_e) < 1e-9:
        return None
    d_c = [full[i][1] - hold[i][1] for i in range(n)]
    ps = [s[0] for s in scen]
    pen = S.pen if S is not None else None
    base = [hold[i][1] for i in range(n)]
    if pen is not None:      # the level penalty is priced from the level reached, not mixed
        for i in range(n):
            d_c[i] -= full[i][10] - hold[i][10]
            base[i] -= hold[i][10]
    # top-up: per scenario the pence per kWh of level above the band's top, and what the full charge's linear share was
    tc = [0.0] * n
    if S is not None and S.topup:
        for i in range(n):
            if full[i][9] > 0.0:
                tc[i] = S.topup * _grid_share(S, full[i], hold[i]) / S.eta_c
                d_c[i] -= full[i][9]

    def top(i: int, f: float) -> float:
        return tc[i] * max(0.0, eh[i] + f * d_e[i] - max(eh[i], S.hi)) if tc[i] else 0.0

    def pen_f(i: int, f: float) -> float:
        return _pen_partial(pen, S.dt, eh[i], eh[i] + f * d_e[i], f) if pen is not None else 0.0

    def dpen_f(i: int, f: float) -> float:
        return _dpen_partial(pen, S.dt, eh[i], d_e[i], f) if pen is not None else 0.0

    def slope(f: float) -> float:
        t = 0.0
        for i in range(n):
            s_i = d_c[i] + _slope(Vn, eh[i] + f * d_e[i]) * d_e[i] + dpen_f(i, f)
            if tc[i] and (eh[i] >= S.hi or eh[i] + f * d_e[i] > S.hi):
                s_i += tc[i] * d_e[i]
            t += ps[i] * s_i
        return t

    def cost(f: float) -> float:
        t = 0.0
        for i in range(n):
            t += ps[i] * (base[i] + f * d_c[i] + top(i, f) + pen_f(i, f) + _val(Vn, eh[i] + f * d_e[i]))
        return t

    if slope(0.0) >= -SLOPE_TOL or slope(1.0) < -SLOPE_TOL:
        return None
    lo, hi = 0.0, 1.0
    for _ in range(BISECT_STEPS):
        mid = (lo + hi) / 2
        if slope(mid) >= -SLOPE_TOL:
            hi = mid
        else:
            lo = mid
    m = max(range(n), key=lambda i: ps[i])
    best_f, best_g = hi, cost(hi)
    if abs(d_e[m]) > 1e-9:
        x = (eh[m] + hi * d_e[m]) * Vn.inv
        for j in (math.floor(x), math.floor(x) + 1):
            f = min(1.0, max(0.0, (j / Vn.inv - eh[m]) / d_e[m]))
            if 0.0 < f < 1.0:
                g = cost(f)
                if g < best_g - 1e-9 or (abs(g - best_g) <= 1e-9 and f < best_f):
                    best_f, best_g = f, g
    return best_g, best_f


# What a mode is, for the cost of changing between them (as v1's optimiser: self-use, hold, charge, discharge).
NONE_K, HOLD_K, CHARGE_K, DISCHARGE_K = "none", "hold", "charge", "discharge"   # DISCHARGE_K: a sale (Export, Event)
KINDS = (NONE_K, HOLD_K, CHARGE_K, DISCHARGE_K)
KIND = {SELF_USE: NONE_K, HOLD: HOLD_K, CHARGE: CHARGE_K, FREE: CHARGE_K, EXPORT: DISCHARGE_K, EVENT: DISCHARGE_K}


def switch_cost(prev: str, new: str, cost_p: float) -> float:
    """Pence for changing from the kind of mode `prev` to `new`: the one setting for any change, nothing for staying.
    Every change costs the same, so a detour through a third mode (a hold between a charge and a sale) always costs
    more than the direct change and is never taken to save the cost."""
    return 0.0 if prev == new else cost_p


def _candidates(S: _Seg, e: float, scen: list, imp_p: float, Vk: dict,
                force: str | None = None, dscale: float = 1.0) -> list[tuple[float, str, float]]:
    """Every choice in one segment at level e for one price outcome, as (expected cost-to-go, mode, f), before the
    cost of changing mode. f is the share of the segment the mode runs (1 for everything but a charge or sale that
    stops part-way). The cost-to-go of a mode is read from the next segment's curve for the kind of that mode."""
    ckw, dkw = _chg_kw(S, e), _dis_kw(S, e)
    forced = force or S.forced
    if forced:
        Vn = Vk[KIND[forced]]
        g = 0.0
        for p, _so, _ho, net in scen:
            r = _phys(S, e, forced, net, imp_p, ckw, dkw * dscale)
            g += p * (r[1] + _val(Vn, r[0]))
        return [(g, forced, 1.0)]
    out: list[tuple[float, str, float]] = []
    hold = None
    for mode in S.allowed:
        Vn = Vk[KIND[mode]]
        res = [_phys(S, e, mode, s[3], imp_p, ckw, dkw) for s in scen]
        if mode == HOLD:
            hold = res
        elif mode in (CHARGE, EXPORT):
            if hold is None:
                hold = [_phys(S, e, HOLD, s[3], imp_p) for s in scen]
            part = _partial(scen, res, hold, Vn, S)
            if part is not None:
                out.append((part[0], mode, part[1]))
        g = 0.0
        for i in range(len(scen)):
            r = res[i]
            g += scen[i][0] * (r[1] + _val(Vn, r[0]))
        out.append((g, mode, 1.0))
    return out


def _pick(cands: list, prev: str, cost_p: float) -> float:
    """The cheapest candidate once the cost of changing from kind `prev` is added. Ties keep the earlier one in the
    modes' order (a change must beat the one before it by more than EPS)."""
    best = math.inf
    for g, mode, _f in cands:
        g += switch_cost(prev, KIND[mode], cost_p)
        if g < best - EPS:
            best = g
    return best


# --- the solve -----------------------------------------------------------------------------------------------------
class _Core:
    __slots__ = ("segs", "V", "VK", "tv", "tail", "cap", "step", "fine_step")


def _terminal_p(fc: Forecast, facts: BatteryFacts, settings: V2Settings) -> float:
    """The flat price of a kWh left at the end of the look-ahead: what refilling it costs, from the cheapest import
    price in the last 24 hours (or the fixed figure)."""
    if settings.terminal_value == "fixed":
        return settings.terminal_value_p
    segs = fc.segments
    cut = segs[-1].end.timestamp() - 24 * 3600
    prices = [s.import_p for s in segs if not s.free and s.end.timestamp() > cut]
    prices = prices or [s.import_p for s in segs if not s.free]
    if not prices:
        return settings.terminal_value_p
    return min(prices) / facts.eta_charge


CHEAP_BAND_P = 1.0       # a segment within this of the cheapest price in the last 24 h counts as cheap


def _terminal_curve(fc: Forecast, facts: BatteryFacts, settings: V2Settings, step: float, n: int) -> tuple[float, list]:
    """What energy left at the end of the look-ahead is worth, by level: (the price above the knee, the values at
    levels 0, step, .. n * step in pence, negative: a credit).

    The look-ahead ends part-way through a day, so the plan cannot see what the battery will need next. The last 24
    hours of it stand for what happens after the end (the same times of day tomorrow) and are read for two things: how
    much stored energy could still be bought back at the cheap price before the dear stretch begins (R: the rest of
    the cheap window, if the end is inside one, at the charge rate), and how much the house takes from the battery in
    that dear stretch (D). Below the level that leaves just enough to reach D with R refilled, a kWh is worth what it
    saves in the dear stretch; above it, a kWh can be bought back (or, with no refill to come, sold), so it is worth
    no more than the refill price or what a sale brings. Without this the end of the plan sold the battery down to
    the floor in the last hours of a cheap window (8 Oct 2026), or just after the dear rate began (7 Oct, the 0.9.119
    case), though the energy was needed in the dear stretch that followed."""
    refill = _terminal_p(fc, facts, settings)
    flat = [-refill * i * step for i in range(n + 1)]
    segs = fc.segments
    if settings.terminal_value == "fixed" or not segs:
        return refill, flat
    cut = segs[-1].end - timedelta(hours=24)
    ghost = [g for g in segs if g.end > cut and not g.free]
    prices = [g.import_p for g in ghost]
    if not prices:
        return refill, flat
    cheap_p = min(prices) + CHEAP_BAND_P
    sale = segs[-1].export_p * facts.eta_discharge - settings.wear_sale_p
    charge_kw = facts.max_charge_kw * facts.charge_factor

    def part(g: Segment) -> float:                  # the share of the segment after the cut (the first may be cut)
        return min(1.0, (g.end - max(g.start, cut)).total_seconds() / max(g.hours * 3600, 1e-9))

    i, refillable = 0, 0.0
    while i < len(ghost) and ghost[i].import_p <= cheap_p:           # the cheap window the end may be inside
        refillable += charge_kw * ghost[i].hours * part(ghost[i]) * facts.eta_charge
        i += 1
    need = weighted = 0.0
    while i < len(ghost) and ghost[i].import_p > cheap_p:            # the dear stretch after it
        g = ghost[i]
        net = max(0.0, g.load_kwh.mid - g.solar_kwh.mid) * part(g)
        need += net
        weighted += net * g.import_p
        i += 1
    above = refill if refillable > 0.0 else max(refill, sale)
    if need < 0.05:
        return above, [-above * k * step for k in range(n + 1)]
    below = weighted / need * facts.eta_discharge        # a stored kWh used in the dear stretch saves this
    if below <= above + EPS:
        return above, [-above * k * step for k in range(n + 1)]
    cap = facts.capacity_kwh
    knee = min(cap, max(0.0, settings.reserve_soc / 100 * cap + need / facts.eta_discharge - refillable))
    return above, [-(below * min(k * step, knee) + above * max(0.0, k * step - knee)) for k in range(n + 1)]


# An event may start or end part-way through a segment, so it sells at a quarter, a half, three quarters or all of the
# full power (four sizes, not one, so the kink the sale's limit puts in the value is not at the same level every time).
LATE_SHARES = (0.25, 0.5, 0.75, 1.0)
LATE_NOTICE_S = 30 * 60        # an event starting within this is already announced, so it is not a late one


def _late_events(segs: list, settings: V2Settings, now: datetime) -> None:
    """Give each future, unforced segment the chance that a grid event no one has announced yet is running in it
    (`S.late_p`): events a week / 7 x hours each / 24, the share of time spent inside one. The event pays what a known
    one does (`event_value_p`, plus the export rate when paid on top) and sells down to the segment's floor."""
    q = 0.0
    if settings.late_events and settings.events and settings.late_events_per_week > 0:
        q = min(0.5, settings.late_events_per_week / 7.0 * settings.late_event_hours / 24.0
                / (sum(LATE_SHARES) / len(LATE_SHARES)))       # the sales' average size is under the full power
    for S in segs:
        S.late_p = 0.0
        if q and not S.forced and (S.seg.start - now).total_seconds() >= LATE_NOTICE_S and S.event_p == 0.0:
            S.late_p = q
            S.event_p = settings.event_value_p + (S.export_p if settings.event_plus_export else 0.0)


def _backward(fc: Forecast, facts: BatteryFacts, settings: V2Settings, limits_for, now: datetime,
              fine: float, coarse: float) -> _Core:
    """The value curves, backwards. The state is the level and the kind of the mode before (so a change of mode costs
    `switch_cost_p`): `core.VK[k][kind]` is the cost-to-go from segment k's start having come from that kind, and
    `core.V[k]` the one for self-use, which is what `lam` (the value of a stored kWh) is read from."""
    cap = facts.capacity_kwh
    core = _Core()
    core.cap = cap
    core.segs = [_make(s, facts, settings, limits_for(s)) for s in fc.segments]
    _late_events(core.segs, settings, now)
    nf, sf = _grid(cap, fine)
    nc, sc = _grid(cap, max(coarse, fine))
    core.step, core.fine_step = sf, sf
    core.tv, tail_values = _terminal_curve(fc, facts, settings, sc, nc)
    core.tail = tail = _Arr(tail_values, sc)
    VK: list[dict] = [{k: tail for k in KINDS}] * (len(core.segs) + 1)
    horizon = FINE_HOURS * 3600
    cost_p = settings.switch_cost_p
    for k in range(len(core.segs) - 1, -1, -1):
        S = core.segs[k]
        fine_here = (S.seg.start - now).total_seconds() < horizon
        n, step = (nf, sf) if fine_here else (nc, sc)
        Vk = VK[k + 1]
        rows = {kind: [] for kind in KINDS}
        outcomes, scen, q = S.outcomes, S.scen, S.late_p
        for i in range(n + 1):
            e = i * step
            tot = dict.fromkeys(KINDS, 0.0)
            for pw, ip in outcomes:
                cands = _candidates(S, e, scen, ip, Vk)
                for kind in KINDS:
                    tot[kind] += pw * (1.0 - q) * _pick(cands, kind, cost_p)
            if q:                                   # a late grid event: a forced sale, no choice, no change cost
                g = sum(_candidates(S, e, scen, _outcome_price(S), Vk, force=EVENT, dscale=f)[0][0]
                        for f in LATE_SHARES) * q / len(LATE_SHARES)
                for kind in KINDS:
                    tot[kind] += g
            for kind in KINDS:
                rows[kind].append(tot[kind])
        VK[k] = {kind: _Arr(rows[kind], step) for kind in KINDS}
    core.VK = VK
    core.V = [d[NONE_K] for d in VK]
    return core


def _lam_row(a: _Arr) -> list[float]:
    """The value of one more stored kWh at each level: the slope of the cost-to-go to the next level up (the last level
    takes the slope below it). A one-sided slope, not a central one, because the floors, the ceiling and the comfort
    band's edges are kinks that fall on levels: a central difference would give half the value at the floor, which is
    exactly where a battery at its reserve sits."""
    v, st = a.v, a.step
    out = [(v[i] - v[i + 1]) / st for i in range(a.n)]
    out.append(out[-1])
    return out


LATE_SMOOTH_KWH = 0.5


def _smoothed(row: list[float], step: float) -> list[float]:
    """Each level's value averaged with its neighbours within `LATE_SMOOTH_KWH` either side. The programme's choices
    snap to the level grid, and with a late event in the plan the snapping shows as dips of several pence in the value
    of a stored kWh from one level to the next; a half kWh is far less than a decision is made on."""
    k = max(1, int(round(LATE_SMOOTH_KWH / step)))
    n = len(row)
    out = []
    for i in range(n):
        lo, hi = max(0, i - k), min(n, i + k + 1)
        out.append(sum(row[lo:hi]) / (hi - lo))
    return out


def _fine_lam(a: _Arr, n: int, step: float, smooth: bool = False) -> tuple[float, ...]:
    row = _lam_row(a)
    if smooth:
        row = _smoothed(row, a.step)
    if a.n == n:
        return tuple(row)
    lam = _Arr(row, a.step)
    return tuple(_val(lam, i * step) for i in range(n + 1))


# --- forward runs --------------------------------------------------------------------------------------------------
def _outcome_price(S: _Seg) -> float:
    return max(S.outcomes, key=lambda o: o[0])[1]


def _charge_cross(row, step: float, e: float, buy: float, top: float, hi: float, tol: float) -> float:
    """The level (kWh) a charge from e stops at, going up: where the value stops being above the buy line (`buy`, p per
    kWh stored), which is `top` higher for energy that lands above the comfort band's top `hi` (kWh)."""
    x = _cross(row, step, e, (buy + top if e >= hi else buy) + tol, True)
    if e < hi < x:
        if _row_at(row, step, hi) > buy + top + tol:
            x = max(hi, _cross(row, step, hi, buy + top + tol, True))
        else:
            x = hi
    return x


def _cross(row, step: float, e: float, thr: float, up: bool) -> float | None:
    """The level (kWh), going up (`up`) or down from e, where the value row crosses `thr`: up, the first level at
    which the value is no longer above thr; down, the first at which it is no longer below it. The row's end if it
    never does."""
    n = len(row) - 1
    x0 = min(float(n), max(0.0, e / step))
    prev, v_prev = x0, _row_at(row, step, e)
    rng = range(int(x0) + 1, n + 1) if up else range(math.ceil(x0) - 1, -1, -1)
    for j in rng:
        crossed = row[j] <= thr if up else row[j] >= thr
        if crossed:
            t = (v_prev - thr) / (v_prev - row[j]) if v_prev != row[j] else 0.0
            return (prev + t * (j - prev)) * step
        prev, v_prev = float(j), row[j]
    return n * step if up else 0.0


def _end_row(lam, k: int):
    """The value curve a decision in segment k compares against: what a stored kWh is worth when the segment ends
    (the next segment's curve; the last segment's own). The curve at a segment's start already includes that segment's
    own choice, so using it would stop a charge early and restart it in every segment."""
    return lam[min(k + 1, len(lam) - 1)]


def _policy(S: _Seg, row, step: float, e: float, imp_p: float, net: float, prev: str | None,
            band: float) -> tuple[str, float | None]:
    """The mode layer 5 would be in at level e for this segment, by the value curve alone (the lines, with the price
    band as hysteresis: entering a mode needs the band, staying tolerates it): (mode, the level a charge or a sale
    stops at). Used for the expected timeline and the path."""
    if S.forced:
        return S.forced, None
    value = _row_at(row, step, e)
    ec, ed = S.eta_c, S.eta_d
    buy, sell = imp_p / ec, S.export_p * ed - S.wear_s
    use, store = imp_p * ed - S.wear_h, S.export_p / ec
    bc, bd = band / ec, band * ed
    top = S.topup / ec
    if CHARGE in S.allowed and e < S.ceil - 1e-9 \
            and value > buy + (top if e >= S.hi else 0.0) + (-bc if prev == CHARGE else bc):
        return CHARGE, min(S.ceil, _charge_cross(row, step, e, buy, top, S.hi, LINE_TOL / ec))
    if EXPORT in S.allowed and e > S.floor + 1e-9 and value < sell + (bd if prev == EXPORT else -bd):
        return EXPORT, max(S.floor, _cross(row, step, e, sell - LINE_TOL, False))
    if net > 0:
        edge = use + (bd if prev == SELF_USE else -bd if prev == HOLD else 0.0)
        want = SELF_USE if value < edge else HOLD
    else:
        edge = store + (-bc if prev == SELF_USE else bc if prev == HOLD else 0.0)
        want = SELF_USE if value > edge else HOLD
    if want in S.allowed:
        return want, None
    return (S.allowed[0] if S.allowed else SELF_USE), None


def _stretch_ends(segs: list[_Seg]) -> list[int]:
    """For each segment, the index of the last segment of its stretch: the run of unforced segments with the same
    effective import price (within 0.01p). A forced segment is a stretch of its own."""
    ends = list(range(len(segs)))
    for i in range(len(segs) - 2, -1, -1):
        a, b = segs[i], segs[i + 1]
        if not a.forced and not b.forced and abs(_outcome_price(a) - _outcome_price(b)) < 0.01:
            ends[i] = ends[i + 1]
    return ends


def _dp_choice(S: _Seg, e: float, imp_p: float, vk_next: dict, prev: str | None) -> tuple[str, float]:
    """The mode (and the share of the segment it runs) the programme itself chooses at level e, coming from the mode
    `prev`: the same candidates and the same cost of a change as the backward pass, so the expected timeline is the
    policy the values were worked out for, not a second rule laid over the curve."""
    prev_kind = KIND[prev] if prev else NONE_K
    best, pick = math.inf, (SELF_USE, 1.0)
    for g, mode, f in _candidates(S, e, S.scen, imp_p, vk_next):
        g += switch_cost(prev_kind, KIND[mode], S.sw)
        if g < best - EPS:
            best, pick = g, (mode, f)
    return pick


def _walk(segs: list[_Seg], rows: list, step: float, e0: float, kind: str, band: float, front: bool = True,
          prev: str | None = None, first: int = 0, vks: list | None = None) -> list[dict]:
    """Run forward over `segs` from level e0 kWh; `rows[i]` is the value curve segment i compares against. kind: "mid"
    (middle sun, middle house), "low" / "high" (the low / high net-load group), "self" (Self-use wherever nothing is
    forced). The mode in each segment is what layer 5 would choose from the value curve at that level and those
    prices (`_policy`); a charge or sale stops where the curve says.

    `front`: within a stretch of one import price, charging costs the same early or late, and late is risky (the rate
    tapers, a slot is withdrawn, the forecast is wrong). So if the policy charges anywhere in the stretch, the same
    charge is moved to the start of the stretch: full power from the first segment until the level the policy would
    have reached by the stretch's end, then the policy's own choice. Only up to the comfort band's top: above it a
    kWh held costs per hour and a kWh charged costs the top-up, so early and late are no longer the same cost and
    the programme's own timing (late) stands: the early charge takes whole steps up to the top, and the step that
    would cross it is the programme's own choice.

    `prev` is the mode running now. A sale running at the start (`prev` is Export and the programme itself goes on
    selling) is not turned round by the stretch's early charge: the executor goes on with the sale (`choice_now`), so a
    plan that opened with "charge now" was one nothing followed, and every revaluation opened with it again."""
    ends = _stretch_ends(segs)
    recs, e, prev_mode = [], e0, prev
    target, target_to, capped = None, -1, False
    for i, S in enumerate(segs):
        if kind == "mid":
            so, ho = S.mid
            net = ho + S.car - so
        else:
            _, so, ho, net = S.scen[-1] if kind == "high" else S.scen[0]
        imp_p = _outcome_price(S)
        if front and kind != "self" and not S.forced and i > target_to and (i == 0 or ends[i - 1] < i):
            sub = _walk(segs[i:ends[i] + 1], rows[i:ends[i] + 1], step, e, kind, band, False, prev_mode,
                        vks=None if vks is None else vks[i:ends[i] + 1])
            reach = sub[-1]["e1"]
            sale_goes_on = i == 0 and prev == EXPORT and sub[0]["mode"] == EXPORT
            capped = bool((S.ccost or S.topup or S.pen is not None) and reach > S.fcap)
            if capped:                              # early is free only inside the band: above its top the hours cost
                reach = S.fcap
            target = reach if (any(r["mode"] == CHARGE for r in sub) and reach > e + 1e-9
                               and not sale_goes_on) else None
            target_to = ends[i]
        if kind == "self":
            mode, stop = S.forced or SELF_USE, None
        elif (front and target is not None and i <= target_to and not S.forced and CHARGE in S.allowed
              and e < target - 1e-9 and e < S.ceil - 1e-9
              and not (capped and _phys(S, e, CHARGE, net, imp_p)[0] > target + 1e-9)):
            mode, stop = CHARGE, min(target, S.ceil)
        elif vks is not None:
            mode, share = _dp_choice(S, e, imp_p, vks[i], prev_mode)
            stop = None
            if share < 1.0:                                  # a charge or sale that stops part-way: where it stops
                lo, hi = _phys(S, e, HOLD, net, imp_p)[0], _phys(S, e, mode, net, imp_p)[0]
                stop = lo + share * (hi - lo)
        else:
            mode, stop = _policy(S, rows[i], step, e, imp_p, net, prev_mode, band)
        full = _phys(S, e, mode, net, imp_p)
        res, e_hold, f = full, e, 1.0
        if stop is not None:
            hold = _phys(S, e, HOLD, net, imp_p)
            span = full[0] - hold[0]
            f = min(1.0, max(0.0, (stop - hold[0]) / span)) if abs(span) > 1e-9 else 1.0
            if f < 1.0:
                res, e_hold = _mix(full, hold, f, S), hold[0]
        change = switch_cost(KIND[prev_mode], KIND[mode], S.sw) if prev_mode is not None else 0.0
        prev_mode = HOLD if f < 1.0 else mode
        recs.append({"k": first + i, "mode": mode, "f": f, "e0": e, "e1": res[0], "e_full": full[0], "e_hold": e_hold,
                     "cost": res[1] + change, "comfort": res[2], "imp": res[3], "exp": res[4], "evx": res[5],
                     "house": res[6], "sold": res[7], "gtb": res[8], "imp_p": imp_p})
        e = res[0]
    return recs


def _forward(core: _Core, lam: tuple, step: float, e0: float, kind: str, band: float,
             prev: str | None = None) -> tuple[list[dict], float]:
    """`_walk` over the whole forecast. Returns (records, cost including the credit for the energy left at the end)."""
    rows = [_end_row(lam, k) for k in range(len(core.segs))]
    recs = _walk(core.segs, rows, step, e0, kind, band, prev=prev, vks=[core.VK[k + 1] for k in range(len(core.segs))])
    return recs, sum(r["cost"] for r in recs) + _val(core.tail, recs[-1]["e1"])


def _level_at(rec: dict, x: float) -> float:
    """Battery level (kWh) a fraction x of the way through a segment's record."""
    e0, f = rec["e0"], rec["f"]
    if f >= 1.0:
        return e0 + (rec["e1"] - e0) * x
    return e0 + (rec["e_full"] - e0) * min(x, f) + (rec["e_hold"] - e0) * max(0.0, x - f)


def _path(core: _Core, now: datetime, runs: dict[str, list[dict]]) -> dict:
    segs = core.segs
    end = segs[-1].seg.end
    starts = [s.seg.start for s in segs]
    cap = core.cap
    n = int((end - now).total_seconds() // (PATH_STEP_MIN * 60)) + 1
    out: dict = {"start": now.isoformat(), "step_min": PATH_STEP_MIN}
    for name, recs in runs.items():
        vals = []
        for i in range(n):
            t = now.timestamp() + i * PATH_STEP_MIN * 60
            tt = datetime.fromtimestamp(t, tz=timezone.utc)
            k = max(0, min(len(segs) - 1, bisect_right(starts, tt) - 1))
            seg = segs[k].seg
            span = (seg.end - seg.start).total_seconds()
            x = min(1.0, max(0.0, (t - seg.start.timestamp()) / span)) if span > 0 else 0.0
            vals.append(round(_level_at(recs[k], x) / cap * 100, 1))
        out[name] = vals
    return out


# --- words ---------------------------------------------------------------------------------------------------------
def _pp(x: float) -> str:
    s = f"{x:.2f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def _local(t: datetime, tz) -> datetime:
    return t.astimezone(tz) if tz else t


def _when(t: datetime, now: datetime, tz) -> str:
    lt, ln = _local(t, tz), _local(now, tz)
    if lt.date() == ln.date():
        return f"{lt:%H:%M}"
    return f"{lt:%H:%M} tomorrow" if (lt.date() - ln.date()).days == 1 else f"{lt:%a %H:%M}"


def _ahead(recs: list[dict], segs: list[_Seg], after: int, cur_imp: float, now: datetime, tz) -> str:
    """What is coming that explains the value, as the end of a sentence ('' when nothing stands out)."""
    for j in range(after, len(recs)):
        r, seg = recs[j], segs[j].seg
        when = _when(seg.start, now, tz)
        if r["mode"] == EVENT:
            return f", because a {N('event')} event at {when} pays {_pp(seg.event_p)}p a kWh"
        if r["mode"] == SELF_USE and r["house"] > 0.02 and r["imp_p"] > cur_imp + 1.0:
            return f", because the house would otherwise buy at {_pp(r['imp_p'])}p from {when}"
        if r["mode"] == EXPORT and r["sold"] > 0.02:
            return f", because it can sell at {_pp(seg.export_p)}p from {when}"
    return ""


def _reason(mode: str, rec: dict, S: _Seg, value: float, ahead: str, by_level: bool, facts: BatteryFacts) -> str:
    seg = S.seg
    imp, exp = rec["imp_p"], seg.export_p
    v = _pp(value)
    if S.forced and seg.manual and S.forced == seg.manual and not seg.event:
        return f"Manual override: the mode is fixed by you here (import {_pp(imp)}p, a stored kWh is worth {v}p)."
    if mode == EVENT:
        return (f"{N('event')} event: export at full power, paid {_pp(seg.event_p)}p a kWh "
                f"(a stored kWh is worth {v}p otherwise){ahead}.")
    if mode == FREE:
        return f"Free power: charge while it costs 0p (a stored kWh is worth {v}p)."
    if mode == CHARGE:
        return f"Charge at {_pp(imp)}p: a stored kWh is worth {v}p here{ahead}."
    if mode == EXPORT:
        return (f"Sell at {_pp(exp)}p: a stored kWh is worth only {v}p here, less than the "
                f"{_pp(exp * facts.eta_discharge)}p a sale brings{ahead}.")
    if mode == HOLD:
        if by_level:
            return f"Hold: the charge has reached its target (a stored kWh is worth {v}p, import {_pp(imp)}p)."
        if rec["exp"] > 0.02 and rec["imp"] <= 0.02:
            return (f"Hold: spare sun is sold at {_pp(exp)}p because a stored kWh is worth only {v}p here; "
                    f"storing it would need {_pp(exp / facts.eta_charge)}p{ahead}.")
        if value >= imp * facts.eta_discharge:
            return (f"Hold at {_pp(imp)}p: the grid runs the house; a stored kWh is worth {v}p here, more than the "
                    f"{_pp(imp * facts.eta_discharge)}p it saves now{ahead}.")
        return f"Hold at {_pp(imp)}p: the grid runs the house; a stored kWh is worth {v}p here{ahead}."
    if rec["house"] > 0.02:
        return (f"Self-use at {_pp(imp)}p: the battery covers the house; a stored kWh is worth {v}p here, "
                f"under the {_pp(imp * facts.eta_discharge)}p it saves{ahead}.")
    if rec["e1"] > rec["e0"] + 0.02:
        return (f"Self-use: spare sun goes into the battery; a stored kWh is worth {v}p here, more than the "
                f"{_pp(exp)}p export pays{ahead}.")
    return f"Self-use at {_pp(imp)}p: nothing to move now; a stored kWh is worth {v}p here{ahead}."


def _timeline(core: _Core, recs: list[dict], lam: tuple, step: float, now: datetime, tz,
              facts: BatteryFacts) -> tuple[TimelineItem, ...]:
    cap = core.cap
    pieces = []     # (mode, start, end, e_start, e_end, rec index, by_level)
    for rec in recs:
        seg = core.segs[rec["k"]].seg
        dt = seg.end - seg.start
        if rec["f"] < 1.0:
            cut = seg.start + dt * rec["f"]
            pieces.append((rec["mode"], seg.start, cut, rec["e0"], _level_at(rec, rec["f"]), rec["k"], True))
            pieces.append((HOLD, cut, seg.end, _level_at(rec, rec["f"]), rec["e1"], rec["k"], True))
        else:
            pieces.append((rec["mode"], seg.start, seg.end, rec["e0"], rec["e1"], rec["k"], False))
    merged: list[list] = []
    for p in pieces:
        if (p[2] - p[1]).total_seconds() < 30:
            continue
        if merged and merged[-1][0] == p[0]:
            m = merged[-1]
            m[2], m[4], m[6] = p[2], p[4], p[6]
            m[7] = p[5]
        else:
            merged.append(list(p) + [p[5]])
    items = []
    for idx, m in enumerate(merged):
        mode, t0, t1, e0, e1, k0, by_level, k_last = m
        S = core.segs[k0]
        span = recs[k0:k_last + 1]
        rec = dict(span[0])                       # the item's first record, with the flows of the whole item
        for key in ("house", "exp", "imp", "sold"):
            rec[key] = sum(r[key] for r in span)
        rec["e1"] = span[-1]["e1"]
        row = _end_row(lam, k0)
        x = min(len(row) - 1.0, max(0.0, e0 / step))
        j = int(x)
        value = row[j] + (x - j) * (row[min(j + 1, len(row) - 1)] - row[j])
        ahead = _ahead(recs, core.segs, k_last + 1, rec["imp_p"], now, tz)
        soc1 = e1 / cap * 100
        nxt = merged[idx + 1] if idx + 1 < len(merged) else None
        if mode in (CHARGE, EXPORT, FREE) and (by_level or mode == FREE) and abs(e1 - e0) > 1e-6:
            until = f"until {soc1:.0f}%"
        elif mode == EVENT:
            until = f"until the {N('event')} event ends at {_when(t1, now, tz)}"
        elif nxt is not None and nxt[0] == EVENT:
            until = f"until the {N('event')} event at {_when(t1, now, tz)}"
        elif nxt is None:
            until = f"until the end of the look-ahead ({_when(t1, now, tz)})"
        else:
            until = f"until {_when(t1, now, tz)}"
        items.append(TimelineItem(mode=mode, start=t0, end=t1, level_start=round(e0 / cap * 100, 1),
                                  level_end=round(soc1, 1), until=until,
                                  reason=_reason(mode, rec, S, value, ahead, by_level, facts)))
    return tuple(items)


# --- public --------------------------------------------------------------------------------------------------------
def solve(forecast: Forecast, start_soc: float, facts: BatteryFacts, settings: V2Settings, limits_for,
          now: datetime, because: str, tz=None, running: str | None = None) -> ValueResult:
    """Work out the value curve for the forecast, then the expected timeline from `start_soc` (percent). `limits_for`
    gives each segment's allowed modes, floors and caps (layer 4). `running` is the mode now (layer 5's): the expected
    timeline starts from it, so its first step is the programme's own choice from there, as `choice_now` gives."""
    t_start = time.perf_counter()
    if not forecast.segments:
        return ValueResult(made_at=now, because=because, forecast=forecast, step_kwh=facts.capacity_kwh, lam=(),
                           timeline=(), path={"start": now.isoformat(), "step_min": PATH_STEP_MIN, "mid": [],
                                              "low": [], "high": []},
                           cost_expected_p=0.0, cost_selfuse_p=0.0, comfort_given_up_p=None,
                           calc_s=time.perf_counter() - t_start)
    cap = facts.capacity_kwh
    core = _backward(forecast, facts, settings, limits_for, now, settings.level_step_kwh, COARSE_STEP_KWH)
    n_fine, step = _grid(cap, settings.level_step_kwh)
    late = bool(settings.late_events and settings.events and settings.late_events_per_week > 0)
    lam = tuple(_fine_lam(core.V[k], n_fine, step, late) for k in range(len(core.segs)))
    e0 = min(cap, max(0.0, start_soc / 100 * cap))
    mid, cost_mid = _forward(core, lam, step, e0, "mid", settings.price_band_p, running)
    low, _ = _forward(core, lam, step, e0, "low", settings.price_band_p, running)
    high, _ = _forward(core, lam, step, e0, "high", settings.price_band_p, running)
    _, cost_self = _forward(core, lam, step, e0, "self", settings.price_band_p)
    given_up = None
    if settings.comfort_cost_p > 0 or settings.top_up_cost_p > 0:      # the top-up is part of the comfort figure
        plain = replace(settings, comfort_cost_p=0.0, top_up_cost_p=0.0)
        core0 = _backward(forecast, facts, plain, limits_for, now, COARSE_STEP_KWH, COARSE_STEP_KWH)
        n0, step0 = _grid(cap, COARSE_STEP_KWH)
        lam0 = tuple(_fine_lam(core0.V[k], n0, step0) for k in range(len(core0.segs)))
        _, cost0 = _forward(core0, lam0, step0, e0, "mid", settings.price_band_p)
        cash_a = cost_mid - sum(r["comfort"] for r in mid)
        given_up = max(0.0, cash_a - cost0)
    path = _path(core, now, {"mid": mid, "low": low, "high": high})
    timeline = _timeline(core, mid, lam, step, now, tz, facts)
    return ValueResult(made_at=now, because=because, forecast=forecast, step_kwh=step, lam=lam, timeline=timeline,
                       path=path, cost_expected_p=cost_mid, cost_selfuse_p=cost_self, comfort_given_up_p=given_up,
                       calc_s=time.perf_counter() - t_start, limits=tuple(S.lim for S in core.segs),
                       vk=tuple(core.VK[1:]))


def _segment_index(vr: ValueResult, t: datetime) -> int:
    starts = [s.start for s in vr.forecast.segments]
    return max(0, min(len(starts) - 1, bisect_right(starts, t) - 1))


def _row_at(row, step: float, e: float) -> float:
    x = min(len(row) - 1.0, max(0.0, e / step))
    j = int(x)
    return row[j] + (x - j) * (row[min(j + 1, len(row) - 1)] - row[j])


def value_at(vr: ValueResult, t: datetime, soc: float) -> float:
    """What a stored kWh is worth (p/kWh) at time t and battery level soc (percent): the value curve at the end of the
    segment holding t (see `_end_row`), read at the level. Zero when there is no value curve."""
    if not vr.lam:
        return 0.0
    row = _end_row(vr.lam, _segment_index(vr, t))
    cap = vr.step_kwh * (len(row) - 1)
    return _row_at(row, vr.step_kwh, soc / 100 * cap)


def run_target(vr: ValueResult, t: datetime, soc: float, import_p: float, facts: BatteryFacts,
               settings: V2Settings) -> float | None:
    """Run the policy (middle scenario) from level `soc` (percent) at time t to the end of the stretch of one import
    price that holds t (the price within 0.01p of `import_p`). If it charges anywhere in the stretch, the level
    (percent) it reaches by the stretch's end, else None. No new solve: it reuses the value curves and the limits the
    result was made with, and walks only the segments of the stretch."""
    if not vr.lam or len(vr.limits) != len(vr.forecast.segments):
        return None
    k0 = _segment_index(vr, t)
    segs = vr.forecast.segments
    if t >= segs[-1].end or abs(_effective_price(segs[k0]) - import_p) >= 0.01:
        return None
    k1 = k0
    while (k1 + 1 < len(segs) and not _forced_of(segs[k1 + 1], vr.limits[k1 + 1])
           and abs(_effective_price(segs[k1 + 1]) - import_p) < 0.01):
        k1 += 1
    if _forced_of(segs[k0], vr.limits[k0]):
        return None
    cut = []
    for k in range(k0, k1 + 1):
        sg = segs[k]
        if k == k0 and t > sg.start:                  # only what is left of the segment now running
            frac = (sg.end - t).total_seconds() / (sg.end - sg.start).total_seconds()
            sg = replace(sg, start=t, solar_kwh=_scaled(sg.solar_kwh, frac), load_kwh=_scaled(sg.load_kwh, frac))
        cut.append(_make(sg, facts, settings, vr.limits[k]))
    rows = [_end_row(vr.lam, k) for k in range(k0, k1 + 1)]
    cap = vr.step_kwh * (len(vr.lam[0]) - 1)
    vks = [vr.vk[k] for k in range(k0, k1 + 1)] if len(vr.vk) == len(segs) else None
    recs = _walk(cut, rows, vr.step_kwh, min(cap, max(0.0, soc / 100 * cap)), "mid", settings.price_band_p, False,
                 None, k0, vks)
    if not any(r["mode"] == CHARGE for r in recs):
        return None
    reach = recs[-1]["e1"] / cap * 100
    top_pct = settings.comfort_high_soc if (settings.comfort_cost_p > 0 or settings.top_up_cost_p > 0) else 100.0
    if LEVEL_TOP_RATE_AT_FULL_P > 0.0:
        top_pct = min(top_pct, LEVEL_TOP_START_SOC)
    if top_pct < 100.0:                                               # early only up to the band's top (see `_walk`)
        reach = min(reach, top_pct)
    return reach


def choice_now(vr: ValueResult, t: datetime, soc: float, import_p: float, facts: BatteryFacts,
               settings: V2Settings, prev: str | None) -> tuple[str, float] | None:
    """The mode the programme itself chooses for the segment running at `t`, at level `soc` (percent) and the live
    import price, coming from the mode `prev`: (mode, share of the segment it runs). It is the backward pass's own
    choice (same candidates, same cost of a change), so what the live decision does can't disagree with the plan about
    a sale or a charge that only looks good one step at a time. None when it can't say (no curves, a forced segment)."""
    segs = vr.forecast.segments
    if not vr.lam or len(vr.vk) != len(segs) or len(vr.limits) != len(segs) or not segs or t >= segs[-1].end:
        return None
    k0 = _segment_index(vr, t)
    sg = segs[k0]
    if _forced_of(sg, vr.limits[k0]):
        return None
    if t > sg.start:                                    # only what is left of the segment now running
        frac = (sg.end - t).total_seconds() / (sg.end - sg.start).total_seconds()
        sg = replace(sg, start=t, solar_kwh=_scaled(sg.solar_kwh, frac), load_kwh=_scaled(sg.load_kwh, frac))
    S = _make(sg, facts, settings, vr.limits[k0])
    cap = vr.step_kwh * (len(vr.lam[0]) - 1)
    return _dp_choice(S, min(cap, max(0.0, soc / 100 * cap)), import_p, vr.vk[k0], prev)


def _forced_of(sg: Segment, lim: Limits) -> str | None:
    return lim.forced or (EVENT if sg.event else FREE if sg.free else sg.manual)


def _effective_price(sg: Segment) -> float:
    if sg.slot_prob is not None and sg.slot_import_p is not None and sg.slot_prob >= 1:
        return sg.slot_import_p
    return sg.import_p if not sg.slot_prob or sg.slot_import_p is None else \
        (sg.slot_import_p if sg.slot_prob >= 0.5 else sg.import_p)


def _scaled(sp: Spread, f: float) -> Spread:
    return Spread(sp.low * f, sp.mid * f, sp.high * f, sp.w)


def lines(vr: ValueResult, t: datetime, soc: float, import_p: float, export_p: float, facts: BatteryFacts,
          settings: V2Settings) -> Lines:
    """The live comparison in value terms. `charge_target_soc` is where, going up from `soc`, a stored kWh stops being
    worth more than the import price after charging losses (None when it is not above it at `soc`, ties included);
    `sell_floor_soc` is where, going down from `soc`, a stored kWh stops being worth less than a sale brings.

    Charging is also asked about as a run: `run_target_soc` is the level the policy reaches by the end of the stretch
    of this import price (`run_target`). When that is more than the level band above `soc`, `charge_now` is true even
    where the value alone is on the buy line (charging early costs the same as late and leaves room for surprises),
    and `charge_target_soc` is at least that level. `charge_now` is also true when the value says to buy."""
    ec, ed = facts.eta_charge, facts.eta_discharge
    buy, sell = import_p / ec, export_p * ed - settings.wear_sale_p
    use, store = import_p * ed - settings.wear_house_p, export_p / ec
    value = value_at(vr, t, soc)
    target = floor = None
    top = settings.top_up_cost_p / ec
    if soc >= settings.comfort_high_soc:                 # energy charged above the band's top costs the top-up more
        buy += top
    if vr.lam:
        row = _end_row(vr.lam, _segment_index(vr, t))
        step = vr.step_kwh
        cap = step * (len(row) - 1)
        e = soc / 100 * cap
        if value > buy + LINE_TOL / ec:
            hi = settings.comfort_high_soc / 100 * cap
            target = _charge_cross(row, step, e, import_p / ec, top, hi, LINE_TOL / ec) / cap * 100
        if value < sell - LINE_TOL:
            floor = _cross(row, step, e, sell - LINE_TOL, False) / cap * 100
    run = run_target(vr, t, soc, import_p, facts, settings)
    now_charge = target is not None
    if run is not None and run > soc + settings.level_band_pct:
        now_charge = True
        target = max(target if target is not None else soc, run)
    return Lines(value_p=value, buy_line_p=buy, sell_line_p=sell, use_line_p=use, store_sun_line_p=store,
                 import_p=import_p, export_p=export_p, charge_target_soc=target, sell_floor_soc=floor,
                 charge_now=now_charge, run_target_soc=run)


def segment_step(e_kwh: float, mode: str, seg: Segment, solar_kwh: float, load_kwh: float, import_p: float,
                 facts: BatteryFacts, settings: V2Settings, lim: Limits, end_kwh: float | None = None):
    """The battery physics of one segment (consistent with v1's planner.step). `solar_kwh` and `load_kwh` are for the
    whole segment (the car's draw, `seg.car_kw`, is added to the house). Returns (end kWh, cost in pence including
    wear and comfort, flows). `end_kwh` makes a Charge or Export stop when the battery reaches that level and hold for
    the rest of the segment."""
    S = _make(seg, facts, settings, lim)
    net = load_kwh + S.car - solar_kwh
    e = min(S.cap, max(0.0, e_kwh))
    res = _phys(S, e, mode, net, import_p)
    f = 1.0
    if end_kwh is not None and mode in (CHARGE, EXPORT):
        hold = _phys(S, e, HOLD, net, import_p)
        span = res[0] - hold[0]
        if abs(span) > 1e-9:
            f = min(1.0, max(0.0, (end_kwh - hold[0]) / span))
            res = _mix(res, hold, f, S)
    flows = {"import_kwh": res[3], "export_kwh": res[4], "event_export_kwh": res[5], "battery_to_house_kwh": res[6],
             "battery_sold_kwh": res[7], "grid_to_battery_kwh": res[8], "comfort_p": res[2],
             "cash_p": res[1] - res[2], "share_of_segment": f, "hours": S.dt}
    return res[0], res[1], flows
