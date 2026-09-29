"""Turn mapped inputs into one normalised snapshot of the system.

Everything downstream (status sentence, dashboard entities, later the planner)
works from a Readings object, never from raw HA states. Conventions:

- power in W; battery + = discharging, grid + = importing (after any invert)
- energy in kWh, rates in GBP/kWh, percentages 0-100
- times are timezone-aware datetimes
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .adapters.axle import AxleEvents
from .adapters.kraken import KrakenTariff
from .adapters.myenergi import ZappiCharger
from .config import Config
from .parsing import (  # noqa: F401
    State,
    Window,
    _energy_kwh,
    _is_on,
    _num,
    _power_w,
    _rate,
    parse_time,
    parse_windows,
)

GetState = Callable[[str], State | None]

_DEFAULT_EV = ZappiCharger()


@dataclass
class Readings:
    now: datetime
    battery_soc: float | None = None
    battery_power: float | None = None   # W, + discharging
    grid_power: float | None = None      # W, + importing
    grid_ref_power: float | None = None  # W, + importing: a second meter, for the cross-check only
    grid_ref_at: datetime | None = None  # when the second meter last reported
    grid_power_inverter: float | None = None  # W, the inverter's own meter (grid_power may be the check meter)
    grid_source: str = "inverter"        # "inverter" | "check meter"
    house_power: float | None = None     # W, house only (car removed)
    house_power_raw: float | None = None
    house_includes_ev: bool = True       # the inverter's house load includes the car charger
    ev_power: float | None = None        # W
    solar_power: float | None = None     # W, all enabled plants
    solar_by_plant: dict[str, float | None] = field(default_factory=dict)
    import_rate: float | None = None     # GBP/kWh now
    export_rate: float | None = None
    standing_charge: float | None = None  # GBP/day
    rates: list[Window] = field(default_factory=list)          # today + tomorrow
    dispatches: list[Window] = field(default_factory=list)     # planned smart-charge slots
    completed_dispatches: list[Window] = field(default_factory=list)
    ev_plug: str | None = None
    car_idle: bool = False               # the last smart slot passed with the car drawing nothing (it's full)
    ev_status: str | None = None
    ev_mode: str | None = None
    ev_session_kwh: float | None = None
    axle_active: bool = False
    axle_start: datetime | None = None
    axle_end: datetime | None = None
    free_active: bool = False
    free_start: datetime | None = None
    free_end: datetime | None = None
    offpeak_now: bool | None = None
    forecast_today_kwh: float | None = None
    forecast_tomorrow_kwh: float | None = None
    problems: list[str] = field(default_factory=list)
    # the EV charger adapter that classifies the plug status (None: the default Zappi one); not part of equality
    ev: Any = field(default=None, repr=False, compare=False)

    # --- derived views -----------------------------------------------------------

    def current_dispatch(self) -> Window | None:
        return next((w for w in self.dispatches if w.start <= self.now < w.end), None)

    def next_dispatch(self) -> Window | None:
        return next((w for w in sorted(self.dispatches, key=lambda w: w.start) if w.start > self.now), None)

    def next_rate_change(self) -> Window | None:
        """The first future window whose price differs from the current one."""
        if self.import_rate is None:
            return None
        for w in sorted(self.rates, key=lambda w: w.start):
            if w.start > self.now and w.value is not None and abs(w.value - self.import_rate) > 1e-6:
                return w
        return None

    def rate_period_end(self) -> datetime | None:
        """When the current price stops applying (start of the next different window)."""
        nxt = self.next_rate_change()
        return nxt.start if nxt else None

    def _ev_adapter(self) -> ZappiCharger:
        return self.ev or _DEFAULT_EV

    def ev_state(self) -> str:
        """'charging' | 'plugged_in' | 'unplugged' (the EV adapter's reading of the plug status; see
        `ZappiCharger.classify`)."""
        return self._ev_adapter().classify(self.ev_plug, self.ev_power)

    def ev_complete(self) -> bool:
        """The charger says the charge is complete (car full) while the car is still plugged in."""
        return self._ev_adapter().complete(self.ev_state(), self.ev_status)

    def axle_state(self) -> str:
        if self.axle_active:
            return "active"
        if self.axle_start and self.axle_start > self.now:
            return "scheduled"
        return "idle"

    def free_state(self) -> str:
        if self.free_active:
            return "active"
        if self.free_start and self.free_start > self.now:
            return "scheduled"
        return "none"


# --- parsing helpers ----------------------------------------------------------------

CHECK_METER_MAX_AGE_S = 180        # an older check-meter reading isn't used (the inverter's meter is, uncorrected)


def forecast_kwh(items: Any) -> float | None:
    """Total of a Solcast detailedForecast list (pv_estimate is kW over 30 min)."""
    if not isinstance(items, list) or not items:
        return None
    return round(sum((_num(i.get("pv_estimate")) or 0.0) * 0.5 for i in items if isinstance(i, dict)), 3)


# --- the reader ---------------------------------------------------------------------

def read(cfg: Config, get_state: GetState, now: datetime | None = None, tariff: KrakenTariff | None = None,
         events: AxleEvents | None = None, ev: ZappiCharger | None = None) -> Readings:
    """Build Readings from the config's mappings using `get_state(entity_id)`. The supplier's tariff data and the
    aggregator's events are parsed by the tariff and event adapters, and the car charger's by the EV adapter (the
    Kraken, Axle and Zappi ones unless given)."""
    now = now or datetime.now(timezone.utc)
    tariff = tariff or KrakenTariff()
    events = events or AxleEvents()
    ev = ev or _DEFAULT_EV
    r = Readings(now=now, ev=ev)
    inputs = cfg.inputs

    def state(role: str) -> State | None:
        spec = inputs.get(role)
        if not spec:
            return None
        if "value" in spec:
            return {"state": spec["value"], "attributes": {"unit_of_measurement": spec.get("unit")}}
        return get_state(spec["entity"])

    def inv(role: str) -> bool:
        return bool((inputs.get(role) or {}).get("invert"))

    def attr(role: str, name: str) -> Any:
        s = state(role)
        return ((s or {}).get("attributes") or {}).get(name)

    r.battery_soc = _num((state("battery_soc") or {}).get("state"))
    # some inverters (Solis via SolaX Modbus) report battery power without a sign, plus separate in/out sensors;
    # when both are mapped they replace the single sensor entirely (no fallback to an unsigned value)
    if all("entity" in (inputs.get(k) or {}) for k in ("battery_charge_power", "battery_discharge_power")):
        b_in, b_out = _power_w(state("battery_charge_power")), _power_w(state("battery_discharge_power"))
        r.battery_power = abs(b_out) - abs(b_in) if b_in is not None and b_out is not None else None
    else:
        r.battery_power = _power_w(state("battery_power"), inv("battery_power"))
    r.grid_power = _power_w(state("grid_power"), inv("grid_power"))
    ref = state("grid_power_reference")
    r.grid_ref_power = _power_w(ref, inv("grid_power_reference"))
    if ref and r.grid_ref_power is not None:
        stamp = ref.get("last_reported") or ref.get("last_updated")
        try:
            t = datetime.fromisoformat(str(stamp).replace("Z", "+00:00")) if stamp else None
            r.grid_ref_at = t.replace(tzinfo=timezone.utc) if t is not None and t.tzinfo is None else t
        except ValueError:
            r.grid_ref_at = None
    r.house_power_raw = _power_w(state("house_load_power"))
    r.grid_power_inverter = r.grid_power
    fresh = r.grid_ref_at is not None and abs((now - r.grid_ref_at).total_seconds()) <= CHECK_METER_MAX_AGE_S
    if (cfg.features.get("use_check_meter", True) and fresh and r.grid_ref_power is not None
            and r.grid_power is not None):
        # the check meter is trusted over the inverter's (28 Sep 2026: the Solis meter read ~16% high both ways,
        # so its house load read high while charging and low while selling). The inverter's house load is its
        # meter plus its own AC flow, so correct it by the same difference.
        delta = r.grid_power - r.grid_ref_power
        r.grid_power, r.grid_source = r.grid_ref_power, "check meter"
        if r.house_power_raw is not None:
            r.house_power_raw = max(0.0, r.house_power_raw - delta)
    ev_raw = ev.read(state)
    r.ev_power = ev_raw["power_w"]
    r.house_includes_ev = bool(cfg.system.get("house_load_includes_ev", True))
    if r.house_power_raw is not None:
        car = r.ev_power if (r.house_includes_ev and r.ev_power and r.ev_power > 0) else 0.0
        r.house_power = max(0.0, r.house_power_raw - car)

    total, seen = 0.0, False
    for plant in cfg.solar_plants:
        if not plant.enabled:
            continue
        spec = plant.power
        p = _power_w(get_state(spec["entity"]) if "entity" in spec else {"state": spec.get("value")})
        r.solar_by_plant[plant.id] = p
        if p is not None:
            total += max(0.0, p)
            seen = True
    r.solar_power = total if seen else None

    r.import_rate = tariff.read_import_rate(state)
    r.export_rate = tariff.read_export_rate(state)
    r.standing_charge = tariff.read_standing_charge(state)
    r.rates = tariff.read_rates(state)
    r.dispatches, r.completed_dispatches = tariff.read_dispatches(state)

    r.ev_plug, r.ev_status, r.ev_mode, r.ev_session_kwh = (ev_raw["plug"], ev_raw["status"], ev_raw["mode"],
                                                            ev_raw["session_kwh"])

    r.axle_active, r.axle_start, r.axle_end = events.read_event(state)
    r.free_active, r.free_start, r.free_end = tariff.read_free_sessions(state)
    r.offpeak_now = tariff.read_offpeak(state)

    r.forecast_today_kwh = forecast_kwh(attr("solar_forecast_today", "detailedForecast"))
    r.forecast_tomorrow_kwh = forecast_kwh(attr("solar_forecast_tomorrow", "detailedForecast"))

    for name in ("battery_soc", "battery_power", "grid_power", "house_power", "import_rate"):
        if getattr(r, name) is None:
            r.problems.append(name)
    return r
