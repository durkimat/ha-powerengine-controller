"""Passive mode's simulated battery: the SoC PowerEngine would have produced.

Each cycle the current decision is applied to the real house load and solar
over the elapsed time, using the planner's battery physics. At local midnight
the simulation re-syncs to the real battery, so drift never builds up.
"""

from __future__ import annotations

from datetime import datetime

from .decide import NONE, Decision
from .forecast import Slot
from .planner import Params, PlanSlot, step
from .readings import Readings


class SimBattery:
    def __init__(self):
        self.soc: float | None = None
        self.day = None
        self.last: datetime | None = None
        self.cost_today = 0.0          # GBP the simulated system would have paid today

    def update(self, decision: Decision | None, r: Readings, p: Params, tz=None) -> float | None:
        local_day = (r.now.astimezone(tz) if tz else r.now).date()
        if self.soc is None or local_day != self.day:
            self.soc, self.day, self.last, self.cost_today = r.battery_soc, local_day, r.now, 0.0
            return self.soc
        if decision is None or decision.action == NONE or r.house_power is None or self.last is None:
            self.last = r.now
            return self.soc
        dt_h = max(0.0, min((r.now - self.last).total_seconds() / 3600, 0.25))   # ignore long gaps
        self.last = r.now
        if dt_h == 0:
            return self.soc
        slot = Slot(r.now, r.import_rate, r.export_rate,
                    solar_kwh=(r.solar_power or 0.0) / 1000 * dt_h, load_kwh=r.house_power / 1000 * dt_h,
                    car_kw=(r.ev_power or 0.0) / 1000 if r.ev_state() == "charging" else 0.0)
        ps = PlanSlot(slot, decision.action, decision.reason, target_soc=decision.target_soc)
        self.soc = step(ps, self.soc, p, dt_h)
        self.cost_today += ps.cost
        return self.soc
