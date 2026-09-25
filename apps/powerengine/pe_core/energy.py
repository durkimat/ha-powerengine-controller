"""Energy flows: who supplied what to whom, per half-hour.

Each 30-second reading is split into flows from three sources (solar, grid import, battery discharge) to four
destinations (house, car, battery charge, export). The split is a convention, applied the same way every time:

1. Solar, then the battery, then the grid supply the house and the car (in that order).
2. Solar left over charges the battery, then is exported.
3. Battery discharge left over is exported.
4. Grid import left over charges the battery.

Grid import and export themselves are metered, so they are never guessed; anything the meters don't account for
(inverter losses, standby, sensor mismatch) is kept as `unallocated` so the cost reconciliation can show it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .readings import Readings

HALF = timedelta(minutes=30)
FLOW_VERSION = 2          # bump when the way flows are measured changes; older days are rebuilt from history
MAX_GAP_S = 300                     # longer gaps between readings aren't integrated

FLOWS = ("s_h", "s_c", "s_b", "s_e", "b_h", "b_c", "b_e", "g_h", "g_c", "g_b")
SOURCES = {"s": "solar", "b": "battery", "g": "grid"}
SINKS = {"h": "house", "c": "car", "b": "battery", "e": "export"}


def half_hour(t: datetime) -> datetime:
    return t.replace(minute=0 if t.minute < 30 else 30, second=0, microsecond=0)


def allocate(solar_w: float, grid_w: float, battery_w: float, house_w: float, car_w: float) -> dict[str, float]:
    """Split one instant's power (W) into flows (W). grid + = import, battery + = discharge."""
    src = {"s": max(0.0, solar_w), "b": max(0.0, battery_w), "g": max(0.0, grid_w)}
    need = {"h": max(0.0, house_w), "c": max(0.0, car_w)}
    room = {"b": max(0.0, -battery_w), "e": max(0.0, -grid_w)}
    out = dict.fromkeys(FLOWS, 0.0)

    def give(s: str, d: str, pool: dict) -> None:
        amount = min(src[s], pool[d])
        if amount > 0:
            out[f"{s}_{d}"] += amount
            src[s] -= amount
            pool[d] -= amount

    for s in ("s", "b", "g"):                  # 1. the house and car first
        give(s, "h", need)
        give(s, "c", need)
    give("s", "b", room)                        # 2. solar left over: battery, then export
    give("s", "e", room)
    give("b", "e", room)                        # 3. battery left over: export
    give("g", "b", room)                        # 4. grid left over: battery
    out["unallocated_src"] = sum(src.values())
    out["unallocated_sink"] = sum(need.values()) + sum(room.values())
    return out


@dataclass
class HalfHour:
    """Energy (kWh) and context for one half-hour, built from readings as they arrive."""
    start: datetime
    kwh: dict[str, float] = field(default_factory=lambda: dict.fromkeys(FLOWS, 0.0))
    grid_import: float = 0.0          # metered, kWh
    grid_export: float = 0.0
    house: float = 0.0
    car: float = 0.0
    solar: float = 0.0
    battery_in: float = 0.0
    battery_out: float = 0.0
    unallocated_src: float = 0.0
    unallocated_sink: float = 0.0
    seconds: float = 0.0
    soc_start: float | None = None
    soc_end: float | None = None
    axle: bool = False
    free: bool = False
    import_rate: float | None = None   # last rate seen (fallback when the rate list is missing)
    export_rate: float | None = None
    standing: float | None = None      # GBP/day

    def add(self, r: Readings, dt_s: float) -> None:
        if self.soc_start is None:
            self.soc_start = r.battery_soc
        if r.battery_soc is not None:
            self.soc_end = r.battery_soc
        self.axle = self.axle or r.axle_active
        self.free = self.free or r.free_active
        self.import_rate = r.import_rate if r.import_rate is not None else self.import_rate
        self.export_rate = r.export_rate if r.export_rate is not None else self.export_rate
        if r.standing_charge is not None:
            self.standing = r.standing_charge
        if dt_s <= 0 or None in (r.grid_power, r.battery_power, r.house_power):
            return
        h = dt_s / 3600 / 1000                         # W -> kWh over dt
        car_w = (r.ev_power or 0.0) if r.ev_state() == "charging" else 0.0
        solar_w = r.solar_power or 0.0
        f = allocate(solar_w, r.grid_power, r.battery_power, r.house_power, car_w)
        for k in FLOWS:
            self.kwh[k] += f[k] * h
        self.unallocated_src += f["unallocated_src"] * h
        self.unallocated_sink += f["unallocated_sink"] * h
        self.grid_import += max(0.0, r.grid_power) * h
        self.grid_export += max(0.0, -r.grid_power) * h
        self.battery_out += max(0.0, r.battery_power) * h
        self.battery_in += max(0.0, -r.battery_power) * h
        self.house += max(0.0, r.house_power) * h
        self.car += car_w * h
        self.solar += solar_w * h
        self.seconds += dt_s

    def as_dict(self) -> dict:
        d = {k: round(v, 5) for k, v in self.kwh.items()}
        d.update({k: (round(v, 5) if isinstance(v, float) else v) for k, v in {
            "start": self.start.isoformat(), "grid_import": self.grid_import, "grid_export": self.grid_export,
            "house": self.house, "car": self.car, "solar": self.solar, "battery_in": self.battery_in,
            "battery_out": self.battery_out, "unallocated_src": self.unallocated_src,
            "unallocated_sink": self.unallocated_sink, "seconds": self.seconds, "soc_start": self.soc_start,
            "soc_end": self.soc_end, "axle": self.axle, "free": self.free, "import_rate": self.import_rate,
            "export_rate": self.export_rate, "standing": self.standing}.items()})
        d["fv"] = str(FLOW_VERSION)            # replaced by the cost book's full flow id
        return d


class Recorder:
    """Feeds readings into half-hours; returns each half-hour once it is complete."""

    def __init__(self):
        self.current: HalfHour | None = None
        self.last: datetime | None = None

    def add(self, r: Readings) -> HalfHour | None:
        start = half_hour(r.now)
        done = None
        if self.current is not None and start != self.current.start:
            # credit the time up to the boundary to the old half-hour, the rest to the new one
            boundary = start
            if self.last is not None and self.last < boundary:
                gap = (boundary - self.last).total_seconds()
                if gap <= MAX_GAP_S:
                    self.current.add(r, gap)
                self.last = boundary
            done, self.current = self.current, None
        if self.current is None:
            self.current = HalfHour(start)
        dt = (r.now - self.last).total_seconds() if self.last else 0.0
        self.current.add(r, dt if dt <= MAX_GAP_S else 0.0)
        self.last = r.now
        return done
