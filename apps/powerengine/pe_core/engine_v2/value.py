"""Engine v2, layer 3: the value of stored energy (docs/plans/engine-v2.md section 6; build plan, package A).

Stochastic dynamic programming over the battery level, backwards over the forecast's segments: for every segment and
every level, what one more stored kWh is worth (`lam`, pence per kWh). From that curve come the live comparison
(`lines`), the expected timeline and the level path.

Model, in short (all prices in pence per kWh, energy in kWh stored in the battery):

* `V_k(e)` is the expected cost from segment k on, starting at level e. `V_end(e) = -terminal_value * e`.
* A smart slot is two price outcomes, known before the choice, so the minimum is taken inside each outcome and the
  outcomes are weighted by `slot_prob`. A forced mode (grid event, free power, an override) is the only choice.
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
from datetime import datetime, timezone

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
                 "wear_h", "wear_s", "ccost", "lo", "hi", "allowed", "forced", "outcomes", "scen", "mid")


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
    S.lo, S.hi = settings.comfort_low_soc / 100 * S.cap, settings.comfort_high_soc / 100 * S.cap
    S.forced = lim.forced or (EVENT if seg.event else FREE if seg.free else seg.manual)
    S.allowed = tuple(m for m in _ORDER if m in lim.allowed) or (SELF_USE,)
    scen = []
    for p, so, ho in _groups(seg.solar_kwh, seg.load_kwh):
        scen.append((p, so, ho, ho + S.car - so))
    S.scen = scen
    S.mid = (seg.solar_kwh.mid, seg.load_kwh.mid)
    if seg.slot_prob is not None and seg.slot_import_p is not None and 0 < seg.slot_prob < 1 and not S.forced:
        S.outcomes = [(seg.slot_prob, seg.slot_import_p), (1 - seg.slot_prob, seg.import_p)]
    elif seg.slot_prob is not None and seg.slot_import_p is not None and seg.slot_prob >= 1:
        S.outcomes = [(1.0, seg.slot_import_p)]
    elif seg.slot_prob is not None and seg.slot_import_p is not None and seg.slot_prob > 0:   # forced: one price
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
    battery sold, grid to battery), energies in kWh, cost including wear and comfort."""
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
    comfort = 0.0
    if S.ccost:
        hi, lo = S.hi, S.lo
        ex0 = (e - hi if e > hi else 0.0) + (lo - e if e < lo else 0.0)
        ex1 = (end - hi if end > hi else 0.0) + (lo - end if end < lo else 0.0)
        comfort = S.ccost * dt * 0.5 * (ex0 + ex1)
    return (end, cash + comfort, comfort, imp, exp, evx, house, sold, gtb)


def _mix(a: tuple, b: tuple, f: float) -> tuple:
    """f of the segment in `a` (a charge or a sale), the rest in `b` (hold)."""
    g = 1.0 - f
    return tuple(f * x + g * y for x, y in zip(a, b, strict=True))


# --- the choice in one segment -------------------------------------------------------------------------------------
def _partial(scen: list, full: list, hold: list, Vn: _Arr) -> tuple[float, float] | None:
    """The best stop inside a charge (or a sale): the share f of the segment (0 < f < 1) spent on the mode, the rest
    held. The expected cost is convex in f, so the smallest f whose slope reaches zero is found by bisection, then
    snapped to the grid levels either side of it. Returns (expected cost-to-go, f) or None when full or none is best."""
    n = len(scen)
    ef = [x[0] for x in full]
    eh = [x[0] for x in hold]
    d_e = [ef[i] - eh[i] for i in range(n)]
    if max(abs(d) for d in d_e) < 1e-9:
        return None
    d_c = [full[i][1] - hold[i][1] for i in range(n)]
    ps = [s[0] for s in scen]

    def slope(f: float) -> float:
        t = 0.0
        for i in range(n):
            t += ps[i] * (d_c[i] + _slope(Vn, eh[i] + f * d_e[i]) * d_e[i])
        return t

    def cost(f: float) -> float:
        t = 0.0
        for i in range(n):
            t += ps[i] * (hold[i][1] + f * d_c[i] + _val(Vn, eh[i] + f * d_e[i]))
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


