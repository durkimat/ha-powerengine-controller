"""Equipment scenarios for the Simulator (#54 phase 4): a bigger battery, more solar, a second car.

Each is applied to the recorded days of a tariff and compared with the same tariff without it:
- battery: the optimiser runs a battery of the new size and power instead of yours;
- solar: the recorded solar is scaled by (your kWp + extra kWp) / your kWp (same roof direction);
- second car: its daily energy (miles a year x kWh a mile / 365) is charged in the day's cheapest half-hours.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace


@dataclass
class EquipmentSettings:
    battery_enabled: bool = False
    battery_kwh: float = 0.0             # usable capacity of the new battery (total, replacing yours)
    battery_kw: float = 0.0              # its charge/discharge power
    battery_cost: float = 0.0
    solar_enabled: bool = False
    solar_current_kwp: float = 0.0
    solar_extra_kwp: float = 0.0
    solar_cost: float = 0.0
    ev2_enabled: bool = False
    ev2_miles_year: float = 0.0
    ev2_kwh_per_mile: float = 0.3
    ev2_charger_kw: float = 7.4

    @classmethod
    def from_dict(cls, d: dict | None) -> EquipmentSettings:
        s = cls()
        for k, v in (d or {}).items():
            if not hasattr(s, k):
                continue
            if k.endswith("_enabled"):
                setattr(s, k, bool(v))
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
        if self.battery_enabled and (self.battery_kwh <= 0 or self.battery_kw <= 0):
            out.append("battery: enter its usable kWh and power")
        if self.solar_enabled and (self.solar_current_kwp <= 0 or self.solar_extra_kwp <= 0):
            out.append("solar: enter your current kWp and the extra kWp")
        if self.ev2_enabled and (self.ev2_miles_year <= 0 or self.ev2_kwh_per_mile <= 0 or self.ev2_charger_kw <= 0):
            out.append("second car: enter miles a year, kWh a mile and charger power")
        return out

    def kinds(self) -> list[str]:
        k = [n for n, on in (("battery", self.battery_enabled), ("solar", self.solar_enabled),
                             ("ev2", self.ev2_enabled)) if on]
        return k + (["all"] if len(k) > 1 else [])


LABELS = {"battery": "Bigger battery", "solar": "More solar", "ev2": "Second car", "all": "All of these"}


def params_for(kind: str, s: EquipmentSettings, p):
    if kind in ("battery", "all") and s.battery_enabled:
        return replace(p, capacity_kwh=s.battery_kwh, max_charge_kw=s.battery_kw, max_discharge_kw=s.battery_kw)
    return p


def cost_of(kind: str, s: EquipmentSettings) -> float:
    parts = {"battery": s.battery_cost if s.battery_enabled else 0.0,
             "solar": s.solar_cost if s.solar_enabled else 0.0, "ev2": 0.0}
    return sum(parts.values()) if kind == "all" else parts.get(kind, 0.0)


def adjust_slots(kind: str, s: EquipmentSettings, slots) -> dict:
    """Change the day's slots in place for solar or a second car; returns what was added (for the results)."""
    info = {}
    if kind in ("solar", "all") and s.solar_enabled and s.solar_current_kwp > 0:
        f = (s.solar_current_kwp + s.solar_extra_kwp) / s.solar_current_kwp
        added = 0.0
        for sl in slots:
            extra = sl.solar_kwh * (f - 1)
            sl.solar_kwh += extra
            added += extra
        info["extra_solar_kwh"] = round(added, 2)
    if kind in ("ev2", "all") and s.ev2_enabled:
        need = s.ev2_miles_year * s.ev2_kwh_per_mile / 365
        cap = s.ev2_charger_kw * 0.5
        for i in sorted(range(len(slots)), key=lambda k: (slots[k].price if slots[k].price is not None else 9.99, k)):
            if need <= 1e-6:
                break
            take = min(cap, need)
            slots[i].load_kwh += take
            slots[i].car_kw = (slots[i].car_kw or 0.0) + take / 0.5
            need -= take
        info["ev2_kwh"] = round(s.ev2_miles_year * s.ev2_kwh_per_mile / 365, 2)
    return info
