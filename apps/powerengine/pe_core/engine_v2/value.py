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
   under a second, so `FINE_HOURS` is 48 (no coarse part); the coarse grid stays as a lever if a slower machine
   needs it (set `FINE_HOURS` to 12);
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

# A smooth cost on where the battery sits (docs/plans/engine-v2-level-penalty.md; the settings `level_top_*`,
# `level_bot_*` and `slot_bot_*`): a rate in pence per kWh per hour for the energy in each percent-layer beyond a
# start level, doubling every so many points. The top curve starts at `level_top_start_soc` and reaches
# `level_top_rate_p` at 100%; the bottom one starts at `level_bot_start_soc` and reaches `level_bot_rate_p` at
# `level_bot_anchor_soc`. A rate of 0 switches a curve off (both off: the plan is exactly as without them). It is a
# cost of the level itself, so it enters the value curve and the live lines with no rule. Inside a smart slot the
# bottom rate is multiplied by `slot_bot_factor` and the curve may start higher (`slot_bot_start_soc`).
LEVEL_STEP_KWH = 0.1            # the battery-level grid the values are worked out on (kWh)
TERMINAL_FALLBACK_P = 10.0      # the value of a kWh left at the end when the last day has no usable price (p)
TERMINAL_FIXED_P = None         # tests and the simulator: a fixed value for the energy left at the end (None: refill)
PEN_RES = 100                 # table entries per kWh
CONVEX_COST_TO_GO = True      # the cost-to-go of every segment is made convex in the level, from the floor up


# --- the level penalty ---------------------------------------------------------------------------------------------
class _Pen:
    """Tables over the battery level (kWh, `res` entries per kWh): `phi[i]` the cost in pence per hour of holding that
    level, `dphi[i]` its slope in pence per kWh per hour (the rate of the layer at that level: positive above the top
    start, negative below the bottom start), and the top curve's start."""
    __slots__ = ("phi", "dphi", "res", "top_start_kwh", "bot_start_kwh")


_pen_cache: dict = {}


def _layer(x: float, a: float, d: float) -> tuple[float, float]:
    """(rate, cumulative rate) of a curve `x` points past its start: rate a*(2^(x/d)-1), and its integral over them."""
    k = math.log(2.0) / d
    grow = 2.0 ** (x / d) - 1.0
    return a * grow, a * (grow / k - x)


def _level_penalty(cap: float, slot: bool, s: V2Settings) -> _Pen | None:
    """The tables for a battery of `cap` kWh under settings `s`; `slot` asks for the one used inside a smart slot."""
    top_start, top_rate, top_d = s.level_top_start_soc, s.level_top_rate_p, s.level_top_doubling_pts
    anchor = max(s.reserve_soc, 0.0)                      # the bottom curve's rate is set at your reserve
    bot_start, bot_rate, bot_d = s.level_bot_start_soc, s.level_bot_rate_p, s.level_bot_doubling_pts
    if slot:
        bot_rate *= s.slot_bot_factor
        bot_start = s.slot_bot_start_soc or bot_start
    top_on = top_rate > 0.0 and top_start < 100.0
    bot_on = bot_rate > 0.0 and bot_start > anchor
    if not (top_on or bot_on):
        return None
    key = (cap, top_start, top_rate, top_d, bot_start, bot_rate, anchor, bot_d)
    hit = _pen_cache.get(key)
    if hit is not None:
        return hit
    span_top = (100.0 - top_start) / top_d
    span_bot = (bot_start - anchor) / bot_d
    a_top = top_rate / (2.0**span_top - 1.0) if top_on else 0.0
    a_bot = bot_rate / (2.0**span_bot - 1.0) if bot_on else 0.0
    n = int(round(cap * PEN_RES))
    phi, rate = [], []
    per_point = cap / 100.0
    for i in range(n + 2):
        pct = i / PEN_RES / cap * 100.0
        r = c = 0.0
        if top_on and pct > top_start:
            r1, c1 = _layer(pct - top_start, a_top, top_d)
            r, c = r + r1, c + c1
        if bot_on and pct < bot_start:
            r1, c1 = _layer(bot_start - pct, a_bot, bot_d)
            r, c = r - r1, c + c1                              # more energy lowers the bottom penalty: a negative slope
        rate.append(r)
        phi.append(c * per_point)
    pen = _Pen()
    pen.phi, pen.dphi, pen.res = phi, rate, PEN_RES
    pen.top_start_kwh = top_start / 100.0 * cap if top_on else cap
    pen.bot_start_kwh = bot_start / 100.0 * cap if bot_on else 0.0
    if len(_pen_cache) > 8:
        _pen_cache.clear()
    _pen_cache[key] = pen
    return pen


