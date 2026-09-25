"""PowerEngine AppDaemon entry point.

A thin adapter between AppDaemon/Home Assistant and pe_core. Decision logic
belongs in pe_core so it can be tested offline.

0.0.x builds are Passive-only: they publish PowerEngine's own entities over
MQTT and never write to the inverter or any other device.
"""

import dataclasses
import json
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import appdaemon.plugins.hass.hassapi as hass

from pe_core import __version__
from pe_core.activity import ActivityLog
from pe_core.checks import OK, check, summarise
from pe_core.config import (
    DEFAULT_PATHS,
    ConfigError,
    load_config,
    required_roles,
    settings_catalogue,
    uses_battery_pair,
)
from pe_core.costbook import MIN_MEASURE_DAYS, CostBook, cost_entity_states
from pe_core.costs import METHOD_VERSION
from pe_core.dashboard import sync_dashboard
from pe_core.decide import decide
from pe_core.eeprom import WriteLog, WriteModel
from pe_core.energy import Recorder
from pe_core.entities import (
    AVAILABILITY_TOPIC,
    BASE_TOPIC,
    ENTITIES,
    OFFLINE,
    ONLINE,
    discovery_payload,
    removal_messages,
    validate_definitions,
)
from pe_core.forecast import LoadProfile, build_slots, house_only_means, parse_history, profile_from_means
from pe_core.health import plan_snapshot
from pe_core.loadstore import LoadStore
from pe_core.modes import effective_mode
from pe_core.notify import Notifier, axle_message, daily_message, free_message, health_message, input_message
from pe_core.planner import make_plan, params_from, plan_entity_states
from pe_core.readings import read
from pe_core.replay import Timeline, flow_id, history_entities, replay
from pe_core.roles import ROLE_BY_KEY, ROLES, catalogue
from pe_core.simulate import SimBattery
from pe_core.slots import SlotTracker
from pe_core.status import entity_states
from pe_core.store import save_config

HEARTBEAT_SECONDS = 60
CYCLE_SECONDS = 30
REPLAN_SECONDS = 300
HISTORY_DAYS = 14
BACKFILL_DAYS = 14
RECHECK_SECONDS = 300
SAVE_EVENT = "pe_config_save"
RESULT_EVENT = "pe_config_result"