def _best(S: _Seg, e: float, scen: list, imp_p: float, Vn: _Arr) -> tuple[float, str, float]:
    """(expected cost-to-go, mode, f): the cheapest choice at level e for one price outcome. f is the share of the
    segment the mode runs (1 for everything but a charge or sale that stops part-way)."""
    ckw, dkw = _chg_kw(S, e), _dis_kw(S, e)
    forced = S.forced
    if forced:
        g = 0.0
        for p, _so, _ho, net in scen:
            r = _phys(S, e, forced, net, imp_p, ckw, dkw)
            g += p * (r[1] + _val(Vn, r[0]))
        return g, forced, 1.0
    best_g, best_m, best_f = math.inf, SELF_USE, 1.0
    hold = None
    for mode in S.allowed:
        res = [_phys(S, e, mode, s[3], imp_p, ckw, dkw) for s in scen]
        if mode == HOLD:
            hold = res
        elif mode in (CHARGE, EXPORT):
            if hold is None:
                hold = [_phys(S, e, HOLD, s[3], imp_p) for s in scen]
            part = _partial(scen, res, hold, Vn)
            if part is not None and part[0] < best_g - EPS:
                best_g, best_m, best_f = part[0], mode, part[1]
        g = 0.0
        for i in range(len(scen)):
            r = res[i]
            g += scen[i][0] * (r[1] + _val(Vn, r[0]))
        if g < best_g - EPS:
            best_g, best_m, best_f = g, mode, 1.0
    return best_g, best_m, best_f


# --- the solve -----------------------------------------------------------------------------------------------------
class _Core:
    __slots__ = ("segs", "V", "tv", "cap", "step", "fine_step")


def _terminal_p(fc: Forecast, facts: BatteryFacts, settings: V2Settings) -> float:
    if settings.terminal_value == "fixed":
        return settings.terminal_value_p
    segs = fc.segments
    cut = segs[-1].end.timestamp() - 24 * 3600
    prices = [s.import_p for s in segs if not s.free and s.end.timestamp() > cut]
    prices = prices or [s.import_p for s in segs if not s.free]
    return min(prices) / facts.eta_charge if prices else settings.terminal_value_p


def _backward(fc: Forecast, facts: BatteryFacts, settings: V2Settings, limits_for, now: datetime,
              fine: float, coarse: float) -> _Core:
    cap = facts.capacity_kwh
    core = _Core()
    core.cap = cap
    core.segs = [_make(s, facts, settings, limits_for(s)) for s in fc.segments]
    core.tv = _terminal_p(fc, facts, settings)
    nf, sf = _grid(cap, fine)
    nc, sc = _grid(cap, max(coarse, fine))
    core.step, core.fine_step = sf, sf
    tail = _Arr([-core.tv * i * sc for i in range(nc + 1)], sc)
    V: list[_Arr] = [tail] * (len(core.segs) + 1)
    horizon = FINE_HOURS * 3600
    for k in range(len(core.segs) - 1, -1, -1):
        S = core.segs[k]
        fine_here = (S.seg.start - now).total_seconds() < horizon
        n, step = (nf, sf) if fine_here else (nc, sc)
        Vn = V[k + 1]
        row = []
        outcomes, scen = S.outcomes, S.scen
        for i in range(n + 1):
            e = i * step
            if len(outcomes) == 1:
                row.append(_best(S, e, scen, outcomes[0][1], Vn)[0])
            else:
                row.append(sum(pw * _best(S, e, scen, ip, Vn)[0] for pw, ip in outcomes))
        V[k] = _Arr(row, step)
    core.V = V
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
    if CHARGE in S.allowed and e < S.ceil - 1e-9 and value > buy + (-bc if prev == CHARGE else bc):
        return CHARGE, min(S.ceil, _cross(row, step, e, buy - bc, True))
    if EXPORT in S.allowed and e > S.floor + 1e-9 and value < sell + (bd if prev == EXPORT else -bd):
        return EXPORT, max(S.floor, _cross(row, step, e, sell + bd, False))
    if net > 0:
        edge = use + (bd if prev == SELF_USE else -bd if prev == HOLD else 0.0)
        want = SELF_USE if value < edge else HOLD
    else:
        edge = store + (-bc if prev == SELF_USE else bc if prev == HOLD else 0.0)
        want = SELF_USE if value > edge else HOLD
    if want in S.allowed:
        return want, None
    return (S.allowed[0] if S.allowed else SELF_USE), None