def _interp(t: list[float], x: float) -> float:
    """`t` at the fractional index `x`, straight between the two entries around it (the nearest entry made the value
    of stored power step at every table line, and the plan with it)."""
    if x <= 0.0:
        return t[0]
    i = int(x)
    if i + 1 >= len(t):
        return t[-1]
    return t[i] + (x - i) * (t[i + 1] - t[i])


def _phi(P: _Pen, e: float) -> float:
    return _interp(P.phi, e * P.res)


def _dphi(P: _Pen, e: float) -> float:
    return _interp(P.dphi, e * P.res)


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
    percent (the floors, the ceiling, the range's edges) falls exactly on a level: a kink between two levels
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
                 "wear_h", "wear_s", "sw", "allowed", "forced", "outcomes", "scen",
                 "mid", "pen", "fcap", "fbot", "leg")


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


def leg_band(settings: V2Settings) -> tuple[float, float, float] | None:
    """(bottom, top, shortest run) in percent: the everyday range and the shortest charge or sale (`min_run_pct`), or
    None where there is no shortest run. A charge or a sale runs that far, or as far as the range's edge where that is
    nearer: a charge that starts at 85% going up with a 90% top needs only to reach 90%. Plan and executor use it
    alike."""
    lo, hi, run = settings.range_low_soc, settings.range_high_soc, settings.min_run_pct
    if run > 0 and hi > lo:
        return lo, hi, run
    return None


def leg_need(band: tuple[float, float, float], mode: str, start: float) -> float:
    """How far (the same unit as `band` and `start`) a charge or a sale that begins at `start` must run before it can
    stop or turn round: the shortest run, cut short by the range's edge in its direction."""
    bot, top, run = band
    room = top - start if mode == CHARGE else start - bot
    return min(run, max(room, 0.0))


