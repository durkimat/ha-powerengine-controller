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
from datetime import datetime, timedelta, timezone
from typing import Any

from .config import Config

State = dict[str, Any]
GetState = Callable[[str], State | None]

_ON = {"on", "true", "yes", "1", "active"}


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime
    value: float | None = None           # rate (GBP/kWh), or kWh for dispatches


@dataclass
class Readings:
    now: datetime
    battery_soc: float | None = None
    battery_power: float | None = None   # W, + discharging
    grid_power: float | None = None      # W, + importing
    house_power: float | None = None     # W, house only (car removed)
    house_power_raw: float | None = None
    ev_power: float | None = None        # W
    solar_power: float | None = None     # W, all enabled plants
    solar_by_plant: dict[str, float | None] = field(default_factory=dict)
    import_rate: float | None = None     # GBP/kWh now
    export_rate: float | None = None
    rates: list[Window] = field(default_factory=list)          # today + tomorrow
    dispatches: list[Window] = field(default_factory=list)     # planned smart-charge slots
    ev_plug: str | None = None
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

    def ev_state(self) -> str:
        plug = (self.ev_plug or "").lower()
        if self.ev_power is not None and self.ev_power > 100:
            return "charging"
        if not plug or "disconnect" in plug or plug in ("unknown", "unavailable"):
            return "unplugged"
        return "plugged_in"

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

def _num(value: Any) -> float | None:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if n == n else None           # drop NaN


def parse_time(value: Any) -> datetime | None:
    if value in (None, "", "unknown", "unavailable", "None"):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _power_w(state: State | None, invert: bool = False) -> float | None:
    if not state:
        return None
    n = _num(state.get("state"))
    if n is None:
        return None
    unit = (state.get("attributes") or {}).get("unit_of_measurement")
    if unit == "kW":
        n *= 1000
    return -n if invert else n


def _energy_kwh(state: State | None) -> float | None:
    if not state:
        return None
    n = _num(state.get("state"))
    if n is None:
        return None
    return n / 1000 if (state.get("attributes") or {}).get("unit_of_measurement") == "Wh" else n


def _rate(state: State | None) -> float | None:
    if not state:
        return None
    n = _num(state.get("state"))
    if n is None:
        return None
    return n / 100 if (state.get("attributes") or {}).get("unit_of_measurement") == "p/kWh" else n


def parse_windows(items: Any, value_keys: tuple[str, ...] = ("value_inc_vat", "value")) -> list[Window]:
    """Parse [{start, end, value...}] lists (EDF/Octopus rates, dispatches)."""
    out: list[Window] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        start, end = parse_time(it.get("start")), parse_time(it.get("end"))
        if not start:
            continue
        end = end or start + timedelta(minutes=30)
        value = next((_num(it[k]) for k in value_keys if k in it and _num(it[k]) is not None), None)
        out.append(Window(start, end, value))
    return out


def forecast_kwh(items: Any) -> float | None:
    """Total of a Solcast detailedForecast list (pv_estimate is kW over 30 min)."""
    if not isinstance(items, list) or not items:
        return None
    return round(sum((_num(i.get("pv_estimate")) or 0.0) * 0.5 for i in items if isinstance(i, dict)), 3)


def _is_on(state: State | None) -> bool:
    return bool(state) and str(state.get("state", "")).lower() in _ON


# --- the reader ---------------------------------------------------------------------

def read(cfg: Config, get_state: GetState, now: datetime | None = None) -> Readings:
    """Build Readings from the config's mappings using `get_state(entity_id)`."""
    now = now or datetime.now(timezone.utc)
    r = Readings(now=now)
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
    r.battery_power = _power_w(state("battery_power"), inv("battery_power"))
    r.grid_power = _power_w(state("grid_power"), inv("grid_power"))
    r.house_power_raw = _power_w(state("house_load_power"))
    r.ev_power = _power_w(state("ev_charge_power"))
    if r.house_power_raw is not None:
        car = r.ev_power if r.ev_power and r.ev_power > 0 else 0.0
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

    r.import_rate = _rate(state("import_rate_now"))
    r.export_rate = _rate(state("export_rate"))
    r.rates = parse_windows(attr("import_rates_today", "rates")) + parse_windows(attr("import_rates_tomorrow", "rates"))
    r.dispatches = parse_windows(attr("smart_dispatches", "planned_dispatches"), ("charge_in_kwh",))

    r.ev_plug = (state("ev_plug_status") or {}).get("state")
    r.ev_status = (state("ev_charger_status") or {}).get("state")
    r.ev_mode = (state("ev_charge_mode") or {}).get("state")
    r.ev_session_kwh = _energy_kwh(state("ev_session_energy"))

    r.axle_active = _is_on(state("axle_event_active"))
    r.axle_start = parse_time((state("axle_event_start") or {}).get("state"))
    r.axle_end = parse_time((state("axle_event_end") or {}).get("state"))
    r.free_active = _is_on(state("free_power_active"))
    r.free_start = parse_time((state("free_power_next_start") or {}).get("state"))
    r.free_end = parse_time((state("free_power_next_end") or {}).get("state"))
    off = state("offpeak_now")
    r.offpeak_now = _is_on(off) if off else None

    r.forecast_today_kwh = forecast_kwh(attr("solar_forecast_today", "detailedForecast"))
    r.forecast_tomorrow_kwh = forecast_kwh(attr("solar_forecast_tomorrow", "detailedForecast"))

    for name in ("battery_soc", "battery_power", "grid_power", "house_power", "import_rate"):
        if getattr(r, name) is None:
            r.problems.append(name)
    return r