def _forward(core: _Core, lam: tuple, step: float, e0: float, kind: str, band: float) -> tuple[list[dict], float]:
    """Run forward from level e0 kWh. kind: "mid" (middle sun, middle house), "low" / "high" (the low / high net-load
    group), "self" (Self-use wherever nothing is forced). The mode in each segment is what layer 5 would choose from
    the value curve at that level and those prices (`_policy`); a charge or sale stops where the curve says. Returns
    (records, cost including the credit for the energy left at the end)."""
    recs, e, total, prev = [], e0, 0.0, None
    for k, S in enumerate(core.segs):
        if kind == "mid":
            so, ho = S.mid
            net = ho + S.car - so
        else:
            _, so, ho, net = S.scen[-1] if kind == "high" else S.scen[0]
        imp_p = _outcome_price(S)
        if kind == "self":
            mode, stop = S.forced or SELF_USE, None
        else:
            mode, stop = _policy(S, _end_row(lam, k), step, e, imp_p, net, prev, band)
        full = _phys(S, e, mode, net, imp_p)
        res, e_hold, f = full, e, 1.0
        if stop is not None:
            hold = _phys(S, e, HOLD, net, imp_p)
            span = full[0] - hold[0]
            f = min(1.0, max(0.0, (stop - hold[0]) / span)) if abs(span) > 1e-9 else 1.0
            if f < 1.0:
                res, e_hold = _mix(full, hold, f), hold[0]
        prev = HOLD if f < 1.0 else mode
        recs.append({"k": k, "mode": mode, "f": f, "e0": e, "e1": res[0], "e_full": full[0], "e_hold": e_hold,
                     "cost": res[1], "comfort": res[2], "imp": res[3], "exp": res[4], "evx": res[5],
                     "house": res[6], "sold": res[7], "gtb": res[8], "imp_p": imp_p})
        total += res[1]
        e = res[0]
    return recs, total - core.tv * e


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
          now: datetime, because: str, tz=None) -> ValueResult:
    """Work out the value curve for the forecast, then the expected timeline from `start_soc` (percent). `limits_for`
    gives each segment's allowed modes, floors and caps (layer 4)."""
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
    lam = tuple(_fine_lam(core.V[k], n_fine, step) for k in range(len(core.segs)))
    e0 = min(cap, max(0.0, start_soc / 100 * cap))
    mid, cost_mid = _forward(core, lam, step, e0, "mid", settings.price_band_p)
    low, _ = _forward(core, lam, step, e0, "low", settings.price_band_p)
    high, _ = _forward(core, lam, step, e0, "high", settings.price_band_p)
    _, cost_self = _forward(core, lam, step, e0, "self", settings.price_band_p)
    given_up = None
    if settings.comfort_cost_p > 0:
        plain = replace(settings, comfort_cost_p=0.0)
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
                       calc_s=time.perf_counter() - t_start)


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


def lines(vr: ValueResult, t: datetime, soc: float, import_p: float, export_p: float, facts: BatteryFacts,
          settings: V2Settings) -> Lines:
    """The live comparison in value terms. `charge_target_soc` is where, going up from `soc`, a stored kWh stops being
    worth more than the import price after charging losses (None when it is not above it at `soc`, ties included);
    `sell_floor_soc` is where, going down from `soc`, a stored kWh stops being worth less than a sale brings."""
    ec, ed = facts.eta_charge, facts.eta_discharge
    buy, sell = import_p / ec, export_p * ed - settings.wear_sale_p
    use, store = import_p * ed - settings.wear_house_p, export_p / ec
    value = value_at(vr, t, soc)
    target = floor = None
    if vr.lam:
        row = _end_row(vr.lam, _segment_index(vr, t))
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
            res = _mix(res, hold, f)
    flows = {"import_kwh": res[3], "export_kwh": res[4], "event_export_kwh": res[5], "battery_to_house_kwh": res[6],
             "battery_sold_kwh": res[7], "grid_to_battery_kwh": res[8], "comfort_p": res[2],
             "cash_p": res[1] - res[2], "share_of_segment": f, "hours": S.dt}
    return res[0], res[1], flows
