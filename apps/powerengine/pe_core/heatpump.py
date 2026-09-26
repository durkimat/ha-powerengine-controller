"""Heat pump model for the Simulator (#54 phase 3).

Heat demand each hour = HLC x max(0, 15.5 C - outside temperature) (space heating, with 15.5 C as the usual
base that allows for heat from people and appliances) plus a daily hot-water load. Electricity = heat / COP, with
COP a straight line between two points (e.g. 2.5 at -3 C and 4.5 at 12 C), lower for hot water (hotter flow).

HLC (the house's heat loss, kW per K) comes from a year of gas use (gas kWh x boiler efficiency = heat, less hot
water) over the year's degree-hours, or from a heat-loss survey figure (kW at -3 C, 21 C inside).

Flexibility: hot water is heated in the day's cheapest half-hours; space heating can be brought forward up to
`preheat_h` hours (pre-heating the house), paying a small loss for each hour it's moved.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

BASE_C = 15.5
DESIGN_OUT_C, DESIGN_IN_C = -3.0, 21.0
PREHEAT_LOSS_PER_H = 0.05          # 5% more heat for each hour heat is delivered early
HW_COP_DROP = 0.8                  # hot water at ~50 C is less efficient than space heating


@dataclass
class HeatPumpSettings:
    enabled: bool = False
    gas_kwh_year: float = 0.0            # annual gas use (for heat-loss calibration and the gas comparison)
    boiler_efficiency: float = 85.0      # %
    heat_loss_kw: float = 0.0            # survey figure at -3 C (overrides the gas calibration when > 0)
    hot_water_kwh_day: float = 6.0       # heat for hot water per day
    tank_litres: float = 200.0
    cop_cold: float = 2.5                # at -3 C
    cop_mild: float = 4.5                # at 12 C
    max_kw: float = 8.0                  # the pump's maximum heat output (kW)
    preheat_h: float = 2.0
    gas_price_p: float = 6.0             # p/kWh, for the "keep gas" comparison
    gas_standing_p: float = 30.0         # p/day
    install_cost: float = 0.0            # GBP after grants, for payback

    @classmethod
    def from_dict(cls, d: dict | None) -> HeatPumpSettings:
        s = cls()
        for k, v in (d or {}).items():
            if not hasattr(s, k):
                continue
            if k == "enabled":
                s.enabled = bool(v)
                continue
            try:
                setattr(s, k, float(v))
            except (TypeError, ValueError):
                continue
        return s

    def as_dict(self) -> dict:
        return asdict(self)

    def problems(self) -> list[str]:
        out = []
        if self.enabled and self.gas_kwh_year <= 0 and self.heat_loss_kw <= 0:
            out.append("enter a year of gas use or a heat-loss figure")
        if not 50 <= self.boiler_efficiency <= 100:
            out.append("boiler efficiency must be 50-100%")
        if not (1.0 <= self.cop_cold <= 7.0 and 1.0 <= self.cop_mild <= 8.0):
            out.append("COP figures must be 1-8")
        if not 0 <= self.preheat_h <= 6:
            out.append("pre-heating must be 0-6 hours")
        return out


def cop(t_out: float, s: HeatPumpSettings, hot_water: bool = False) -> float:
    slope = (s.cop_mild - s.cop_cold) / (12.0 - DESIGN_OUT_C)
    c = s.cop_cold + slope * (t_out - DESIGN_OUT_C)
    if hot_water:
        c -= HW_COP_DROP
    return max(1.3, min(6.5, c))


def degree_hours(temps: list[float]) -> float:
    return sum(max(0.0, BASE_C - t) for t in temps)


def hlc_kw_per_k(s: HeatPumpSettings, year_temps: list[float]) -> float | None:
    """The house's heat loss in kW per degree, from the survey figure or a year of gas use."""
    if s.heat_loss_kw > 0:
        return s.heat_loss_kw / (DESIGN_IN_C - DESIGN_OUT_C)
    dh = degree_hours(year_temps)
    if s.gas_kwh_year <= 0 or dh <= 0 or len(year_temps) < 24 * 300:
        return None
    heat = s.gas_kwh_year * s.boiler_efficiency / 100 - s.hot_water_kwh_day * 365
    return max(0.0, heat) / dh


def day_heat(hourly_temps: list[float], hlc: float, s: HeatPumpSettings) -> tuple[list[float], float]:
    """(space-heat kWh for each half-hour (48), hot-water heat kWh for the day)."""
    out = []
    for t in hourly_temps[:24]:
        h = hlc * max(0.0, BASE_C - t)
        out += [h / 2, h / 2]
    while len(out) < 48:
        out.append(out[-1] if out else 0.0)
    return out, s.hot_water_kwh_day


def schedule(prices: list[float | None], temps_hh: list[float], space: list[float], hot_water: float,
             s: HeatPumpSettings) -> tuple[list[float], float]:
    """Electricity (kWh) per half-hour for the day, and the heat delivered (kWh).

    Hot water goes into the cheapest half-hours; each half-hour's space heat goes into the cheapest half-hour from
    `preheat_h` hours before it up to itself, allowing for the loss of heating early. Limited by the pump's size."""
    n = len(space)
    cap = s.max_kw * 0.5                       # heat per half-hour
    used = [0.0] * n                           # heat already scheduled per half-hour
    elec = [0.0] * n
    price = [p if p is not None else 9.99 for p in prices[:n]] + [9.99] * max(0, n - len(prices))

    def put(i: int, heat: float, hw: bool) -> float:
        room = max(0.0, cap - used[i])
        take = min(room, heat)
        used[i] += take
        elec[i] += take / cop(temps_hh[i], s, hw)
        return heat - take

    left = hot_water
    for i in sorted(range(n), key=lambda k: (price[k] / cop(temps_hh[k], s, True), k)):
        if left <= 1e-6:
            break
        left = put(i, left, True)
    back = int(round(s.preheat_h * 2))
    for j in range(n):
        need = space[j]
        if need <= 0:
            continue
        cands = sorted(range(max(0, j - back), j + 1),
                       key=lambda i: (price[i] * (1 + PREHEAT_LOSS_PER_H * (j - i) / 2) / cop(temps_hh[i], s), -i))
        for i in cands:
            if need <= 1e-6:
                break
            extra = 1 + PREHEAT_LOSS_PER_H * (j - i) / 2
            before = need
            need = put(i, need * extra, False) / extra
            if need >= before - 1e-9:
                continue
        if need > 1e-6:                         # pump too small: the rest would need a backup heater (COP 1)
            elec[j] += need
    heat = sum(space) + hot_water
    return elec, heat