class PowerEngine(hass.Hass):
    def initialize(self):
        self.log(f"PowerEngine {__version__} starting (Passive-only build: nothing is controlled)")
        validate_definitions()

        # Optional override. Not "config_path": AppDaemon sets that arg itself.
        custom = self.args.get("settings_file")
        self.paths = [custom] if custom else list(DEFAULT_PATHS)
        self.cfg, self.cfg_path, self.cfg_error = None, None, None
        try:
            self.cfg, self.cfg_path = load_config(self.paths)
        except ConfigError as err:
            self.cfg_error = str(err)
            self.log(f"Config problem: {err}", level="WARNING")
        if self.cfg is None and self.cfg_error is None:
            looked = ", ".join(self.paths)
            self.log(f"No config.yaml found (looked in {looked}); running unconfigured.", level="WARNING")
        elif self.cfg is not None:
            self.log(f"Loaded config from {self.cfg_path}: {len(self.cfg.inputs)} inputs, "
                     f"{len(self.cfg.solar_plants)} solar plant(s)")

        self.mqtt = self._mqtt_api()
        if self.mqtt is None:
            return
        if self.cfg is not None and self.cfg.remove_entities:
            self._remove_entities()
            return

        for ent in ENTITIES:
            self._publish(ent.discovery_topic, discovery_payload(ent, __version__))
        self._ui_defaults()
        self._publish(AVAILABILITY_TOPIC, ONLINE)
        self._publish_state("diag_version", __version__)
        self._publish_state("diag_started", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        self._publish_state("map_catalogue", str(len(ROLES)), catalogue())
        self._publish_state("map_settings", str(len(settings_catalogue()["safety"])), settings_catalogue())
        self._last_checks = None
        self._published = {}
        self._decision, self._since = None, None
        try:   # keep the activity log across restarts (it lives in the entity's attributes)
            saved = self.get_state("sensor.pe_state_activity", attribute="entries")
        except Exception:
            saved = None
        self.activity = ActivityLog(saved if isinstance(saved, list) else None)
        self.profile: LoadProfile | None = None
        self._hist_means: dict = {}
        self.loadstore = LoadStore(os.path.join(os.path.dirname(self._save_path()), "load_history.json"))
        try:
            n = self.loadstore.load()
            if n:
                self.log(f"Load record: {n} half-hours recorded by PowerEngine")
        except Exception as err:
            self.log(f"Could not read the load record: {err!r}", level="WARNING")
        self.plan, self._plan_sig, self._plan_time = None, None, None
        self.sim = SimBattery()
        try:
            self.tz = ZoneInfo(str(self.get_timezone()))
        except Exception:
            self.tz = None
        self.recorder = Recorder()
        self.notifier = Notifier(os.path.join(os.path.dirname(self._save_path()), "notifications.json"))
        self._bad_since = {}
        self.writes = WriteLog(os.path.join(os.path.dirname(self._save_path()), "inverter_writes.json"))
        self.write_model = WriteModel()
        self._write_listeners = []
        self._watch_controls()
        self._publish_writes()
        self.slots = SlotTracker(os.path.join(os.path.dirname(self._save_path()), "costs", "slots.json"))
        self._slots_last = None
        self.costbook, self._months, self.measured = None, [], None
        try:
            self.costbook = CostBook(os.path.join(os.path.dirname(self._save_path()), "costs"), self.tz)
            if self.cfg is not None:
                self.costbook.flow_id = flow_id(self.cfg)
            self._measure(revalue=False)
            if self.costbook.needs_revalue and self.cfg is not None:
                n = self.costbook.revalue(**self._cost_params())
                self.log(f"Costs: re-valued {n} half-hours with method {METHOD_VERSION}")
            self._refresh_months()
        except Exception as err:
            self.log(f"Could not open the cost book: {err!r}", level="WARNING")
        self._evaluate()                                   # also publishes mode + config status
        self._cycle({})
        self._sync_dashboard()

        self.listen_event(self._on_save, SAVE_EVENT)
        self._beat({})
        self.run_every(self._beat, "now+60", HEARTBEAT_SECONDS)
        self.run_every(lambda kwargs: self._evaluate(), f"now+{RECHECK_SECONDS}", RECHECK_SECONDS)
        self.run_every(self._cycle, f"now+{CYCLE_SECONDS}", CYCLE_SECONDS)
        self.run_in(self._learn_load, 5)                     # load profile from history, then daily
        self.run_daily(self._learn_load, "00:10:00")
        self.run_daily(lambda kwargs: (self._refresh_months(prune=True), self._measure()), "00:05:00")
        self.run_daily(self._daily_summary, "08:00:00")
        self.run_in(self._backfill, 90)                      # fill recent days from HA history (after load learning)
        self.run_daily(self._backfill, "00:20:00")           # and any day with gaps (e.g. restarts)
        self.log(f"Published {len(ENTITIES)} entities under the PowerEngine device")

    def terminate(self):
        if getattr(self, "mqtt", None) is not None:
            self._publish(AVAILABILITY_TOPIC, OFFLINE)

    # --- mapping checks and mode -----------------------------------------------------

    def _evaluate(self):
        checks, required = {}, []
        if self.cfg is not None:
            required = required_roles(self.cfg)
            for role in ROLES:
                spec = self.cfg.inputs.get(role.key)
                state = None
                if spec and "entity" in spec:
                    state = self.get_state(spec["entity"], attribute="all")
                checks[role.key] = check(role, spec, state)
            if uses_battery_pair(self.cfg) and "battery_power" in self.cfg.inputs:
                checks["battery_power"] = (OK, "Not used: the charging and discharging sensors are mapped")
        missing = [k for k in required if checks.get(k, ("unmapped", ""))[0] != OK]
        self._watch_inputs(checks, required)
        mode = effective_mode(self.cfg, self.cfg_error, missing_required=missing)
        self.mode = mode

        if self.cfg_error:
            overall = "error"
        elif self.cfg is None:
            overall = "unconfigured"
        else:
            overall = summarise(checks, required)

        signature = (overall, mode, tuple(sorted(checks.items())))
        if signature == self._last_checks:
            return
        self._last_checks = signature
        self._checks = {k: {"status": s, "message": m} for k, (s, m) in checks.items()}
        self._health()
        self._publish_state("diag_config_ok", "ON" if overall in ("ok", "warnings") else "OFF",
                            {"reason": self.cfg_error or overall, "file": self.cfg_path})
        self._publish_state("cfg_operation_mode", mode.configured)
        self._publish_state("state_operation_mode", mode.effective, {"reason": mode.reason})
        self._publish_state("map_config", overall, {
            "config": self.cfg.raw if self.cfg else {},
            "checks": {k: {"status": s, "message": m} for k, (s, m) in checks.items()},
            "required": required,
            "file": self.cfg_path,
            "save_path": self._save_path(),
            "error": self.cfg_error,
        })
        self.log(f"Inputs: {overall}; mode {mode.effective} ({mode.reason})")

    # --- the monitoring cycle ------------------------------------------------------

    def _cycle(self, kwargs):
        """Read inputs, decide (Passive: would-do only), then publish entities that changed."""
        readings, decision = None, None
        if self.cfg is not None and self.mode.effective != "unconfigured":
            try:
                readings = read(self.cfg, lambda eid: self.get_state(eid, attribute="all"))
                self._record_load(readings)
                self._record_costs(readings)
                self._watch_events(readings)
                self._track_slots(readings)
                self._maybe_replan(readings)
                decision = decide(readings, self.cfg, self._decision, self.tz, plan=self.plan)
                sim = self.sim.update(decision, readings, self._params(), self.tz)
                if sim is not None:
                    self._publish_if_changed("state_sim_soc", round(sim, 1),
                                             {"cost_today": round(self.sim.cost_today, 2),
                                              "real_soc": readings.battery_soc})
            except Exception as err:          # never let one bad reading stop the app
                self.log(f"Reading, planning or deciding failed: {err!r}", level="WARNING")
        if decision is not None:
            passive = self.mode.effective != "active"
            entry = self.activity.record(decision, readings.now, passive, self.tz)
            if entry:
                self._since = entry["hhmm"]
                self.log(f"Decision: {entry['text']}")
                self._logbook(entry["text"])
                self._publish_state("state_activity", entry["time"], {"entries": self.activity.entries})
            self._decision = decision
            self._count_would_writes(readings, decision)
        for key, (state, attrs) in entity_states(readings, self.mode, self.tz, decision, self._since).items():
            self._publish_if_changed(key, state, attrs)

    # --- cost accounting -------------------------------------------------------------

    def _today(self):
        return datetime.now(self.tz or timezone.utc).date()

    def _record_costs(self, r):
        if self.costbook is None:
            return
        hh = self.recorder.add(r)
        if hh is None:
            return
        try:
            rec = self.costbook.add(hh, r, **self._cost_params())
            if rec is None:
                return
            if rec["v"].get("event"):
                self._refresh_months()
            self._publish_costs()
        except Exception as err:
            self.log(f"Cost accounting failed for {hh.start.isoformat()}: {err!r}", level="WARNING")

    def _params(self, readings=None):
        """Planner/simulation parameters, with the measured battery efficiency once there is enough data."""
        p = params_from(self.cfg, readings)
        m = getattr(self, "measured", None)
        if m and m.get("measured") and m.get("efficiency"):
            p = dataclasses.replace(p, efficiency=m["efficiency"])
        if m and m.get("capacity_measured") and m.get("capacity_kwh"):
            p = dataclasses.replace(p, capacity_kwh=m["capacity_kwh"])
        return p

    def _measure(self, revalue=True):
        """Measure battery efficiency and system losses; publish them; re-value costs if the efficiency moved."""
        if self.costbook is None or self.cfg is None:
            return
        try:
            old = self.measured or {}
            before = old.get("efficiency")
            cap_before = old.get("capacity_kwh") if old.get("capacity_measured") else None
            self.measured = self.costbook.measure(self._today(), params_from(self.cfg).capacity_kwh)
            m = self.measured
            configured = params_from(self.cfg).efficiency
            eff = m["efficiency"] if m["measured"] else configured
            self._publish_state("diag_battery_efficiency", round(eff * eff * 100, 1), {
                "measured": m["measured"], "one_way": round(eff, 4), "configured_one_way": configured,
                "days": m["days"], "battery_in_kwh": m["battery_in"], "battery_out_kwh": m["battery_out"],
                "note": "measured over the last 30 days" if m["measured"]
                else f"estimated (configured figure) until {MIN_MEASURE_DAYS} full days are recorded"})
            self._publish_state("diag_system_losses", m.get("losses_yesterday") if m.get("losses_yesterday") is not None
                                else "unknown", {"average_kwh": m.get("losses_avg"), "by_day": m["losses"]})
            if m["measured"]:
                self.log(f"Battery round trip measured at {m['rte'] * 100:.1f}% over {m['days']} days")
            self._publish_state("diag_battery_capacity", m["capacity_kwh"] if m.get("capacity_kwh") else "unknown", {
                "measured": m.get("capacity_measured"), "configured_kwh": params_from(self.cfg).capacity_kwh,
                "samples": m.get("capacity_samples"), "max_charge_kw": m.get("max_charge_kw"),
                "max_discharge_kw": m.get("max_discharge_kw"), "min_soc_seen": m.get("min_soc")})
            cap_now = m.get("capacity_kwh") if m.get("capacity_measured") else None
            eff_moved = m["measured"] and (before is None or abs(m["efficiency"] - before) > 0.002)
            cap_moved = cap_now is not None and (cap_before is None or abs(cap_now - cap_before) > 0.2)
            if revalue and (eff_moved or cap_moved):
                n = self.costbook.revalue(**self._cost_params())
                self.log(f"Costs re-valued ({n} half-hours) with the measured battery efficiency/capacity")
                self._refresh_months()
        except Exception as err:
            self.log(f"Could not measure losses: {err!r}", level="WARNING")

    def _cost_params(self):
        p = self._params()
        return {"capacity": p.capacity_kwh, "eff": p.efficiency, "floor_soc": p.min_reserve_soc,
                "max_kw": p.max_discharge_kw, "includes_ev": p.hold_for_car, "axle_value": p.axle_value}

    def _backfill(self, kwargs):
        """Fill recent days that PowerEngine didn't record (or only partly) from HA history, one day per callback."""
        if self.costbook is None or self.cfg is None:
            return
        days = self.costbook.days_to_backfill(self._today(), BACKFILL_DAYS)
        if not days:
            return
        plain, full = history_entities(self.cfg)
        units = {}
        for eid in plain:
            try:
                units[eid] = {"unit_of_measurement": self.get_state(eid, attribute="unit_of_measurement")}
            except Exception:
                units[eid] = {}
        self.log(f"Cost backfill: {len(days)} day(s) from HA history ({days[0]:%d %b} to {days[-1]:%d %b})")
        self._bf = {"days": days, "plain": plain, "full": full, "units": units, "added": 0, "errors": 0}
        self.run_in(self._backfill_day, 1)

    def _backfill_day(self, kwargs):
        job = self._bf
        if not job["days"]:
            n = self.costbook.revalue(**self._cost_params())
            failed = f"; {job['errors']} fetch(es) failed" if job["errors"] else ""
            self.log(f"Cost backfill done: {job['added']} half-hours added from history; "
                     f"{n} half-hours re-valued{failed}")
            self._refresh_months()
            self._measure()
            return
        day = job["days"].pop(0)
        tz = self.tz or timezone.utc
        start = datetime(day.year, day.month, day.day, tzinfo=tz).astimezone(timezone.utc)
        end = (datetime(day.year, day.month, day.day, tzinfo=tz) + timedelta(days=1)).astimezone(timezone.utc)
        timelines = {}
        for eid in job["plain"] + job["full"]:
            try:
                if eid in job["full"]:
                    rows = self.get_history(entity_id=eid, start_time=start - timedelta(days=1), end_time=end)
                else:
                    rows = self._history(eid, start, end)
                timelines[eid] = Timeline(rows, job["units"].get(eid))
            except Exception as err:
                job["errors"] += 1
                if job["errors"] <= 3:
                    self.log(f"Cost backfill: couldn't read {eid} for {day:%d %b} ({err!r})", level="WARNING")
        rec, kw, added = Recorder(), self._cost_params(), 0
        last = None
        try:
            stop = min(end + timedelta(seconds=30), datetime.now(timezone.utc) - timedelta(minutes=1))
            for r in replay(self.cfg, timelines, start, stop):
                hh = rec.add(r)
                last = r
                if hh is not None and self.costbook.add(hh, r, keep_existing=True, **kw):
                    added += 1
        except Exception as err:
            job["errors"] += 1
            self.log(f"Cost backfill failed for {day:%d %b}: {err!r}", level="WARNING")
        job["added"] += added
        if last is not None:
            self.log(f"Cost backfill: {day:%a %d %b}: {added} half-hours from history")
        self.run_in(self._backfill_day, 1)

    def _refresh_months(self, prune=False):
        if self.costbook is None:
            return
        try:
            if prune:
                self.costbook.prune(self._today())
            self._months = self.costbook.months(self._today())
            self._publish_costs()
            self._health()
        except Exception as err:
            self.log(f"Could not summarise costs: {err!r}", level="WARNING")

    def _publish_costs(self):
        if self.costbook is None or getattr(self, "mqtt", None) is None or not hasattr(self, "_published"):
            return
        for key, (state, attrs) in cost_entity_states(self.costbook, self._today(), self._months).items():
            self._publish_if_changed(key, state, attrs)

    def _logbook(self, message):
        """Write to the HA logbook.

        Only name + message: AppDaemon moves `entity_id` into a service target (logbook.log rejects it) and
        reserves `domain` as its own argument name.
        """
        try:
            self.call_service("logbook/log", name="PowerEngine", message=message)
        except Exception as err:
            self.log(f"Could not write to the logbook: {err!r}", level="WARNING")

    def _publish_if_changed(self, key, state, attrs):
        if self._published.get(key) != (state, attrs):
            self._published[key] = (state, attrs)
            self._publish_state(key, state, attrs)

    # --- forecasting and planning --------------------------------------------------

    def _role_entity(self, role):
        spec = (self.cfg.inputs if self.cfg else {}).get(role) or {}
        return spec.get("entity")

    def _learn_load(self, kwargs):
        """Start reading 14 days of house-load (and car) history from HA, one day per callback.

        A busy power sensor can log ~15,000 readings a day; asking for 14 days at once returns nothing,
        so history is fetched a day at a time in the background.
        """
        house, car = self._role_entity("house_load_power"), self._role_entity("ev_charge_power")
        if not house:
            self.log("Load history: no house-load input mapped yet", level="WARNING")
            return
        def unit(eid):
            try:
                return self.get_state(eid, attribute="unit_of_measurement") if eid else None
            except Exception:
                return None
        now = datetime.now(timezone.utc)
        self._hist_job = {"now": now, "days": list(range(HISTORY_DAYS, 0, -1)), "errors": 0,
                          "ids": {"house": house, "car": car}, "units": {"house": unit(house), "car": unit(car)},
                          "rows": {"house": [], "car": []}}
        self.run_in(self._learn_load_day, 1)

    def _history(self, eid, start, end):
        try:
            return self.get_history(entity_id=eid, start_time=start, end_time=end,
                                    minimal_response=True, no_attributes=True)
        except TypeError:                              # older AppDaemon without those options
            return self.get_history(entity_id=eid, start_time=start, end_time=end)

    def _learn_load_day(self, kwargs):
        job = self._hist_job
        if job["days"]:
            d = job["days"].pop(0)
            start = job["now"] - timedelta(days=d)
            end = start + timedelta(days=1)
            for key in ("house", "car"):
                eid = job["ids"][key]
                if not eid:
                    continue
                try:
                    job["rows"][key] += parse_history(self._history(eid, start, end), unit=job["units"][key])
                except Exception as err:
                    job["errors"] += 1
                    if job["errors"] == 1:
                        self.log(f"Load history: couldn't read {eid} for {start:%d %b} ({err!r})", level="WARNING")
            self.run_in(self._learn_load_day, 1)
            return
        house_rows, car_rows = job["rows"]["house"], job["rows"]["car"]
        self._hist_means = house_only_means(house_rows, car_rows, job["now"],
                                            bool(self.cfg.system.get("house_load_includes_ev", True)))
        failed = f" ({job['errors']} day(s) failed)" if job["errors"] else ""
        self.log(f"Load history from HA: {len(house_rows)} house readings, {len(car_rows)} car readings "
                 f"-> {len(self._hist_means)} half-hours{failed}")
        if not self._hist_means:
            self.log("Load history from HA was empty; retrying in an hour. "
                     "PowerEngine's own load record is still used.", level="WARNING")
            self.run_in(self._learn_load, 3600)
        self._rebuild_profile()

    def _record_load(self, r):
        """Add the current house-only load to PowerEngine's own half-hourly record."""
        if self.loadstore.add(r.now, r.house_power):
            try:
                self.loadstore.save()
            except OSError as err:
                self.log(f"Could not save the load record: {err}", level="WARNING")
            if r.now.minute < 30:                                   # rebuild once an hour
                self._rebuild_profile()

    def _rebuild_profile(self):
        means = {**self._hist_means, **self.loadstore.means}
        if not means:
            return
        self.profile = profile_from_means(means, datetime.now(timezone.utc), self.tz)
        self.log(f"Load profile: {self.profile.days:.1f} days ({len(self._hist_means)} half-hours from HA history, "
                 f"{len(self.loadstore.means)} recorded by PowerEngine)")
        self._plan_sig = None                                       # re-plan with the new profile

    def _solar_forecast(self):
        items = []
        for role in ("solar_forecast_today", "solar_forecast_tomorrow", "solar_forecast_day3"):
            eid = self._role_entity(role)
            if eid:
                items += self.get_state(eid, attribute="detailedForecast") or []
        return items

    def _maybe_replan(self, r):
        sig = (len(r.rates), r.rates[0].start if r.rates else None,
               tuple((w.start, w.end) for w in r.dispatches), r.axle_start, r.axle_end, r.free_start, r.free_end,
               self.profile.days if self.profile else None, json.dumps(self.cfg.safety, sort_keys=True),
               json.dumps(self.cfg.features, sort_keys=True))
        due = self._plan_time is None or (r.now - self._plan_time).total_seconds() >= REPLAN_SECONDS
        if sig == self._plan_sig and not due or r.battery_soc is None:
            return
        slots = build_slots(r, self._solar_forecast(), self.profile, self.tz)
        self.plan = make_plan(slots, r.battery_soc, self._params(r), r.now, self.tz,
                              auto_cheap=bool(self.cfg.features.get("auto_cheap_threshold", True)),
                              wear_p=self.cfg.safety.get("battery_wear_p", 2.0))
        self._plan_sig, self._plan_time = sig, r.now
        self._snapshot_plan(r.now)
        extra = {"load_profile_days": round(self.profile.days, 1) if self.profile else 0}
        for key, (state, attrs) in plan_entity_states(self.plan, extra).items():
            self._publish_if_changed(key, state, attrs)

    def _snapshot_plan(self, now):
        """Keep the first plan of each local day, to compare with what actually happened (Health tab)."""
        if self.costbook is None or self.plan is None:
            return
        tz = self.tz or timezone.utc
        day = now.astimezone(tz).date()
        if getattr(self, "_snap_day", None) == day:
            return
        self._snap_day = day
        try:
            if self.costbook.plan_snapshot(day) is None:
                start = datetime(day.year, day.month, day.day, tzinfo=tz)
                self.costbook.save_plan_snapshot(day, plan_snapshot(self.plan, start, start + timedelta(days=1)))
        except Exception as err:
            self.log(f"Could not save the plan snapshot: {err!r}", level="WARNING")

    # --- notifications (HA companion app) ------------------------------------------------

    def _notify(self, event, msg):
        """Send (key, title, message) if that kind of notification is on and it hasn't been sent already."""
        if not msg or self.cfg is None:
            return
        n = self.cfg.notifications
        key, title, message = msg
        now = datetime.now(timezone.utc)
        if not n["service"] or not n["events"].get(event) or not self.notifier.should_send(key, now):
            return
        try:
            self.call_service(n["service"].replace(".", "/", 1), title=title, message=message)
            self.notifier.mark(key, now)
            self.log(f"Notified: {title}")
        except Exception as err:
            self.log(f"Could not send a notification via {n['service']}: {err!r}", level="WARNING")

    def _watch_inputs(self, checks, required):
        now = datetime.now(timezone.utc)
        for key in required:
            status, message = checks.get(key, ("unmapped", "Not set"))
            if status == OK:
                if self._bad_since.pop(key, None) is not None:
                    self.notifier.clear(f"input:{key}")
                continue
            since = self._bad_since.setdefault(key, now)
            if (now - since).total_seconds() >= 15 * 60:
                role = ROLE_BY_KEY.get(key)
                self._notify("inputs", input_message(key, role.label if role else key, status, message))

    def _watch_events(self, r):
        if r.axle_start and r.axle_start > r.now:
            self._notify("axle", axle_message(r.axle_start, r.axle_end, r.now, self.tz))
        if r.free_start and r.free_start > r.now:
            self._notify("free_power", free_message(r.free_start, r.free_end, r.now, self.tz))

    # --- inverter writes (EEPROM wear) ---------------------------------------------------

    CONTROL_ROLES = ("timed_charge_start_hour", "timed_charge_start_minute", "timed_charge_end_hour",
                     "timed_charge_end_minute", "timed_charge_current", "timed_discharge_start_hour",
                     "timed_discharge_start_minute", "timed_discharge_end_hour", "timed_discharge_end_minute",
                     "timed_discharge_current", "timed_update_button", "storage_mode", "inverter_export_limit")

    def _watch_controls(self):
        """Count writes to the inverter's control entities by whatever controls it now (e.g. Predbat)."""
        for handle in self._write_listeners:
            try:
                self.cancel_listen_state(handle)
            except Exception:
                pass
        self._write_listeners = []
        if self.cfg is None:
            return
        for role in self.CONTROL_ROLES:
            eid = self._role_entity(role)
            if eid:
                self._write_listeners.append(self.listen_state(self._on_control_change, eid))

    def _on_control_change(self, entity, attribute, old, new, kwargs):
        if old == new or old in (None, "unknown", "unavailable") or new in (None, "unknown", "unavailable"):
            return
        self.writes.observed(self._today(), entity)

    def _count_would_writes(self, r, decision):
        try:
            p = self._params(r)
            events = self.write_model.step(r.now, decision, p.max_charge_kw * 1000, p.max_discharge_kw * 1000)
            self.writes.would(self._today(), len(events))
            if r.now.minute % 10 == 0 and r.now.second < CYCLE_SECONDS:     # save every 10 minutes
                self._publish_writes()
        except Exception as err:
            self.log(f"Could not count inverter writes: {err!r}", level="WARNING")

    def _publish_writes(self):
        try:
            self.writes.save()
            s = self.writes.summary(self._today())
            self._publish_state("diag_inverter_writes", s["would"]["per_day"] if s["would"]["per_day"] is not None
                                else "unknown", s)
        except Exception as err:
            self.log(f"Could not publish inverter writes: {err!r}", level="WARNING")

    def _track_slots(self, r):
        dt = (r.now - self._slots_last).total_seconds() if self._slots_last else 0.0
        self._slots_last = r.now
        charging = r.ev_state() == "charging"
        if self.slots.update(r.now, r.dispatches, r.completed_dispatches, charging, r.ev_power, min(dt, 300.0)):
            try:
                self.slots.save()
            except OSError as err:
                self.log(f"Could not save the slot record: {err}", level="WARNING")
            self._health()

    def _daily_summary(self, kwargs):
        if self.costbook is None:
            return
        today = self._today()
        s = self.costbook.summary(today - timedelta(days=1), today)
        if s:
            self._notify("daily", daily_message(s))

    def _health(self):
        if self.costbook is None or getattr(self, "mqtt", None) is None:
            return
        try:
            h = self.costbook.health(self._today(), getattr(self, "_checks", None))
            h["slots"] = self.slots.summary(datetime.now(timezone.utc), tz=self.tz)
            self._publish_state("diag_health", h["state"], h)
            self._notify("health", health_message(h))
        except Exception as err:
            self.log(f"Could not evaluate health: {err!r}", level="WARNING")

    def _sync_dashboard(self):
        target = os.path.join(os.path.dirname(self._save_path()), "dashboard.yaml")
        try:
            if sync_dashboard(target):
                self.log(f"Dashboard updated: {target} (refresh the PowerEngine dashboard to see it)")
        except OSError as err:
            self.log(f"Could not write the dashboard file {target}: {err}", level="WARNING")

    # --- saving from the config card -------------------------------------------------

    def _save_path(self):
        if self.cfg_path:
            return self.cfg_path
        for path in self.paths:
            root = os.path.dirname(os.path.dirname(path))   # e.g. /homeassistant
            if os.path.isdir(root):
                return path
        return self.paths[0]

    def _on_save(self, event_name, data, kwargs):
        new = data.get("config")
        user = self._user_name(data)
        try:
            if not isinstance(new, dict):
                raise ConfigError("no configuration received")
            _, backup = save_config(self._save_path(), new)
        except (ConfigError, OSError) as err:
            self.log(f"Config save by {user} rejected: {err}", level="WARNING")
            self.fire_event(RESULT_EVENT, ok=False, message=str(err))
            return
        changed = self._changes(self.cfg.raw if self.cfg else {}, new)
        self.log(f"Config saved by {user} ({changed}); backup: {backup or 'none (first save)'}")
        self._logbook(f"configuration saved by {user}: {changed}")
        self._reload()
        self.fire_event(RESULT_EVENT, ok=True, message=f"Saved. {changed}.")

    def _reload(self):
        """Re-read config.yaml in place and republish status (no app restart)."""
        self.cfg_error = None
        try:
            self.cfg, self.cfg_path = load_config(self.paths)
        except ConfigError as err:
            self.cfg, self.cfg_error = None, str(err)
        self._watch_controls()
        if self.cfg is not None and self.costbook is not None:
            fid = flow_id(self.cfg)
            if fid != self.costbook.flow_id:              # inputs that shape the flows changed: rebuild from history
                self.costbook.flow_id = fid
                self.log("Cost inputs changed; recent days will be rebuilt from HA history")
                self.run_in(self._backfill, 30)
        self._last_checks = None
        self._evaluate()
        self._cycle({})

    def _user_name(self, data):
        ctx = (data.get("metadata") or {}).get("context") or data.get("context") or {}
        uid = ctx.get("user_id")
        if uid:
            for person in (self.get_state("person") or {}).values():
                if (person.get("attributes") or {}).get("user_id") == uid:
                    return person["attributes"].get("friendly_name", uid)
        return uid or "unknown user"

    @staticmethod
    def _changes(old, new):
        o, n = old.get("inputs") or {}, new.get("inputs") or {}
        changed = sorted(k for k in set(o) | set(n) if o.get(k) != n.get(k))
        other = [k for k in set(old) | set(new) if k != "inputs" and old.get(k) != new.get(k)]
        parts = []
        if changed:
            labels = [ROLE_BY_KEY[k].label if k in ROLE_BY_KEY else k for k in changed]
            more = "…" if len(labels) > 6 else ""
            parts.append(f"{len(changed)} input(s) changed: " + ", ".join(labels[:6]) + more)
        if other:
            parts.append("also " + ", ".join(sorted(other)))
        return "; ".join(parts) or "no changes"

    # --- helpers -------------------------------------------------------------------

    def _ui_defaults(self):
        """Set dashboard preferences to their defaults once (HA and the broker keep them after that)."""
        path = os.path.join(os.path.dirname(self._save_path()), "ui.json")
        try:
            with open(path, encoding="utf-8") as fh:
                done = json.load(fh)
        except (OSError, ValueError):
            done = {}
        if not done.get("right_align"):
            self._publish(f"{BASE_TOPIC}/ui_right_align/set", "ON")      # right-aligned numbers by default
            done["right_align"] = True
            try:
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(done, fh)
            except OSError as err:
                self.log(f"Could not save dashboard defaults: {err}", level="WARNING")

    def _mqtt_api(self):
        try:
            api = self.get_plugin_api("MQTT")
        except Exception as err:  # plugin not configured
            api = None
            self.log(f"MQTT plugin error: {err}", level="WARNING")
        if api is None:
            self.log("MQTT plugin not configured in appdaemon.yaml; entities can't be published. "
                     "See docs/INSTALL.md, Step 3.", level="WARNING")
        return api

    def _publish(self, topic, payload):
        if not isinstance(payload, str):
            payload = json.dumps(payload, default=str)
        self.mqtt.mqtt_publish(topic, payload, qos=1, retain=True)

    def _publish_state(self, key, state, attributes=None):
        self._publish(f"powerengine/{key}/state", str(state))
        if attributes is not None:
            self._publish(f"powerengine/{key}/attributes", attributes)

    def _beat(self, kwargs):
        self._publish_state("diag_heartbeat", datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def _remove_entities(self):
        for topic, payload in removal_messages():
            self._publish(topic, payload)
        self.log("remove_entities is set: removed all PowerEngine entities. The app is now idle; "
                 "remove it in HACS or set remove_entities: false to bring them back.", level="WARNING")
