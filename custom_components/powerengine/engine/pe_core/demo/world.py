"""The demo world: a simulated home, tariff and battery that stand in for Home Assistant (demo plan, step C2).

Pure module, no AppDaemon. `DemoWorld` answers `get_state` / `get_history` / `call_service` for a set of demo entities
whose ids satisfy the same patterns the real adapters look for (Solis remote control, Kraken rates, Zappi, Solcast,
Axle), so those adapters run unmodified. The house, car, solar, rates and events are one of the pack's recorded days,
moved onto today (`pack.day_at`); the battery is simulated and follows the remote-control command PowerEngine sends,
so the demo is closed-loop.

Battery model (stepped in minutes): 18 kWh, 95 % each way, 5 kW limits, a 12 % floor and a 100 % ceiling.
    Off (self-use)       covers house + car - solar from the battery, charges from any surplus solar
    Force charge at P    charges at P (from solar first, then the grid), up to the ceiling
    Force charge at 0 W  "hold": the battery neither discharges nor is charged from the grid (solar may still charge it)
    Force discharge at P discharges at P (to the house, the rest exported), down to the floor
Failsafe: a remote-control command that is not re-sent within 5 minutes reverts to Off, as the real inverter does.
Grid power (+ importing) = house + car - solar + charge - discharge, always.

Only these services are accepted: `select/select_option` on the mode select and `number/set_value` on the remote-control
powers and the minimum-SoC number. Anything else is refused (returns False and is recorded in `refused`): the demo never
touches a real system.

Days: today is the chosen pack day. Earlier dates play the pack's other days backwards in its order (so 14 days of
history exist); tomorrow, and any later date, repeat the chosen day.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from . import pack as packmod

CAPACITY_KWH = 18.0
EFFICIENCY = 0.95
LIMIT_W = 5000.0
FLOOR_PCT = 12.0
FAILSAFE_S = 300.0
STEP_S = 60.0
MAX_CATCH_UP = timedelta(hours=12)
OPTIONS = ("Off", "Force charge", "Force discharge")

# role -> entity id (the demo config maps every role to these). The rates and tariff entities carry "edf_energy" so
# `supplier_of` names the supplier the way the recorded data was made; nothing else depends on the ids.
IDS = {
    "battery_soc": "sensor.demo_inverter_battery_soc",
    "battery_power": "sensor.demo_inverter_battery_power",
    "battery_charge_today": "sensor.demo_inverter_battery_charge_today",
    "battery_discharge_today": "sensor.demo_inverter_battery_discharge_today",
    "inverter_min_soc": "number.demo_inverter_battery_minimum_soc",
    "grid_power": "sensor.demo_inverter_meter_power",
    "grid_import_today": "sensor.demo_inverter_grid_import_today",
    "grid_export_today": "sensor.demo_inverter_grid_export_today",
    "house_load_power": "sensor.demo_inverter_house_load",
    "house_load_today": "sensor.demo_inverter_house_load_today",
    "solar_power": "sensor.demo_solar_power",
    "solar_energy_today": "sensor.demo_solar_energy_today",
    "solar_forecast_today": "sensor.demo_solar_forecast_today",
    "solar_forecast_tomorrow": "sensor.demo_solar_forecast_tomorrow",
    "solar_forecast_day3": "sensor.demo_solar_forecast_day_3",
    "import_rate_now": "sensor.demo_edf_energy_import_current_rate",
    "import_rates_today": "event.demo_edf_energy_import_current_day_rates",
    "import_rates_tomorrow": "event.demo_edf_energy_import_next_day_rates",
    "export_rate": "sensor.demo_edf_energy_export_current_rate",
    "standing_charge": "sensor.demo_edf_energy_import_current_standing_charge",
    "offpeak_now": "binary_sensor.demo_edf_energy_import_off_peak",
    "smart_dispatches": "binary_sensor.demo_edf_energy_intelligent_dispatching",
    "free_power_active": "binary_sensor.demo_edf_energy_free_electricity_now",
    "free_power_next_start": "sensor.demo_edf_energy_next_free_electricity_session_start",
    "free_power_next_end": "sensor.demo_edf_energy_next_free_electricity_session_end",
    "ev_plug_status": "sensor.demo_zappi_plug_status",
    "ev_charger_status": "sensor.demo_zappi_status",
    "ev_charge_power": "sensor.demo_zappi_power_charging",
    "ev_energy_today": "sensor.demo_zappi_energy_used_today",
    "axle_event_active": "sensor.demo_axle_event_in_progress",
    "axle_event_start": "sensor.demo_axle_start_time",
    "axle_event_end": "sensor.demo_axle_end_time",
    "rc_mode": "select.demo_inverter_battery_control_override",
    "rc_charge_power": "number.demo_inverter_battery_control_override_charge_power",
    "rc_discharge_power": "number.demo_inverter_battery_control_override_discharge_power",
    "guard_read_only": "switch.demo_other_controller_read_only",
    "guard_off_1": "automation.demo_legacy_battery_control",
}
RC_ROLES = ("rc_mode", "rc_charge_power", "rc_discharge_power")
NUMBERS = {IDS["rc_charge_power"]: (0.0, LIMIT_W), IDS["rc_discharge_power"]: (0.0, LIMIT_W),
           IDS["inverter_min_soc"]: (0.0, 100.0)}
HALF_HOUR_H = 0.5


def _num(x: float, places: int = 1):
    return round(float(x), places)


class DemoWorld:
    def __init__(self, pack: dict, day: str, tz, now_fn, *, capacity_kwh: float = CAPACITY_KWH,
                 efficiency: float = EFFICIENCY, charge_limit_w: float = LIMIT_W, discharge_limit_w: float = LIMIT_W,
                 floor_pct: float = FLOOR_PCT, feed=None):
        """The defaults are the demo's own battery. The engine comparison passes the owner's (size, one-way
        efficiency, power limits, floor) and a `feed` (pe_core.compare.snapfeed.SnapshotFeed: `get(role, when)` gives
        the (state, attributes) a role's entity had then, or None) that replaces the forecast, rate, smart-slot, free
        power and grid-event entities the world would derive from the day."""
        if day not in pack["days"]:
            raise ValueError(f"unknown demo day {day!r}; the pack has {', '.join(packmod.days(pack))}")
        self.pack, self.day, self.tz, self.now_fn = pack, day, tz, now_fn
        self.capacity_kwh, self.efficiency, self.floor_pct = float(capacity_kwh), float(efficiency), float(floor_pct)
        self.charge_limit_w, self.discharge_limit_w = float(charge_limit_w), float(discharge_limit_w)
        self.feed = feed
        self.order = packmod.days(pack)
        self.base = self.order.index(day)
        self.refused: list[tuple[str, str, str]] = []
        self.events: list[str] = []
        self._rows_cache: dict[date, list[dict]] = {}
        now = now_fn().astimezone(timezone.utc)
        self.t = now
        self.today0 = self._local(now).date()
        row = self._row(now)
        self.energy = min(max(row["soc"], self.floor_pct), 100.0) / 100 * self.capacity_kwh      # kWh in the battery
        self.option, self.charge_w, self.discharge_w, self.rc_at = "Off", 0.0, 0.0, None
        self.min_soc = self.floor_pct
        self.flows = {"charge_w": 0.0, "discharge_w": 0.0, "grid_w": 0.0, "house_w": 0.0, "car_w": 0.0, "solar_w": 0.0}
        self._start_counters(now)
        self._flows_at(now, 0.0)

    # --- the pack, moved onto dates ---------------------------------------------------------------

    def _local(self, when: datetime) -> datetime:
        return when.astimezone(self.tz)

    def name_for(self, d: date) -> str:
        """The pack day played on local date `d`: today and later dates the chosen day, earlier ones the others back."""
        back = max(0, (self.today0 - d).days)
        return self.order[(self.base - back) % len(self.order)]

    def rows_for(self, d: date) -> list[dict]:
        rows = self._rows_cache.get(d)
        if rows is None:
            rows = self._rows_cache[d] = packmod.day_at(self.pack, self.name_for(d), d, self.tz)
        return rows

    def _row(self, when: datetime) -> dict:
        return packmod.row_at(self.pack, self.name_for(self._local(when).date()), when, self.tz)

    def title(self) -> str:
        """The chosen day's title, with any <<name>> placeholder still in it (fill it with names.fill)."""
        return self.pack["days"][self.day].get("title", self.day)

    def _upcoming(self, flag_windows, now: datetime) -> list[tuple[datetime, datetime]]:
        d = self._local(now).date()
        rows = self.rows_for(d) + self.rows_for(d + timedelta(days=1))
        return [w for w in flag_windows(rows) if w[1] > now]

    # --- the battery and the flows ------------------------------------------------------------------

    def _command(self) -> tuple[str, float]:
        if self.option == "Force charge":
            return "charge", self.charge_w
        if self.option == "Force discharge":
            return "discharge", self.discharge_w
        return "off", 0.0

    def _flows_at(self, t: datetime, dt_h: float) -> None:
        row = self._row(t)
        house, car, solar = row["house"] * 2000.0, row["car"] * 2000.0, row["solar"] * 2000.0
        load = house + car
        floor_kwh = max(self.floor_pct, self.min_soc) / 100 * self.capacity_kwh
        span_h = dt_h or STEP_S / 3600                # dt 0: what the next minute would allow, for the readings
        room_w = max(0.0, self.capacity_kwh - self.energy) * 1000 / (self.efficiency * span_h)
        out_w = max(0.0, self.energy - floor_kwh) * 1000 * self.efficiency / span_h
        mode, power = self._command()
        surplus = solar - load
        charge = discharge = 0.0
        if mode == "off":
            charge = min(max(surplus, 0.0), self.charge_limit_w, room_w)
            discharge = min(max(-surplus, 0.0), self.discharge_limit_w, out_w)
        elif mode == "charge":
            if power <= 0:                                        # hold: no discharge; solar may still charge it
                charge = min(max(surplus, 0.0), self.charge_limit_w, room_w)
            else:
                charge = min(power, self.charge_limit_w, room_w)
        else:
            discharge = min(power, self.discharge_limit_w, out_w)
        self.flows = {"charge_w": charge, "discharge_w": discharge, "house_w": house, "car_w": car, "solar_w": solar,
                      "grid_w": load - solar + charge - discharge}
        self._row_now = row

    def step(self, now: datetime | None = None) -> None:
        """Advance the world to `now` (default: the clock), in steps of at most a minute."""
        now = (now or self.now_fn()).astimezone(timezone.utc)
        if now - self.t > MAX_CATCH_UP:
            self.t = now - MAX_CATCH_UP
        while self.t < now:
            dt = min(STEP_S, (now - self.t).total_seconds())
            self._failsafe(self.t)
            self._flows_at(self.t, dt / 3600)
            self._integrate(dt / 3600)
            self.t += timedelta(seconds=dt)
            if self._local(self.t).date() != self._counter_day:
                self._start_counters(self.t)
        self._failsafe(self.t)
        self._flows_at(self.t, 0.0)

    def _failsafe(self, t: datetime) -> None:
        if self.option != "Off" and self.rc_at is not None and (t - self.rc_at).total_seconds() > FAILSAFE_S:
            self.events.append(f"failsafe: {self.option} not re-sent for {FAILSAFE_S:.0f} s, back to Off at "
                               f"{t.isoformat(timespec='seconds')}")
            self.option, self.charge_w, self.discharge_w, self.rc_at = "Off", 0.0, 0.0, None

    def _integrate(self, dt_h: float) -> None:
        f = self.flows
        self.energy += f["charge_w"] * dt_h / 1000 * self.efficiency - f["discharge_w"] * dt_h / 1000 / self.efficiency
        self.energy = min(max(self.energy, 0.0), self.capacity_kwh)
        c = self.counters
        c["charge"] += f["charge_w"] * dt_h / 1000
        c["discharge"] += f["discharge_w"] * dt_h / 1000
        c["import"] += max(f["grid_w"], 0.0) * dt_h / 1000
        c["export"] += max(-f["grid_w"], 0.0) * dt_h / 1000
        c["house"] += (f["house_w"] + f["car_w"]) * dt_h / 1000
        c["car"] += f["car_w"] * dt_h / 1000
        c["solar"] += f["solar_w"] * dt_h / 1000

    def _start_counters(self, t: datetime) -> None:
        """Today's energy counters: from the day's own record up to `t` (so a demo started at noon has a morning)."""
        local = self._local(t)
        self._counter_day = local.date()
        name = self.name_for(self._counter_day)
        day = self.pack["days"][name]
        rec = day.get("as_recorded") or {}
        i = local.hour * 2 + local.minute // 30
        frac = ((local.minute % 30) * 60 + local.second) / 1800

        def upto(series):
            return sum(series[:i]) + (series[i] * frac if i < len(series) else 0.0)
        self.counters = {
            "house": upto([h + c for h, c in zip(day["house"], day["car"], strict=False)]), "car": upto(day["car"]),
            "solar": upto(day["solar"]), "import": upto(rec.get("grid_import", [0.0] * 48)),
            "export": upto(rec.get("grid_export", [0.0] * 48)), "charge": upto(rec.get("battery_in", [0.0] * 48)),
            "discharge": upto(rec.get("battery_out", [0.0] * 48)),
        }

    @property
    def soc(self) -> float:
        return self.energy / self.capacity_kwh * 100

    # --- Home Assistant's side ----------------------------------------------------------------------

    def is_demo(self, entity_id) -> bool:
        return entity_id in self._entity_ids()

    def _entity_ids(self) -> set[str]:
        ids = getattr(self, "_ids", None)
        if ids is None:
            ids = self._ids = set(IDS.values())
        return ids

    def _stamp(self) -> str:
        return self.t.isoformat()

    def _full(self, entity_id: str, state, attrs: dict) -> dict:
        stamp = self._stamp()
        return {"entity_id": entity_id, "state": state, "attributes": attrs, "last_changed": stamp,
                "last_updated": stamp, "last_reported": stamp}

    def _rates_attr(self, d: date) -> list[dict]:
        return [{"start": r["start"].isoformat(), "end": r["end"].isoformat(), "value_inc_vat": r["act"],
                 "is_capped": False} for r in self.rows_for(d)]

    def _forecast_attr(self, d: date) -> list[dict]:
        return [{"period_start": r["start"].isoformat(), "pv_estimate": _num(r["forecast"] * 2, 3)}
                for r in self.rows_for(d)]

    def _window_state(self, windows: list[tuple[datetime, datetime]], now: datetime, which: int):
        """ISO start (which=0) or end (1) of the current window, else the next one, else 'unknown'."""
        for w in windows:
            if w[0] <= now < w[1]:
                return w[which].isoformat()
        future = [w for w in windows if w[0] > now]
        return future[0][which].isoformat() if future else "unknown"

    def _state(self, entity_id: str):
        """(state, attributes) of a demo entity now."""
        f, c, now = self.flows, self.counters, self.t
        row = self._row_now
        d = self._local(now).date()
        role = next(k for k, v in IDS.items() if v == entity_id)
        if self.feed is not None:
            fed = self.feed.get(role, now)
            if fed is not None:
                return fed
        W = {"unit_of_measurement": "W", "device_class": "power", "state_class": "measurement"}
        KWH = {"unit_of_measurement": "kWh", "device_class": "energy", "state_class": "total_increasing"}
        RATE = {"unit_of_measurement": "GBP/kWh"}
        if role == "battery_soc":
            return _num(self.soc), {"unit_of_measurement": "%", "device_class": "battery"}
        if role == "battery_power":
            return _num(f["discharge_w"] - f["charge_w"], 0), W
        if role == "grid_power":
            return _num(f["grid_w"], 0), W
        if role == "house_load_power":
            return _num(f["house_w"] + f["car_w"], 0), W
        if role == "solar_power":
            return _num(f["solar_w"], 0), W
        if role == "ev_charge_power":
            return _num(f["car_w"], 0), W
        counters = {"battery_charge_today": "charge", "battery_discharge_today": "discharge",
                    "grid_import_today": "import", "grid_export_today": "export", "house_load_today": "house",
                    "solar_energy_today": "solar", "ev_energy_today": "car"}
        if role in counters:
            return _num(c[counters[role]], 3), KWH
        if role == "inverter_min_soc":
            return _num(self.min_soc), {"unit_of_measurement": "%", "min": 0, "max": 100, "step": 1}
        if role == "import_rate_now":
            return _num(row["act"], 5), {**RATE, "tariff": "E-1R-DEMO-A"}
        if role == "export_rate":
            return _num(row["exp"], 5), RATE
        if role == "standing_charge":
            return _num(row["standing"], 4), {"unit_of_measurement": "GBP/day"}
        if role == "import_rates_today":
            return now.isoformat(), {"rates": self._rates_attr(d)}
        if role == "import_rates_tomorrow":
            return now.isoformat(), {"rates": self._rates_attr(d + timedelta(days=1))}
        if role in ("solar_forecast_today", "solar_forecast_tomorrow", "solar_forecast_day3"):
            off = {"solar_forecast_today": 0, "solar_forecast_tomorrow": 1, "solar_forecast_day3": 2}[role]
            items = self._forecast_attr(d + timedelta(days=off))
            return _num(sum(i["pv_estimate"] for i in items) / 2, 3), {"detailedForecast": items,
                                                                       "unit_of_measurement": "kWh"}
        if role == "offpeak_now":
            return ("on" if row["std"] <= row["ovn"] + 1e-9 else "off"), {}
        if role == "smart_dispatches":
            planned = []
            for start, end in self._upcoming(packmod.smart_slots, now):
                kwh = sum(r["car"] for r in self.rows_for(self._local(start).date()) if start <= r["start"] < end)
                planned.append({"start": start.isoformat(), "end": end.isoformat(),
                                "charge_in_kwh": -_num(max(kwh, 0.1), 2), "source": "SMART"})
            on = any(datetime.fromisoformat(p["start"]) <= now for p in planned)
            return ("on" if on else "off"), {"planned_dispatches": planned, "completed_dispatches": []}
        if role == "free_power_active":
            return ("on" if row["free"] else "off"), {}
        if role in ("free_power_next_start", "free_power_next_end"):
            windows = self._upcoming(lambda rows: packmod._windows(rows, "free"), now)
            return self._window_state(windows, now, 0 if role.endswith("start") else 1), {}
        if role == "axle_event_active":
            return ("on" if row["axle"] else "off"), {}
        if role in ("axle_event_start", "axle_event_end"):
            windows = self._upcoming(packmod.axle_events, now)
            return self._window_state(windows, now, 0 if role.endswith("start") else 1), {}
        if role == "ev_plug_status":
            return ("Charging" if f["car_w"] > 100 else "EV Connected"), {}
        if role == "ev_charger_status":
            if f["car_w"] > 100:
                return "Charging", {}
            return ("Completed" if c["car"] > 0.5 else "Paused"), {}
        if role == "rc_mode":
            return self.option, {"options": list(OPTIONS)}
        if role == "rc_charge_power":
            return _num(self.charge_w, 0), {"unit_of_measurement": "W", "min": 0, "max": LIMIT_W, "step": 100}
        if role == "rc_discharge_power":
            return _num(self.discharge_w, 0), {"unit_of_measurement": "W", "min": 0, "max": LIMIT_W, "step": 100}
        if role == "guard_read_only":
            return "on", {}
        if role == "guard_off_1":
            return "off", {}
        raise KeyError(role)

    def get_state(self, entity_id=None, attribute=None, default=None, **kw):
        """AppDaemon's get_state for the demo entities. Other entities are not the world's: None (or `default`).
        No entity id: every demo entity, by id (how the remote-control entities are found)."""
        if entity_id is None:
            return {eid: self._full(eid, *self._state(eid)) for eid in sorted(self._entity_ids())}
        if entity_id not in self._entity_ids():
            return default
        state, attrs = self._state(entity_id)
        if attribute == "all":
            return json.loads(json.dumps(self._full(entity_id, state, attrs), default=str))
        if attribute:
            return attrs.get(attribute, default)
        return state

    # --- services -----------------------------------------------------------------------------------

    def _refuse(self, service: str, entity_id, why: str) -> bool:
        self.refused.append((service, str(entity_id), why))
        return False

    def call_service(self, service: str, **data) -> bool:
        """True if the world took it. Only the mode select and the number entities are writable."""
        took = self._call_service(service, **data)
        if took:
            self._flows_at(self.t, 0.0)                     # the inverter responds at once: the readings follow
        return took

    def _call_service(self, service: str, **data) -> bool:
        service = service.replace(".", "/", 1)
        eid = data.get("entity_id")
        if not isinstance(eid, str) or eid not in self._entity_ids():
            return self._refuse(service, eid, "not a demo entity")
        now = self.now_fn().astimezone(timezone.utc)
        if service == "select/select_option" and eid == IDS["rc_mode"]:
            option = data.get("option")
            if option not in OPTIONS:
                return self._refuse(service, eid, f"option {option!r} not offered")
            self.option, self.rc_at = option, now
            if option == "Off":
                self.charge_w = self.discharge_w = 0.0
            return True
        if service == "number/set_value" and eid in NUMBERS:
            lo, hi = NUMBERS[eid]
            try:
                value = float(data.get("value"))
            except (TypeError, ValueError):
                return self._refuse(service, eid, "not a number")
            if not lo <= value <= hi:
                return self._refuse(service, eid, f"{value} outside {lo}-{hi}")
            if eid == IDS["rc_charge_power"]:
                self.charge_w, self.rc_at = value, now
            elif eid == IDS["rc_discharge_power"]:
                self.discharge_w, self.rc_at = value, now
            else:
                self.min_soc = value
            return True
        return self._refuse(service, eid, "not writable in the demo")

    # --- history ------------------------------------------------------------------------------------

    def _hist_state(self, role: str, d: date, i: int, row: dict):
        rec = self.pack["days"][self.name_for(d)].get("as_recorded") or {}

        def at(name):
            series = rec.get(name)
            return series[i] if series else 0.0
        if role == "battery_soc":
            return row["soc"]
        if role == "battery_power":
            return _num((at("battery_out") - at("battery_in")) * 2000, 0)
        if role == "grid_power":
            return _num((at("grid_import") - at("grid_export")) * 2000, 0)
        if role == "house_load_power":
            return _num((row["house"] + row["car"]) * 2000, 0)
        if role == "solar_power":
            return _num(row["solar"] * 2000, 0)
        if role == "ev_charge_power":
            return _num(row["car"] * 2000, 0)
        if role == "ev_plug_status":
            return "Charging" if row["car"] * 2000 > 100 else "EV Connected"
        if role == "import_rate_now":
            return row["act"]
        if role == "export_rate":
            return row["exp"]
        if role == "standing_charge":
            return row["standing"]
        if role == "axle_event_active":
            return "on" if row["axle"] else "off"
        if role == "free_power_active":
            return "on" if row["free"] else "off"
        return None

    def history(self, entity_id: str, start: datetime, end: datetime) -> list[dict]:
        """State-change rows for a demo entity between `start` and `end`, one per half-hour of the recorded days
        (`last_changed` at each half-hour's start; the rates entity also carries that day's rates)."""
        role = next((k for k, v in IDS.items() if v == entity_id), None)
        if role is None:
            return []
        start, end = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
        out = []
        if role == "import_rates_today":                       # one row per local day, carrying that day's rates
            d = self._local(start).date()
            t = start
            while t < end:
                out.append({"state": t.isoformat(), "last_changed": t.isoformat(),
                            "attributes": {"rates": self._rates_attr(d)}})
                d += timedelta(days=1)
                t = datetime.combine(d, datetime.min.time()).replace(tzinfo=self.tz).astimezone(timezone.utc)
            return out
        local = self._local(start)
        t = local.replace(minute=local.minute // 30 * 30, second=0, microsecond=0).astimezone(timezone.utc)
        while t < end:
            loc = self._local(t)
            value = self._hist_state(role, loc.date(), loc.hour * 2 + loc.minute // 30, self._row(t))
            if value is not None:
                out.append({"state": str(value), "last_changed": max(t, start).isoformat()})
            t += timedelta(minutes=30)
        return out

    def get_history(self, entity_id=None, start_time=None, end_time=None, **kw) -> list:
        """AppDaemon's get_history shape: a list holding one list of rows."""
        now = self.now_fn()
        end = end_time or now
        start = start_time or end - timedelta(days=1)
        return [self.history(entity_id, start, end)]