def _make(seg: Segment, facts: BatteryFacts, settings: V2Settings, lim: Limits) -> _Seg:
    S = _Seg()
    S.seg, S.lim = seg, lim
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
    S.sw = settings.switch_cost_p
    S.pen = _level_penalty(S.cap, seg.slot_prob is not None, settings)
    band = leg_band(settings)
    S.leg = None if band is None else tuple(x / 100 * S.cap for x in band)     # (bottom, top, shortest run) in kWh
    S.fcap = S.pen.top_start_kwh if S.pen is not None else S.cap     # where charging early stops being free (`_choose`)
    S.fbot = S.pen.bot_start_kwh if S.pen is not None else 0.0       # ... and selling early
    S.forced = lim.forced or (EVENT if seg.event else FREE if seg.free and not lim.allowed else seg.manual)
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
    the house needs energy). Returns (end kWh, cost p, level cost p, import, export, event export, battery to house,
    battery sold, grid to battery, level penalty p), energies in kWh, cost including wear and the level cost (the
    range's curves, `_level_penalty`: not money). The level cost is returned alone as the last element too, because a
    part-way charge has to price it from the level it reaches."""
    dt, cap = S.dt, S.cap
    imp = exp = evx = house = sold = gtb = 0.0
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
    pen = _pen_partial(S.pen, dt, e, end, 1.0) if S.pen is not None else 0.0
    return (end, cash + pen, pen, imp, exp, evx, house, sold, gtb, pen)


def _mix(a: tuple, b: tuple, f: float, S: _Seg | None = None) -> tuple:
    """f of the segment in `a` (a charge or a sale), the rest in `b` (idle). With `S`, the level penalty is priced from
    the level the part-way step reaches, not mixed."""
    g = 1.0 - f
    out = tuple(f * x + g * y for x, y in zip(a, b, strict=True))
    if S is not None and S.pen is not None:
        pen = _pen_partial(S.pen, S.dt, b[0], out[0], f)
        out = out[:1] + (out[1] - out[9] + pen, out[2] - out[9] + pen) + out[3:9] + (pen,)
    return out


# --- the choice in one segment -------------------------------------------------------------------------------------
def _partial(scen: list, full: list, hold: list, Vn: _Arr, S: _Seg | None = None) -> tuple[float, float] | None:
    """The best stop inside a charge (or a sale): the share f of the segment (0 < f < 1) spent on the mode, the rest
    in the idle mode (`hold` holds its flows). The expected cost is convex in f, so the smallest f whose slope reaches
    zero is found by bisection, then snapped to the grid levels either side of it. Returns (expected cost-to-go, f) or
    None when full or none is best. The cost of a part-way step is the idle cost plus f of the difference, except the
    level penalty, which is priced from the level reached."""
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
            d_c[i] -= full[i][9] - hold[i][9]
            base[i] -= hold[i][9]

    def pen_f(i: int, f: float) -> float:
        return _pen_partial(pen, S.dt, eh[i], eh[i] + f * d_e[i], f) if pen is not None else 0.0

    def dpen_f(i: int, f: float) -> float:
        return _dpen_partial(pen, S.dt, eh[i], d_e[i], f) if pen is not None else 0.0

    def slope(f: float) -> float:
        t = 0.0
        for i in range(n):
            t += ps[i] * (d_c[i] + _slope(Vn, eh[i] + f * d_e[i]) * d_e[i] + dpen_f(i, f))
        return t

    def cost(f: float) -> float:
        t = 0.0
        for i in range(n):
            t += ps[i] * (base[i] + f * d_c[i] + pen_f(i, f) + _val(Vn, eh[i] + f * d_e[i]))
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


def _idle(S: _Seg) -> str:
    """What the battery does for the rest of a segment once a charge or a sale stops part-way: Hold where it is
    allowed, else Self-use. The executor does the same, so the plan never counts on a mode the rules forbid."""
    return HOLD if HOLD in S.allowed else SELF_USE


def _candidates(S: _Seg, e: float, scen: list, imp_p: float, Vk: dict) -> list[tuple[float, str, float]]:
    """Every choice in one segment at level e for one price outcome, as (expected cost-to-go, mode, f), before the
    cost of changing mode. f is the share of the segment the mode runs (1 for everything but a charge or sale that
    stops part-way). The cost-to-go of a mode is read from the next segment's curve for the kind of that mode."""
    ckw, dkw = _chg_kw(S, e), _dis_kw(S, e)
    forced = S.forced
    if forced:
        Vn = Vk[KIND[forced]]
        g = 0.0
        for p, _so, _ho, net in scen:
            r = _phys(S, e, forced, net, imp_p, ckw, dkw)
            g += p * (r[1] + _val(Vn, r[0]))
        return [(g, forced, 1.0)]
    out: list[tuple[float, str, float]] = []
    hold = None
    idle = _idle(S)
    for mode in S.allowed:
        Vn = Vk[KIND[mode]]
        res = [_phys(S, e, mode, s[3], imp_p, ckw, dkw) for s in scen]
        if mode == idle:
            hold = res
        elif mode in (CHARGE, EXPORT):
            if hold is None:
                hold = [_phys(S, e, idle, s[3], imp_p) for s in scen]
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
    if TERMINAL_FIXED_P is not None:
        return TERMINAL_FIXED_P
    segs = fc.segments
    cut = segs[-1].end.timestamp() - 24 * 3600
    prices = [s.import_p for s in segs if not s.free and s.end.timestamp() > cut]
    prices = prices or [s.import_p for s in segs if not s.free]
    if not prices:
        return TERMINAL_FALLBACK_P
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
    if TERMINAL_FIXED_P is not None or not segs:
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


def _backward(fc: Forecast, facts: BatteryFacts, settings: V2Settings, limits_for, now: datetime,
              fine: float, coarse: float) -> _Core:
    """The value curves, backwards. The state is the level and the kind of the mode before (so a change of mode costs
    `switch_cost_p`): `core.VK[k][kind]` is the cost-to-go from segment k's start having come from that kind, and
    `core.V[k]` the one for self-use, which is what `lam` (the value of a stored kWh) is read from."""
    cap = facts.capacity_kwh
    core = _Core()
    core.cap = cap
    core.segs = [_make(s, facts, settings, limits_for(s)) for s in fc.segments]
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
        outcomes, scen = S.outcomes, S.scen
        for i in range(n + 1):
            e = i * step
            tot = dict.fromkeys(KINDS, 0.0)
            for pw, ip in outcomes:
                cands = _candidates(S, e, scen, ip, Vk)
                for kind in KINDS:
                    tot[kind] += pw * _pick(cands, kind, cost_p)
            for kind in KINDS:
                rows[kind].append(tot[kind])
        if CONVEX_COST_TO_GO:
            lo = min(n, max(0, math.ceil(S.floor / step - 1e-9)))
            rows = {kind: _convex_from(rows[kind], lo) for kind in KINDS}
        VK[k] = {kind: _Arr(rows[kind], step) for kind in KINDS}
    core.VK = VK
    core.V = [d[NONE_K] for d in VK]
    return core


def _convex_from(v: list[float], lo: int) -> list[float]:
    """The greatest convex function at or below the points from index `lo` up (the lower hull); the levels under `lo`,
    which can't be reached, are left as they are. A segment is all one mode, or a mode and a rest in Hold, so the cost
    of the energy left in the battery can have gaps where no mix of modes reaches a level; each gap is a concave kink,
    and the segment before copies it back, so a comb builds up and shows as the value of a stored kWh going up and
    down. Time-sharing between modes over the half-hour would fill the gap with a straight line: the hull is that."""
    hull: list[int] = []
    for i in range(lo, len(v)):
        while len(hull) >= 2:
            a, b = hull[-2], hull[-1]
            if (v[b] - v[a]) * (i - a) >= (v[i] - v[a]) * (b - a):      # b is on or above the chord a-i
                hull.pop()
            else:
                break
        hull.append(i)
    out = list(v)
    for a, b in zip(hull, hull[1:], strict=False):
        for i in range(a + 1, b):
            out[i] = v[a] + (v[b] - v[a]) * (i - a) / (b - a)
    return out


def _lam_row(a: _Arr) -> list[float]:
    """The value of one more stored kWh at each level: the slope of the cost-to-go to the next level up (the last level
    takes the slope below it). A one-sided slope, not a central one, because the floors, the ceiling and the range's
    edges are kinks that fall on levels: a central difference would give half the value at the floor, which is
    exactly where a battery at its reserve sits."""
    v, st = a.v, a.step
    out = [(v[i] - v[i + 1]) / st for i in range(a.n)]
    out.append(out[-1])
    return out


def _fine_lam(a: _Arr, n: int, step: float) -> tuple[float, ...]:
    row = _lam_row(a)
    if a.n == n:
        return tuple(row)
    lam = _Arr(row, a.step)
    return tuple(_val(lam, i * step) for i in range(n + 1))


# --- forward runs --------------------------------------------------------------------------------------------------
def _outcome_price(S: _Seg) -> float:
    return max(S.outcomes, key=lambda o: o[0])[1]


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


def _net(S: _Seg, kind: str) -> float:
    """House plus car minus sun (kWh) in the segment for the walk's scenario."""
    if kind == "mid":
        so, ho = S.mid
        return ho + S.car - so
    return (S.scen[-1] if kind == "high" else S.scen[0])[3]


def _part(S: _Seg, a: float, b: float) -> _Seg:
    """The share a to b (0 to 1) of segment S's time, as a segment of its own: the energies scaled, the powers not."""
    P = _Seg()
    for name in _Seg.__slots__:
        setattr(P, name, getattr(S, name))
    f = b - a
    P.dt, P.car = S.dt * f, S.car * f
    P.scen = [(p, so * f, ho * f, net * f) for p, so, ho, net in S.scen]
    P.mid = (S.mid[0] * f, S.mid[1] * f)
    return P


def _runs(sub: list[dict]) -> list[tuple[str, float]]:
    """The charges and sales of a walk in order, as (mode, the level each ends at). Idle time is left out: pieces of one
    mode with only idle time between them are one run (it goes on, and the idle time comes after it)."""
    out: list[list] = []
    for r in sub:
        for p in r["pieces"]:
            if p[0] in (CHARGE, EXPORT):
                if out and out[-1][0] == p[0]:
                    out[-1][1] = p[4]
                else:
                    out.append([p[0], p[4]])
    return [(m, e) for m, e in out]


def _choose(stretch: list[_Seg], vks: list, e: float, kind: str, prev: str | None,
            leg: tuple[str, float, float] | None, front: bool, done: str | None = None,
            ) -> tuple[str, float | None]:
    """THE decision: what to do from now at level e (kWh) in `stretch[0]`, coming from the mode `prev` with the charge
    or sale `leg` running: (mode, the level a charge or a sale runs to, else None). The expected timeline is this
    decision stepped forward over the forecast (`_walk`); the executor makes the same call from the live level and the
    mode actually running (`decide`). Nothing else chooses a mode.

    `stretch` is the segment and the rest of its stretch (the run of one import price, `_stretch_ends`); `vks[j]` the
    cost-to-go curves at the end of `stretch[j]`. A charge or a sale runs to a level, not for a time: the programme's
    own choice, segment by segment over the rest of the stretch (a walk without these rules), says which runs it makes
    at today's prices and where each ends, and the decision is the first of them, now:

    * a run the programme starts now goes to where its run ends, however many segments that takes;
    * where the programme would idle now and start a run later in the stretch, that run starts now and the idle time
      comes after it, as far as the range's edge (above the top or below the bottom holding costs by the hour, so a
      last fill before a dear stretch keeps the programme's own timing). The forecast moves (smart slots come and go),
      so what pays at today's prices is done while they hold. `done`, the run that has just reached its level,
      starts again only to go `RESTART_PCT` further, so it never creeps on in slivers;
    * a charge anywhere in the stretch starts now, at full power, to the level the programme reaches by the stretch's
      end (late is risky: the rate tapers, a slot is withdrawn); only up to the top of the range (where the top
      curve starts), where holding starts to cost by the hour. A sale running now is not
      turned round by it.

    `leg` is (mode, kWh it began at, kWh it must run): a charge or a sale runs at least `leg_need` between the level
    bands before it may stop or turn round. With `front` off this is the programme's own choice for the segment alone
    (a run's level is where it stops inside the segment, None when it runs all of it)."""
    S = stretch[0]
    if kind == "self":
        return S.forced or SELF_USE, None
    net = _net(S, kind)
    imp_p = _outcome_price(S)
    mode = target = None
    if not front or S.forced:
        mode, share = _dp_choice(S, e, imp_p, vks[0], prev)
        if share < 1.0:                             # a charge or sale that stops part-way: where it stops
            lo, hi = _phys(S, e, _idle(S), net, imp_p)[0], _phys(S, e, mode, net, imp_p)[0]
            target = lo + share * (hi - lo)
    else:
        sub = _walk(stretch, e, kind, front=False, prev=prev, vks=vks, leg=leg)
        runs = [r for r in _runs(sub) if r[0] in S.allowed]
        if CHARGE in S.allowed:
            reach = sub[-1]["e1"]
            sale_goes_on = prev == EXPORT and sub[0]["mode"] == EXPORT
            capped = S.pen is not None and reach > S.fcap
            if capped:                              # early is free only inside the band: above its top the hours cost
                reach = S.fcap
            if any(m == CHARGE for m, _ in runs) and reach > e + 1e-9 and not sale_goes_on and done != CHARGE \
                    and e < S.ceil - 1e-9 and not (capped and _phys(S, e, CHARGE, net, imp_p)[0] > reach + 1e-9):
                mode, target = CHARGE, min(reach, S.ceil)
        if mode is None:
            first = sub[0]["pieces"][0][0]
            again = S.cap * RESTART_PCT / 100                       # how far a run that just ended must still go
            nxt = next(((m, t) for m, t in runs
                        if (t > e + 1e-6 if m == CHARGE else t < e - 1e-6)
                        and (m != done or abs(t - e) >= again)),
                       None)
            if nxt is not None and first not in (CHARGE, EXPORT):    # a later run, pulled forward: inside the range
                m, t = nxt
                t = min(t, S.fcap) if m == CHARGE else (max(t, S.fbot) if PULL_CAP_SALES else t)
                if t > e + 1e-6 if m == CHARGE else t < e - 1e-6:
                    mode, target = m, t
                else:
                    mode = first
            elif nxt is not None:                                    # the first run of the stretch, now
                mode, target = nxt
            else:
                mode = first if first not in (CHARGE, EXPORT) else _idle(S)
    if S.leg is not None and not S.forced:
        going = False
        if leg is not None and leg[0] in S.allowed:
            lm, frm, need = leg
            going = (e - frm if lm == CHARGE else frm - e) < need - S.cap * LEVEL_TOL_PCT / 100 \
                and (e < S.ceil - 1e-9 if lm == CHARGE else e > S.floor + 1e-9)
            if going:
                goal = min(frm + need, S.ceil) if lm == CHARGE else max(frm - need, S.floor)
                if mode != lm:
                    mode, target = lm, goal                      # a leg runs its length: no stop, no turn-round
                elif target is not None:
                    target = max(target, goal) if lm == CHARGE else min(target, goal)
        if not going and mode in (CHARGE, EXPORT) and target is not None and (leg is None or leg[0] != mode):
            need = leg_need(S.leg, mode, e)                      # a leg starting now: its length is part of its level
            target = max(target, min(e + need, S.ceil)) if mode == CHARGE else min(target, max(e - need, S.floor))
    return mode, target


def _next_leg(S: _Seg, mode: str, e: float, leg: tuple | None) -> tuple | None:
    """The charge or sale running once `mode` starts at level e (see `_choose`): a new one when a charge or a sale
    begins, none when the mode is anything else (as the executor forgets `leg_from`)."""
    if S.leg is None or S.forced or mode not in (CHARGE, EXPORT):
        return None
    if leg is None or leg[0] != mode:
        return (mode, e, leg_need(S.leg, mode, e))
    return leg


LEVEL_TOL_PCT = 0.1     # points: a run is at its level within this (the executor's own test, `execute.LEVEL_EPS`)
PULL_CAP_SALES = True   # a sale pulled forward stops at the range's bottom too (experiment)
RESTART_PCT = 1.0       # a run that reached its level starts again only to go at least this much further (points)
SLIVER_S = 60           # less than this left of a segment: the executor decides for the next one
MAX_PIECES = 4          # modes in one segment at most (a safety bound: runs go to levels, not to segment ends)


def _walk(segs: list[_Seg], e0: float, kind: str, front: bool = True, prev: str | None = None, first: int = 0,
          vks: list | None = None, leg: tuple[str, float, float] | None = None, used: int = 0,
          last_run: str | None = None) -> list[dict]:
    """Step the decision (`_choose`) forward over `segs` from level e0 kWh, coming from the mode `prev` with the leg
    `leg` running; `vks[i]` is the cost-to-go curves at the end of segment i. kind: "mid" (middle sun, middle house),
    "low" / "high" (the low / high net-load group), "self" (Self-use wherever nothing is forced).

    The decision is made where something changes, not on the clock: a charge or a sale goes on, across segments, until
    it reaches its level or its stretch ends (the price changes, a forced segment begins), and when it reaches its
    level the next decision is made at once, inside the segment. Idle time (self-use or hold) is decided again at
    each segment, where the forecast's sun and house change. `used` and `last_run` are for the first segment: how many
    modes it has already had and the run that has just reached its level (it is not started again). With `front` off
    this is the programme's own choice segment by segment, a stop part-way idling the rest of the segment."""
    recs, e, prev_mode = [], e0, prev
    ends = _stretch_ends(segs) if front else None
    active: tuple | None = None                                 # (mode, level, last segment) of the run going on
    for i, S in enumerate(segs):
        imp_p = _outcome_price(S)
        idle = _idle(S)
        end_i = ends[i] if ends else i
        tail = segs[i + 1:end_i + 1] if front else []
        vst = vks[i:i + 1 + len(tail)]
        pieces: list[tuple] = []
        tot = [0.0] * 10
        cost, x, cur = 0.0, 0.0, S
        n_used, stopped = (used, last_run) if i == 0 else (0, None)
        if active is not None and (i > active[2] or S.forced or active[0] not in S.allowed):
            active = None
        while True:
            if active is not None:
                mode, target = active[0], active[1]
            else:
                mode, target = _choose([cur] + tail, vst, e, kind, prev_mode, leg, front, stopped)
                if stopped is not None and mode == stopped and (target is None
                                                                or abs(target - e) < S.cap * RESTART_PCT / 100):
                    mode, target = idle, None                    # the run reached its level: not started again
                if front and kind != "self" and mode in (CHARGE, EXPORT) and target is not None:
                    active = (mode, target, end_i)
            if kind != "self":
                leg = _next_leg(S, mode, e, leg)
            net = _net(cur, kind)
            f = 1.0
            if target is not None:
                full_e = _phys(cur, e, mode, net, imp_p)[0]
                hold_e = _phys(cur, e, idle, net, imp_p)[0]
                span = full_e - hold_e
                f = min(1.0, max(0.0, (target - hold_e) / span)) if abs(span) > 1e-9 else 1.0
                if (1.0 - f) * cur.dt < 1e-6:
                    f = 1.0
            if f * cur.dt < 1e-6:                                # it is at its level already: nothing runs
                active = None
                if mode in (CHARGE, EXPORT) and mode != stopped and n_used < MAX_PIECES - 1:
                    stopped, n_used = mode, n_used + 1
                    continue
                mode, f = idle, 1.0
            part = cur if f >= 1.0 else _part(cur, 0.0, f)
            r = _phys(part, e, mode, _net(part, kind), imp_p)
            cost += r[1] + (switch_cost(KIND[prev_mode], KIND[mode], S.sw) if prev_mode is not None else 0.0)
            for j in range(2, 10):
                tot[j] += r[j]
            x1 = 1.0 if f >= 1.0 else x + f * (1.0 - x)
            pieces.append((mode, x, x1, e, r[0], False, target))
            e, prev_mode = r[0], mode
            n_used += 1
            if f >= 1.0:
                break
            cur, x = _part(cur, f, 1.0), x1
            active = None                                        # the run reached its level inside the segment
            if mode in (CHARGE, EXPORT) and n_used < MAX_PIECES - 1:
                stopped = mode                                   # decide again for what is left of the segment
                continue
            r = _phys(cur, e, idle, _net(cur, kind), imp_p)       # the rest of the segment idles
            cost += r[1] + (switch_cost(KIND[prev_mode], KIND[idle], S.sw) if prev_mode != idle else 0.0)
            for j in range(2, 10):
                tot[j] += r[j]
            pieces.append((idle, x, 1.0, e, r[0], True, None))
            e, prev_mode, leg = r[0], idle, None
            break
        recs.append({"k": first + i, "mode": pieces[0][0], "pieces": pieces, "e0": pieces[0][3], "e1": e,
                     "cost": cost, "comfort": tot[2], "imp": tot[3], "exp": tot[4], "evx": tot[5], "house": tot[6],
                     "sold": tot[7], "gtb": tot[8], "imp_p": imp_p})
    return recs


def _forward(core: _Core, e0: float, kind: str, prev: str | None = None,
             leg: tuple[str, float, float] | None = None) -> tuple[list[dict], float]:
    """`_walk` over the whole forecast. Returns (records, cost including the credit for the energy left at the end)."""
    recs = _walk(core.segs, e0, kind, prev=prev, vks=[core.VK[k + 1] for k in range(len(core.segs))], leg=leg)
    return recs, sum(r["cost"] for r in recs) + _val(core.tail, recs[-1]["e1"])


def _level_at(rec: dict, x: float) -> float:
    """Battery level (kWh) a fraction x of the way through a segment's record."""
    for _mode, x0, x1, e0, e1, _rem, _target in rec["pieces"]:
        if x <= x1 + 1e-12:
            return e0 + (e1 - e0) * ((x - x0) / (x1 - x0) if x1 > x0 else 1.0)
    return rec["e1"]


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
    # (mode, start, end, e_start, e_end, rec index, by_level, remainder, e_lo, e_hi)
    pieces = []
    for rec in recs:
        seg = core.segs[rec["k"]].seg
        dt = seg.end - seg.start
        for mode, x0, x1, e0, e1, rem, _target in rec["pieces"]:
            lo, hi = e1, e1
            by_level = x1 < 1.0 or rem                            # ended at its stop, or the rest after one
            pieces.append((mode, seg.start + dt * x0, seg.start + dt * x1, e0, e1, rec["k"], by_level, rem, lo, hi))
    merged: list[list] = []
    for p in pieces:
        if (p[2] - p[1]).total_seconds() < 30:
            continue
        if merged and merged[-1][0] == p[0]:
            m = merged[-1]
            m[2], m[4], m[6] = p[2], p[4], p[6]
            m[7], m[8], m[9] = m[7] and p[7], p[8], p[9]            # the rest of a segment only if every part is
            m[10] = p[5]
        else:
            merged.append(list(p) + [p[5]])
    items = []
    for idx, m in enumerate(merged):
        mode, t0, t1, e0, e1, k0, by_level, remainder, e_lo, e_hi, k_last = m
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
                                  reason=_reason(mode, rec, S, value, ahead, by_level, facts),
                                  remainder=remainder))
    return tuple(items)


# --- public --------------------------------------------------------------------------------------------------------
def solve(forecast: Forecast, start_soc: float, facts: BatteryFacts, settings: V2Settings, limits_for,
          now: datetime, because: str, tz=None, running: str | None = None,
          leg_from: float | None = None) -> ValueResult:
    """Work out the value curve for the forecast, then the expected timeline from `start_soc` (percent). `limits_for`
    gives each segment's allowed modes, floors and caps (layer 4). `running` is the mode now (layer 5's): the expected
    timeline starts from it, so its first step is the decision the executor makes from there (`decide`).
    `leg_from` is the level (percent) the charge or sale running now began at: it has its minimum length still to run
    (`leg_need`)."""
    t_start = time.perf_counter()
    if not forecast.segments:
        return ValueResult(made_at=now, because=because, forecast=forecast, step_kwh=facts.capacity_kwh, lam=(),
                           timeline=(), path={"start": now.isoformat(), "step_min": PATH_STEP_MIN, "mid": [],
                                              "low": [], "high": []},
                           cost_expected_p=0.0, cost_selfuse_p=0.0, comfort_given_up_p=None,
                           calc_s=time.perf_counter() - t_start)
    cap = facts.capacity_kwh
    core = _backward(forecast, facts, settings, limits_for, now, LEVEL_STEP_KWH, COARSE_STEP_KWH)
    n_fine, step = _grid(cap, LEVEL_STEP_KWH)
    lam = tuple(_fine_lam(core.V[k], n_fine, step) for k in range(len(core.segs)))
    e0 = min(cap, max(0.0, start_soc / 100 * cap))
    leg, band = None, leg_band(settings)
    if band is not None and running in (CHARGE, EXPORT) and leg_from is not None:
        leg = (running, leg_from / 100 * cap, leg_need(band, running, leg_from) / 100 * cap)
    mid, cost_mid = _forward(core, e0, "mid", running, leg)
    low, _ = _forward(core, e0, "low", running, leg)
    high, _ = _forward(core, e0, "high", running, leg)
    _, cost_self = _forward(core, e0, "self")
    path = _path(core, now, {"mid": mid, "low": low, "high": high})
    timeline = _timeline(core, mid, lam, step, now, tz, facts)
    return ValueResult(made_at=now, because=because, forecast=forecast, step_kwh=step, lam=lam, timeline=timeline,
                       path=path, cost_expected_p=cost_mid, cost_selfuse_p=cost_self, comfort_given_up_p=None,
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
    k = _segment_index(vr, t)
    row = _end_row(vr.lam, k)
    cap = vr.step_kwh * (len(row) - 1)
    return _row_at(row, vr.step_kwh, soc / 100 * cap)


def decide(vr: ValueResult, t: datetime, soc: float, lim: Limits, facts: BatteryFacts, settings: V2Settings,
           prev: str | None, leg_from: float | None = None, used: int = 0, last_run: str | None = None,
           import_p: float | None = None, export_p: float | None = None) -> dict | None:
    """The executor's decision: what the plan's own decision (`_choose`) says for what is left of the segment holding
    `t`, from the live level `soc` (percent), the live limits `lim` and the mode actually running `prev` (with the level
    `leg_from` its charge or sale began at), and the live rates `import_p` / `export_p` (p/kWh) where they differ from
    the forecast's. The expected timeline is the same call stepped forward over the forecast,
    so the two differ only where the inputs do. No new solve: it reuses the cost-to-go curves of `vr`.

    When a charge or a sale has just reached its stop inside the segment, `used` is how many modes the segment has had
    and `last_run` that run (`_walk`): the decision is then the one for what is left of the segment.

    Returns {"mode", "stop", "rest", "until", "end"}: the mode; for a charge or a sale, the level (percent) it runs to;
    the idle mode; until when the decision stands unless something changes first (a run: the end of its stretch, idle
    time: the end of the segment), as a timestamp; the level expected at the segment's end. None when it can't say (no
    curves, `t` outside the forecast)."""
    segs = vr.forecast.segments
    if not vr.lam or len(vr.vk) != len(segs) or len(vr.limits) != len(segs) or not segs \
            or t >= segs[-1].end or t < segs[0].start:
        return None
    k0 = _segment_index(vr, t)
    if k0 + 1 < len(segs) and (segs[k0].end - t).total_seconds() < SLIVER_S:
        k0, t = k0 + 1, segs[k0 + 1].start               # a moment left of the segment: decide for the next one
    sg = segs[k0]
    if t > sg.start:                                    # only what is left of the segment now running
        frac = (sg.end - t).total_seconds() / (sg.end - sg.start).total_seconds()
        sg = replace(sg, start=t, solar_kwh=_scaled(sg.solar_kwh, frac), load_kwh=_scaled(sg.load_kwh, frac))
    if import_p is not None and k0 == _segment_index(vr, t):
        # now is priced at what it costs now: a slot running now is certain (no odds), and a new rate can arrive a
        # moment before the forecast's segment does
        sg = replace(sg, import_p=import_p, export_p=export_p, slot_prob=None, slot_import_p=None)
    cut = [_make(sg, facts, settings, lim)]
    k = k0
    while k + 1 < len(segs):                            # the rest of the stretch: the early charge looks across it
        nxt = _make(segs[k + 1], facts, settings, vr.limits[k + 1])
        if cut[-1].forced or nxt.forced or abs(_outcome_price(cut[-1]) - _outcome_price(nxt)) >= 0.01:
            break
        cut.append(nxt)
        k += 1
    cap = facts.capacity_kwh
    e = min(cap, max(0.0, soc / 100 * cap))
    leg, band = None, leg_band(settings)
    if band is not None and prev in (CHARGE, EXPORT) and leg_from is not None:
        leg = (prev, leg_from / 100 * cap, leg_need(band, prev, leg_from) / 100 * cap)
    vks = [vr.vk[j] for j in range(k0, k + 1)]
    r = _walk(cut, e, "mid", prev=prev, first=k0, vks=vks, leg=leg, used=used, last_run=last_run)[0]
    mode, target = r["pieces"][0][0], r["pieces"][0][6]
    run = mode in (CHARGE, EXPORT)
    until = (cut[-1].seg.end if run else cut[0].seg.end).timestamp()
    return {"mode": mode, "stop": target / cap * 100 if run and target is not None else None, "rest": _idle(cut[0]),
            "until": until, "end": r["e1"] / cap * 100}


def _forced_of(sg: Segment, lim: Limits) -> str | None:
    return lim.forced or (EVENT if sg.event else FREE if sg.free and not lim.allowed else sg.manual)


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

    For the display and the sentences only: the mode is the plan's decision (`decide`), not these lines."""
    ec, ed = facts.eta_charge, facts.eta_discharge
    buy, sell = import_p / ec, export_p * ed - settings.wear_sale_p
    use, store = import_p * ed - settings.wear_house_p, export_p / ec
    value = value_at(vr, t, soc)
    target = floor = None
    if vr.lam:
        k = _segment_index(vr, t)
        row = _end_row(vr.lam, k)
        step = vr.step_kwh
        cap = step * (len(row) - 1)
        e = soc / 100 * cap
        if value > buy + LINE_TOL / ec:
            target = _cross(row, step, e, buy + LINE_TOL / ec, True) / cap * 100
        if value < sell - LINE_TOL:
            floor = _cross(row, step, e, sell - LINE_TOL, False) / cap * 100
    return Lines(value_p=value, buy_line_p=buy, sell_line_p=sell, use_line_p=use, store_sun_line_p=store,
                 import_p=import_p, export_p=export_p, charge_target_soc=target, sell_floor_soc=floor)


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
