"""PowerEngine AppDaemon entry point.

A thin adapter between AppDaemon/Home Assistant and pe_core. Decision logic
belongs in pe_core so it can be tested offline.

Passive (the default) only monitors and simulates. Active writes the Solis timed-slot settings (and, if the
feature is on, EDF smart-charge requests) behind the handover guards, the pause switch and the daily write limit.
"""

import asyncio
import copy
import dataclasses
import inspect
import json
import os
import re
import shutil
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import appdaemon.plugins.hass.hassapi as hass

from pe_core import (
    __version__,
    bms,
    clock,
    damping,
    diagnostics,
    earlytarget,
    gridcheck,
    ramcontrol,
    rctest,
    releases,
    testwrite,
)
from pe_core import learn as learning
from pe_core import override as manual
from pe_core.activity import ActivityLog
from pe_core.adapters import registry
from pe_core.adapters.definition import load_definition
from pe_core.adapters.kraken import supplier_of
from pe_core.adapters.null import NULL as NULL_ADAPTERS
from pe_core.adapters.options import site_options
from pe_core.adapters.publish import ad_would_alter, select_publisher
from pe_core.adapters.solcast import ROLES as FORECAST_ROLES
from pe_core.certainty import Certainty
from pe_core.checks import OK, blocking, check, degraded, summarise
from pe_core.commands import from_pe_command, from_service
from pe_core.config import (
    DEFAULT_PATHS,
    LOCATION_LAG_H,
    NOTIFY_DEFAULT,
    ConfigError,
    Site,
    load_config,
    parse_config,
    required_roles,
    settings_catalogue,
    use_measured,
    uses_battery_pair,
)
from pe_core.control import KINDS, Write, readback_mismatches, release, writes_needed
from pe_core.costbook import KEEP_DAYS, MIN_MEASURE_DAYS, CostBook, cost_entity_states
from pe_core.costs import METHOD_VERSION, waterfall
from pe_core.dashboard import energy_flow_card, sync_dashboard
from pe_core.decide import cheap_limit, decide
from pe_core.demo.gate import DemoGate
from pe_core.demo.pack import load_pack as load_demo_pack
from pe_core.demo.world import DemoWorld
from pe_core.earlytarget import EarlyTargets
from pe_core.eeprom import BlockWriteModel, WriteLog, WriteModel
from pe_core.energy import Recorder
from pe_core.entities import (
    ENTITIES,
    RETIRED_ENTITIES,
    device_entity,
    device_ref_from_entity,
    solar_plant_entity,
    solar_plant_id_from_entity,
    validate_definitions,
)
from pe_core.equipment import EquipmentSettings
from pe_core.forecast import (
    LoadProfile,
    build_slots,
    half_hour_means,
    house_only_means,
    meter_corrected,
    parse_history,
    profile_from_means,
)
from pe_core.health import WINDOW_KEYS, overall, plan_snapshot, slot_snapshot
from pe_core.heatpump import HeatPumpSettings
from pe_core.history import chosen_plan, day_view
from pe_core.journal import WriteJournal, day_summary, is_staged
from pe_core.loadstore import LoadStore
from pe_core.lowwrite import Study as LowWriteStudy
from pe_core.modes import GUARDS, UNVERIFIED, effective_mode, guard_problems, guard_status
from pe_core.names import build_names, default_names, fill, neutral_names, set_current
from pe_core.notify import Notifier, axle_message, daily_message, free_message, health_message, input_message
from pe_core.optimiser import compare, optimise
from pe_core.planner import make_plan, params_from, plan_entity_states, slot_certainty_rows
from pe_core.readings import read
from pe_core.replay import Timeline, flow_id, history_entities, replay
from pe_core.roles import ROLE_BY_KEY, ROLES, catalogue, is_forbidden_control
from pe_core.schedule import forecast_writes, periods, settled, urgent
from pe_core.simhistory import History, months_wanted, parse_upload
from pe_core.simjob import SimContext, SimStore
from pe_core.simjob import run as sim_run
from pe_core.simulate import SimBattery
from pe_core.slots import SlotTracker
from pe_core.smartcharge import SmartCharger, ask_message, worth_asking
from pe_core.status import entity_states
from pe_core.store import coerce_flags, save_config, with_operation
from pe_core.tariff import overnight_window
from pe_core.verification import active_refusal
from pe_core.version import MIN_CARD_VERSION, installed_version
from pe_core.weather import Weather
from pe_core.wizard import wizard_info

HEARTBEAT_SECONDS = 60
DATA_GAP_GRACE_S = 180             # a reading missing this long or less keeps the last decision (_bridge_data_gap)
LOWWRITE_START = "02:40:00"       # after the Simulator has had its hour
LOWWRITE_SLICE = 3                # new days planned per pass (about a second each); more passes follow a minute apart
SIM_START = "01:30:00"            # after the midnight jobs (00:05-00:20), well before the morning
SIM_SLICE_SECONDS = 2.0           # work per callback, then hand AppDaemon back for a second
CONTROL_STRATEGY = "rolling"      # "rolling" | "block": decided by #44 (EEPROM writes) before Active ships
BATTERY_VOLTS = 52.0              # nominal, for converting power to the inverter's current settings
CYCLE_SECONDS = 30
STICK_WARMUP = timedelta(minutes=5)   # after a start, no mid-slot stickiness while the plan's inputs load
REPLAN_SECONDS = 300
HISTORY_DAYS = 14
BACKFILL_DAYS = 14
RECHECK_SECONDS = 300
BUTTON_DELAY_S = 3                # apply the inverter's window times this long after writing them (see _press_buttons)
INPUT_GRACE_SECONDS = 600         # inputs missing: keep the inverter's programmed windows this long before releasing
SAVE_EVENT = "pe_config_save"
RESULT_EVENT = "pe_config_result"
DEMO_EVENT, DEMO_RESULT_EVENT = "pe_demo", "pe_demo_result"
OVERRIDE_EVENT, OVERRIDE_RESULT_EVENT = "pe_override", "pe_override_result"
HISTORY_DAY_EVENT = "pe_history_day"
CONTROL_EVENT = "pe_set_control"          # fired by the handover scripts: {"operation": "active" | "passive"}
TEST_EVENT = "pe_test_write"      # supervised test writes, fired by the config card (admin only)
PAUSE_ENTITY = "switch.pe_ctl_pause"
NOT_SET_UP = "Not set up yet"                    # what the Mode and Health tiles say before there is a config


def _notice_id(key: str) -> str:
    return "powerengine_" + "".join(c if c.isalnum() else "_" for c in key)



def _real(writes) -> int:
    """Writes that reach the inverter (staged window times don't until the update button sends them)."""
    return sum(not is_staged(w.role) for w in writes)

class PowerEngine(hass.Hass):
    _demo = None                     # the demo day when `demo: <day>` is set in apps.yaml (see _demo_setup)

    # --- starting again inside the running app (the demo's start, day and exit) -------------------------------
    # AppDaemon keeps every timer and listener until they are cancelled, and an app object keeps its attributes. To
    # run initialize() again without doubling anything up, the app records which attributes initialize() and the
    # callbacks set (`_touched`) and which timers and listeners it registered (`_handles`), and _wipe() undoes both.

    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        touched = self.__dict__.get("_touched")
        if touched is not None:
            touched.add(name)

    @staticmethod
    def _resolved(handle):
        """The handle string behind what AppDaemon returned. AppDaemon 4.4 returns the string; newer versions return
        an asyncio Task/Future (when the call is made from the event loop) that resolves to it."""
        if isinstance(handle, asyncio.Future):
            if handle.done() and not handle.cancelled() and handle.exception() is None:
                return handle.result()
            return None
        return handle

    def _track(self, kind, handle, due=None):
        handles = self.__dict__.get("_handles")
        if handles is None or handle is None:
            return handle
        if kind == "timer":                             # forget one-off timers that have already run (bounded list)
            now = datetime.now(timezone.utc)
            handles[:] = [h for h in handles if not (h[0] == "timer" and h[2] is not None and h[2] < now)]
        if not isinstance(handle, asyncio.Future) and any(h[1] == handle for h in handles):
            return handle                                # already recorded (see below)
        if isinstance(handle, asyncio.Future):
            real = self._resolved(handle)
            if real is not None:
                if not any(h[1] == real for h in handles):
                    handles.append((kind, real, due))
            elif not handle.done():
                generation = self.__dict__.get("_wipes", 0)

                def record(fut, kind=kind, due=due):
                    real = self._resolved(fut)
                    live = self.__dict__.get("_handles")
                    if real is None or live is None:
                        return
                    if self.__dict__.get("_wipes", 0) != generation:     # registered before a wipe: cancel it now
                        cancel = {"timer": self.cancel_timer, "event": self.cancel_listen_event,
                                  "state": self.cancel_listen_state}[kind]
                        try:
                            cancel(real)
                        except Exception:
                            pass
                        return
                    if not any(h[1] == real for h in live):
                        live.append((kind, real, due))
                handle.add_done_callback(record)
            return handle
        handles.append((kind, handle, due))
        return handle

    def _untrack(self, handle):
        handles = self.__dict__.get("_handles")
        handle = self._resolved(handle) if isinstance(handle, asyncio.Future) else handle
        if handles is not None and handle is not None:
            handles[:] = [h for h in handles if h[1] != handle]

    def _install_tracking(self):
        """Wrap the scheduling and listening calls (once, on the instance) so their handles are kept in `_handles`."""
        def timed(orig):
            def wrapper(callback, delay, **kwargs):
                due = datetime.now(timezone.utc) + timedelta(seconds=float(delay))
                return self._track("timer", orig(callback, delay, **kwargs), due)
            return wrapper

        def tracked(kind, orig):
            def wrapper(callback, *args, **kwargs):
                return self._track(kind, orig(callback, *args, **kwargs))
            return wrapper

        def cancelling(orig):
            def wrapper(handle, *args, **kwargs):
                handle = self._resolved(handle) if isinstance(handle, asyncio.Future) else handle
                self._untrack(handle)
                return orig(handle, *args, **kwargs)
            return wrapper
        d = self.__dict__
        d["run_in"] = timed(self.run_in)
        for name in ("run_every", "run_daily"):
            d[name] = tracked("timer", getattr(self, name))
        d["listen_event"], d["listen_state"] = tracked("event", self.listen_event), tracked("state", self.listen_state)
        for name in ("cancel_timer", "cancel_listen_event", "cancel_listen_state"):
            d[name] = cancelling(getattr(self, name))

    def _timer_gone(self, handle):
        """True when AppDaemon says this timer is no longer scheduled (it ran, or was cancelled): cancelling it would
        only log "Invalid callback handle". Where AppDaemon can't say (4.4 has no timer_running), False."""
        check = getattr(self, "timer_running", None)
        if check is None:
            return False
        try:
            return check(handle) is False
        except Exception:
            return False

    def _wipe(self):
        """Undo a previous initialize(): stop and hand back the inverter, cancel every timer and listener it made, and
        delete every attribute it set (which also puts back the real get_state etc. that demo mode replaced)."""
        try:
            self.terminate()
        except Exception as err:
            self.log(f"Restart: terminate failed: {err!r}", level="WARNING")
        self.__dict__["_wipes"] = self.__dict__.get("_wipes", 0) + 1
        now = datetime.now(timezone.utc)
        cancel = {"timer": self.cancel_timer, "event": self.cancel_listen_event, "state": self.cancel_listen_state}
        done = set()
        for kind, handle, due in list(self.__dict__.get("_handles") or []):
            if handle in done:                              # once each: a second cancel is an "Invalid callback handle"
                continue
            done.add(handle)
            if kind == "timer" and due is not None and due <= now:
                self._untrack(handle)                       # already ran
                continue
            if kind == "timer" and self._timer_gone(handle):
                self._untrack(handle)
                continue
            try:
                cancel[kind](handle)
            except Exception:
                self._untrack(handle)
        for name in list(self.__dict__["_touched"]):
            self.__dict__.pop(name, None)
        self.__dict__["_touched"].clear()

    def initialize(self):
        if self.__dict__.get("_touched") is None:
            custom = self.args.get("settings_file")
            if not any(os.path.isfile(p) for p in ([custom] if custom else list(DEFAULT_PATHS))):
                # Only an app that isn't set up can switch in and out of the demo, so only then are attributes,
                # timers and listeners recorded for _wipe(). A configured app runs exactly as before.
                self.__dict__["_touched"], self.__dict__["_handles"] = set(), []
                self._install_tracking()
        else:
            self._wipe()
        self.log(f"PowerEngine {__version__} starting")
        self._started_at = datetime.now(timezone.utc)
        self.damper = damping.Damper()
        self._damp_restart(self._started_at)
        validate_definitions()

        # Optional override. Not "config_path": AppDaemon sets that arg itself.
        custom = self.args.get("settings_file")
        self.paths = [custom] if custom else list(DEFAULT_PATHS)
        self._real_paths = list(self.paths)
        self.cfg, self.cfg_path, self.cfg_error = None, None, None
        self._demo = None
        self._override = None                        # a manual override (_on_override), loaded below
        self._early = EarlyTargets()                 # early-target records (_early_target), saved below
        day = self.args.get("demo") or self._saved_demo_day()
        if day:
            self._demo_setup(str(day))
        try:
            self.cfg, self.cfg_path = load_config(self.paths)
        except ConfigError as err:
            self.cfg_error = str(err)
            self.log(f"Config problem: {err}", level="WARNING")
        if self.cfg is None and self.cfg_error is None:
            looked = ", ".join(self.paths)
            self.log(f"No config.yaml found (looked in {looked}); running unconfigured.")     # not a fault
        elif self.cfg is not None:
            self.log(f"Loaded config from {self.cfg_path}: {len(self.cfg.inputs)} inputs, "
                     f"{len(self.cfg.solar_plants)} solar plant(s)")
            self._add_site()

        try:                                           # the log from before this start (Health tab's log card)
            self.__dict__.get("_log_ring") or self.log("PowerEngine log starts")
            self._log_ring.load(self._log_path())
        except Exception as err:
            self.log(f"Could not read the saved log: {err!r}", level="WARNING")

        choice = self._publisher_choice()
        self.mqtt = None if choice == "direct" else self._mqtt_api(quiet=choice == "auto")
        self._publisher_obj = None
        if self._get_publisher() is None:
            return
        self.log(f"Entity publishing: {self._get_publisher().name}"
                 + (" (set 'Entity publishing' on the Config page to change it)" if choice == "auto" else ""))
        if self.cfg is not None and self.cfg.remove_entities:
            self._remove_entities()
            return

        for ent in ENTITIES:
            self._get_publisher().discover(ent, __version__)
        self._sync_solar_entities()
        self._sync_device_entities()
        self._retire_old_entities()
        self._ui_defaults()
        self._get_publisher().available(True)
        self._publish_state("diag_version", __version__)
        self._publish_state("diag_started", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        self._publish_state("map_catalogue", str(len(ROLES)), catalogue())
        self._publish_state("map_settings", str(len(settings_catalogue()["safety"])), settings_catalogue())
        self._last_checks = None
        self._published = {}
        self._decision, self._since = None, None
        self._no_data_since = None                 # when the readings last went missing (see DATA_GAP_GRACE_S)
        try:   # keep the activity log across restarts (it lives in the entity's attributes)
            saved = self.get_state("sensor.pe_state_activity", attribute="entries")
        except Exception:
            saved = None
        self.activity = ActivityLog(saved if isinstance(saved, list) else None)
        self.profile: LoadProfile | None = None
        self._hist_means: dict = {}
        self._hist_fixed: set = set()
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
        if self._demo:                                 # the app reads the clock the way the demo world does
            self.tz = self._demo_world().tz
        self._publish_names()                          # the adapters (the forecast needs tz) can now name themselves
        self.recorder = Recorder()
        self.notifier = Notifier(os.path.join(os.path.dirname(self._save_path()), "notifications.json"))
        self._bad_since = {}
        self.writes = WriteLog(os.path.join(os.path.dirname(self._save_path()), "inverter_writes.json"))
        self.write_model = WriteModel()
        self.block_model = BlockWriteModel()
        self._write_listeners = []
        self._watch_controls()
        self._publish_writes()
        self.slots = SlotTracker(os.path.join(os.path.dirname(self._save_path()), "costs", "slots.json"))
        self._slots_last = None
        self.smart = SmartCharger(os.path.join(os.path.dirname(self._save_path()), "costs", "smart_requests.json"))
        self._our_write, self._last_readings = None, None
        self._dashboard_readings_synced = False
        self._watch_ready_by()
        self.costbook, self._months, self.measured = None, [], None
        try:
            self.costbook = CostBook(os.path.join(os.path.dirname(self._save_path()), "costs"), self.tz)
            self.costbook.export_fallback = self._current_export_rate()
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
        self.listen_event(self._on_test, TEST_EVENT)
        self.listen_event(self._on_diag_request, diagnostics.REQUEST_EVENT)
        self.listen_event(self._on_set_control, CONTROL_EVENT)
        self.listen_event(self._on_sim_history, "pe_sim_history")
        self.listen_event(self._on_sim_settings, "pe_sim_settings")
        self._listen_for_commands()
        self._beat({})
        self.run_every(self._beat, "now+60", HEARTBEAT_SECONDS)
        self.run_every(lambda kwargs: self._evaluate(), f"now+{RECHECK_SECONDS}", RECHECK_SECONDS)
        self.run_every(self._cycle, f"now+{CYCLE_SECONDS}", CYCLE_SECONDS)
        self.run_in(self._learn_load, 5)                     # load profile from history, then daily
        self.run_daily(self._learn_load, "00:10:00")
        self.run_daily(lambda kwargs: (self._refresh_months(prune=True), self._measure()), "00:05:00")
        self.run_daily(self._daily_summary, "08:00:00")
        self.run_daily(self._sim_start, SIM_START)                  # tariff Simulator: heavy work, overnight only
        self.run_in(lambda kwargs: self._sim_publish(), 20)
        self.run_daily(self._lowwrite_start, LOWWRITE_START)        # low-write study: shadow only, changes nothing
        self.run_in(lambda kwargs: self._lowwrite_publish(), 25)
        self.run_every(self._clock_step, "now+45", 600)          # inverter clock drift; sync in Active
        self.run_every(self._publish_history, "now+60", 900)     # Plan history tab (today fills in as it goes)
        self.run_every(self._check_update, "now+120", 60)        # a new version installed: ask HA to restart us
        if not self._demo:                                       # (the demo makes no calls to the internet)
            self.run_every(self._release_check, "now+60", 300)   # a new version released: straight from GitHub
        self.run_every(self._log_step, "now+30", 60)             # Health tab's log card (saved every 5 minutes)
        self.listen_event(self._on_health_dismiss, "pe_health_dismiss")
        self.listen_event(self._on_demo, DEMO_EVENT)
        if not self._demo:
            self._early = EarlyTargets(os.path.join(self._real_dir(), "early_target.json"))
        if not self._demo:
            self._override = manual.load(self._override_file(), datetime.now(timezone.utc))
        self.listen_event(self._on_override, OVERRIDE_EVENT)
        self._history_date = None                            # a day picked with the card's date picker (Plan history)
        self.listen_event(self._on_history_day, HISTORY_DAY_EVENT)
        self.run_in(self._backfill, 90)                      # fill recent days from HA history (after load learning)
        self.run_daily(self._backfill, "00:20:00")           # and any day with gaps (e.g. restarts)
        self.log(f"Published {len(ENTITIES)} entities under the PowerEngine device")

    def terminate(self):
        if self._get_publisher() is not None:
            self._get_publisher().available(False)
        try:
            self._log_ring.save(self._log_path())
        except Exception:
            pass

    # --- PowerEngine's own log (Health tab) ------------------------------------------------

    def _log_path(self):
        return os.path.join(os.path.dirname(self._save_path()), "log.json")

    def _log_step(self, kwargs):
        ring = self.__dict__.get("_log_ring")
        if ring is None or not ring.changed:
            return
        self._publish_state("diag_log", len(ring.lines), ring.published())
        now = datetime.now(timezone.utc)
        last = getattr(self, "_log_saved", None)
        if last is None or (now - last).total_seconds() >= 300:
            try:
                ring.save(self._log_path())
                self._log_saved = now
            except OSError as err:
                super().log(f"Could not save the log: {err}", level="WARNING")

    # --- new releases, straight from GitHub (HACS only looks every few hours) ---------------------------

    def _release_check(self, kwargs):
        tags = self.__dict__.setdefault("_release_etags", {})
        texts = self.__dict__.setdefault("_release_texts", {})
        try:
            for repo in (releases.APP_REPO, releases.CARD_REPO):
                url = releases.raw_url(repo)
                text, tags[url] = releases.fetch_text(url, tags.get(url) if texts.get(url) else None)
                if text is not None:
                    texts[url] = text
            s = releases.summary(__version__, texts.get(releases.raw_url(releases.APP_REPO), ""),
                                 texts.get(releases.raw_url(releases.CARD_REPO), ""))
            s["checked"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            state = "available" if s["available"] else "up to date"
            if s["available"] and getattr(self, "_release_seen", None) != s["latest"]:
                self._release_seen = s["latest"]
                self.log(f"New version {s['latest']} released (running {__version__}, {s['behind']} behind)")
            self._publish_if_changed("diag_update", state, s)
        except Exception as err:
            now = datetime.now(timezone.utc)
            last = getattr(self, "_release_err_at", None)
            if last is None or (now - last).total_seconds() >= 3600:
                self._release_err_at = now
                self.log(f"Could not check GitHub for a new version: {err!r}", level="WARNING")

    # --- Health findings: dismissed ones stay hidden (the same finding; a new one shows) ---------------

    def _dismissed_path(self):
        return os.path.join(os.path.dirname(self._save_path()), "health_dismissed.json")

    def _dismissed(self) -> dict:
        d = self.__dict__.get("_dismissed_cache")
        if d is None:
            try:
                with open(self._dismissed_path(), encoding="utf-8") as fh:
                    d = json.load(fh)
            except (OSError, ValueError):
                d = {}
            self._dismissed_cache = d
        return d

    def _on_health_dismiss(self, event_name, data, kwargs):
        key = str((data or {}).get("key", ""))[:20]
        h = getattr(self, "_health_last", None) or {}
        f = next((x for x in h.get("all_findings", []) if x.get("key") == key), None)
        if f is None:
            return
        d = self._dismissed()
        d[key] = {"title": f.get("title"), "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        cutoff = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        for k in [k for k, v in d.items() if v.get("at", "") < cutoff]:
            del d[k]
        try:
            tmp = self._dismissed_path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(d, fh)
            os.replace(tmp, self._dismissed_path())
        except OSError as err:
            self.log(f"Could not save the dismissed finding: {err}", level="WARNING")
        self.log(f"Health: dismissed \"{f.get('title')}\" ({f.get('detail', '')[:120]})")
        self._health()

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
                    if state is None and role.group == "handover":       # AppDaemon lost track: ask HA
                        live = self._guard_state(spec["entity"])
                        if live not in (UNVERIFIED, "unknown", "unavailable", "None"):
                            state = {"state": live, "attributes": {}}
                checks[role.key] = check(role, spec, state)
            if uses_battery_pair(self.cfg) and "battery_power" in self.cfg.inputs:
                checks["battery_power"] = (OK, "Not used: the charging and discharging sensors are mapped")
        missing = blocking(checks, required)
        down = degraded(checks, required)
        if bool(down) != bool(getattr(self, "_degraded", [])):
            if down:
                self.log(f"Car charger inputs unavailable ({', '.join(down)}); control carries on with the car "
                         "assumed not charging", level="WARNING")
            else:
                self.log("Car charger inputs back")
        self._degraded = down
        self._watch_inputs(checks, required)
        guards, absent = guard_status(self.cfg, self._guard_state) if self.cfg is not None else ([], [])
        self._note_absent_guards(absent)
        paused = self.get_state(PAUSE_ENTITY) == "on"
        mode = effective_mode(self.cfg, self.cfg_error, missing_required=missing, guards=guards, paused=paused,
                              unverified=self._unverified())
        self._leave_active(getattr(self, "mode", None), mode, guards)
        prev = getattr(self, "mode", None)
        if mode.effective == "active" and (prev is None or prev.effective != "active"):
            self._damp_restart(datetime.now(timezone.utc))        # resumed, or went Active
        if getattr(self, "_paused", False) and not paused:          # resumed: allow a fresh day's worth of writes
            self.writes.set_base(self._today(), self.writes.own_today(self._today()))
            self._cap_base = self.writes.base(self._today())
        self.mode = mode
        self._guards, self._paused = guards, paused

        if self.cfg_error:
            overall = "error"
        elif self.cfg is None:
            overall = "unconfigured"
        else:
            overall = summarise(checks, required)

        signature = (overall, mode, tuple(guards), paused, tuple(sorted(checks.items())))
        if signature == self._last_checks:
            return
        self._last_checks = signature
        self._checks = {k: {"status": s, "message": m} for k, (s, m) in checks.items()}
        self._health()
        self._publish_state("diag_config_ok", "ON" if overall in ("ok", "warnings") else "OFF",
                            {"reason": self.cfg_error or overall, "file": self.cfg_path})
        self._publish_state("cfg_operation_mode", mode.configured)
        self._publish_state("state_operation_mode", mode.effective,
                            {"reason": mode.reason, "guards": guards or "all safe", "paused": paused,
                             "guards_absent": absent})
        self._publish_status()
        self._publish_state("map_config", overall, {
            "config": self.cfg.raw if self.cfg else {},
            "checks": {k: {"status": s, "message": m} for k, (s, m) in checks.items()},
            "required": required,
            "file": self.cfg_path,
            "save_path": self._save_path(),
            "error": self.cfg_error,
        })
        self.log(f"Inputs: {overall}; mode {mode.label} ({mode.reason})")

    # --- the monitoring cycle ------------------------------------------------------

    def _cycle(self, kwargs):
        """Read inputs, decide (Passive: would-do only), then publish entities that changed."""
        readings, decision = None, None
        if self._demo:
            self._demo_world().step()                      # the simulated home and battery move on a minute
        if self.cfg is not None and not self.cfg_error and (self.mode.effective == "unconfigured"
                                                            or getattr(self, "_guard_live", False)):
            self._guard_live = False
            # inputs missing (e.g. just after an HA restart) or a guard AppDaemon can't see: recheck every cycle, so
            # control resumes within a cycle of them coming back, not up to 5 minutes
            self._evaluate()
        self._release_if_still_missing()
        if self.cfg is not None and self.mode.effective != "unconfigured":
            try:
                readings = read(self.cfg, lambda eid: self.get_state(eid, attribute="all"), None,
                                self._tariff(), self._events(), self._ev(), self._forecast())
                self._record_load(readings)
                self._grid_check(readings)
                self._record_costs(readings)
                self._watch_events(readings)
                self._track_slots(readings)
                self._smart_step(readings)
                self._refresh_temps(readings.now)
                self._expire_override(readings.now)
                self._maybe_replan(readings)
                decision = self._bridge_data_gap(
                    decide(readings, self.cfg, self._decision, self.tz, plan=self.plan,
                           override=self._active_override()), readings.now)
                decision = self._early_target(readings, decision)
                self._note_command(decision)
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
            self._control(readings, decision)
        for key, (state, attrs) in entity_states(readings, self.mode, self.tz, decision, self._since).items():
            self._publish_if_changed(key, state, attrs)
        self._publish_override()
        if not self._dashboard_readings_synced and getattr(self, "_last_readings", None) is not None:
            self._dashboard_readings_synced = True     # now the real capacity, not the 18000 Wh fallback
            self._sync_dashboard()

    # --- manual override (pe_core/override.py, docs/plans/mode-override.md) -------------------------

    def _override_file(self):
        return os.path.join(self._real_dir(), "override.json")

    def _override_reply(self, ok, message):
        self.log(f"Override: {message}", level="INFO" if ok else "WARNING")
        self.fire_event(OVERRIDE_RESULT_EVENT, ok=ok, message=message)

    def _on_override(self, event_name, data, kwargs):
        """The card's override button (pe_override: set / clear). Only the event named pe_override is handled."""
        if event_name != OVERRIDE_EVENT:
            return
        data = data if isinstance(data, dict) else {}
        action, now = data.get("action"), datetime.now(timezone.utc)
        if action == "clear":
            if self._override is None:
                return self._override_reply(True, "No override was on.")
            self._set_override(None)
            self._override_reply(True, "Override cancelled: back to the plan.")
            return self._cycle(None)
        if action != "set":
            return self._override_reply(False, "The override action isn't understood.")
        if self._demo or self.cfg is None or self.mode.effective != "active":
            return self._override_reply(False, "An override works only while PowerEngine is Active. "
                                               "Nothing was changed.")
        window_end = None
        if self.plan is not None and getattr(self.plan, "windows", None):
            w = self.plan.windows[0]
            if w.get("end"):
                window_end = datetime.fromisoformat(w["end"])
        ov, why = manual.parse(data, now, window_end)
        if ov is None:
            return self._override_reply(False, why)
        self._set_override(ov)
        self._override_reply(True, f"Override on: {manual.describe(ov, self.tz)}.")
        self._cycle(None)

    def _active_override(self):
        """The override that is in force: only in Active (the plan and decisions both use this)."""
        return self._override if self.mode.effective == "active" else None

    def _mark_manual(self, slots, now):
        """The slots an override covers, from the half-hour running now until it ends, carry its action, so the plan
        is made around them. A grid event in progress or planned keeps its slot (it wins over an override)."""
        ov = self._active_override()
        if ov is None:
            return slots
        axle = bool(self.cfg.features.get("axle"))
        return [dataclasses.replace(s, manual=ov.mode)
                if s.end > now and (ov.until is None or s.start < ov.until) and not (axle and s.axle) else s
                for s in slots]

    def _set_override(self, ov):
        self._override = ov
        try:
            manual.save(self._override_file(), ov)
        except OSError as err:
            self.log(f"Could not save the override ({err}); it will not survive a restart.", level="WARNING")
        self._publish_override()

    def _expire_override(self, now):
        if manual.expired(self._override, now):
            self.log(f"Override ended ({manual.describe(self._override, self.tz)}): back to the plan.")
            self._set_override(None)

    def _publish_override(self):
        ov = self._override
        attrs = {"mode": None, "until": None, "set_at": None, "text": None} if ov is None else \
            {**ov.as_dict(), "text": manual.describe(ov, self.tz)}
        self._publish_if_changed("state_override", "none" if ov is None else ov.mode, attrs)

    def _early_target(self, r, decision):
        """The plan's charge target for this half-hour is reached (the decision would be a Hold until it ends): if
        there is time left, replan now from the live battery level so the best action takes over for the rest of the
        slot (#175). Looked at once per half-hour; every look is recorded, with why a hold stayed
        (pe_core/earlytarget.py)."""
        e = self._early
        reached = decision.details.get("reached") if decision.rule == "plan" else None
        if not isinstance(reached, dict) or not reached.get("slot"):
            e.note_decision(r.now, decision.action, decision.rule, None)
            return decision
        slot = reached["slot"]
        if not e.first_look(slot):
            return decision
        start = datetime.fromisoformat(slot)
        left = earlytarget.minutes_left(start, r.now)
        nxt = self.plan.slots[1] if self.plan is not None and len(self.plan.slots) > 1 else None
        rec = {"t": r.now.isoformat(timespec="seconds"), "slot": slot,
               "slot_end": (start + earlytarget.SLOT).isoformat(), "left_min": round(left, 1),
               "soc": r.battery_soc, "target": reached.get("target"),
               "price_p": round(r.import_rate * 100, 2) if r.import_rate is not None else None,
               "car_charging": r.ev_state() == "charging", "mode": self.mode.effective,
               "next_action": nxt.action if nxt else None,
               "next_price_p": round(nxt.slot.price * 100, 2) if nxt and nxt.slot.price is not None else None}
        why = earlytarget.evaluate(left, self.plan is not None)
        if why:
            rec["outcome"] = why
            e.record(rec, replanned=False)
            return decision
        try:
            self._maybe_replan(r, force=True)
            new = self._bridge_data_gap(decide(r, self.cfg, decision, self.tz, plan=self.plan,
                                               override=self._active_override()), r.now)
        except Exception as err:
            self.log(f"Early-target replan failed: {err!r}", level="WARNING")
            rec["outcome"] = "error"
            e.record(rec, replanned=False)
            return decision
        still = new.rule == "plan" and isinstance(new.details.get("reached"), dict)
        rec.update(outcome="replanned_still_hold" if still else "replanned_changed", new_action=new.action,
                   new_rule=new.rule, new_reason=new.reason[:90], new_target=new.target_soc)
        e.record(rec, replanned=True)
        self.log(f"Target reached with {left:.0f} min left: replanned, now {new.action} ({new.reason[:60]})")
        return new

    def _bridge_data_gap(self, decision, now):
        """A reading missing for a moment (the import rate went unavailable for one cycle on 4 Oct 2026, 08:21)
        keeps the last real decision for up to DATA_GAP_GRACE_S, instead of handing the inverter to Self-use and
        straight back (two RAM writes). A longer gap, or no earlier decision, decides as before."""
        if decision.rule != "no_data":
            self._no_data_since = None
            return decision
        prev = self._decision
        if self._no_data_since is None:
            self._no_data_since = now
        if prev is None or prev.rule in ("no_data", "unconfigured") \
                or (now - self._no_data_since).total_seconds() > DATA_GAP_GRACE_S:
            return decision
        return prev

    # --- cost accounting -------------------------------------------------------------

    def _today(self):
        return datetime.now(self.tz or timezone.utc).date()

    def _current_export_rate(self):
        spec = (self.cfg.inputs.get("export_rate") if self.cfg else None) or {}
        try:
            return float(spec["value"]) if "value" in spec else float(self.get_state(spec.get("entity")))
        except (TypeError, ValueError, KeyError):
            return None

    def _note_command(self, decision):
        """Record what PowerEngine is asking the inverter for this half-hour (only when it's in control)."""
        if self.mode.effective != "active" or decision is None:
            self.recorder.note(None, None)
            return
        kw = None
        if decision.action in ("grid_charge", "export"):
            rated = self._control_params()
            kw = decision.power_w / 1000 if decision.power_w else (
                rated.max_charge_kw if decision.action == "grid_charge" else rated.max_discharge_kw)
        self.recorder.note(decision.action, kw)

    def _record_costs(self, r):
        if self.costbook is None:
            return
        if r.export_rate:
            self.costbook.export_fallback = r.export_rate
        hh = self.recorder.add(r)
        if hh is None:
            return
        w = getattr(self, "_wlive", None)
        if w is not None:                              # for learning: outside and estimated battery temperature
            hh.temp_c = w.at(hh.start + timedelta(minutes=15))
            measured = self._temperature("battery_temperature")
            hh.tb_c = measured if measured is not None else (getattr(self, "_tb", None) or {}).get(
                learning.hour_of(hh.start))
        try:
            rec = self.costbook.add(hh, r, **self._cost_params())
            if rec is None:
                return
            if rec["v"].get("event"):
                self._refresh_months()
            self._publish_costs()
        except Exception as err:
            self.log(f"Cost accounting failed for {hh.start.isoformat()}: {err!r}", level="WARNING")

    def _params(self, readings=None, conversion=True):
        """Planner/simulation parameters, with the measured battery efficiency once there is enough data.

        conversion=False leaves out the learned inverter conversion losses: the cost accounting values the battery's
        own flows, where the round trip measured from them is the right one."""
        p = params_from(self.cfg, readings)
        m = getattr(self, "measured", None)
        if m and m.get("measured") and m.get("efficiency") and use_measured(self.cfg, "battery_round_trip"):
            p = dataclasses.replace(p, efficiency=m["efficiency"])
        if m and m.get("capacity_measured") and m.get("capacity_kwh") and use_measured(self.cfg, "battery_capacity"):
            p = dataclasses.replace(p, capacity_kwh=m["capacity_kwh"])
        over = self._learned_overrides(p)
        if not conversion:
            over.pop("efficiency", None)
        return dataclasses.replace(p, **over)

    def _control_params(self, readings=None):
        """What the inverter is asked for: the configured rates (the learned ones only shape the plan, so the
        controller keeps asking for the full rate and the learning can see if it's reached)."""
        return params_from(self.cfg, readings)

    def _learned_overrides(self, p) -> dict:
        """Planner parameters replaced by what PowerEngine has learned, where chosen and plausible."""
        lr = getattr(self, "learned", None)
        if lr is None or self.cfg is None:
            return {}
        out: dict = {}
        if lr.max_charge_kw and use_measured(self.cfg, "battery_max_charge_power") \
                and 0.5 * p.max_charge_kw <= lr.max_charge_kw <= 1.2 * p.max_charge_kw:
            out["max_charge_kw"] = lr.max_charge_kw
        if lr.max_discharge_kw and use_measured(self.cfg, "battery_max_discharge_power") \
                and 0.5 * p.max_discharge_kw <= lr.max_discharge_kw <= 1.2 * p.max_discharge_kw:
            out["max_discharge_kw"] = lr.max_discharge_kw
        f = self.cfg.features
        if f.get("learn_taper", True) and lr.taper:
            out["taper"] = lr.taper
        if f.get("learn_taper", True) and lr.dtaper:
            out["dtaper"] = lr.dtaper
        if f.get("learn_conversion", True) and lr.charge_conv and lr.discharge_conv:
            # the battery's own one-way efficiency (measured or configured) times the inverter's AC/DC conversion
            # each way: grid-to-grid, which is what arbitrage really gets
            eff = p.efficiency * (lr.charge_conv * lr.discharge_conv) ** 0.5
            if 0.7 <= eff <= 1.0:
                out["efficiency"] = round(eff, 4)
        if f.get("learn_reserve", True) and lr.reserve_soc is not None \
                and p.min_reserve_soc < lr.reserve_soc <= p.min_reserve_soc + 15:
            out["min_reserve_soc"] = lr.reserve_soc                    # only ever raises the floor
        if f.get("learn_export", True) and lr.export_kw is not None and lr.export_kw < p.export_limit_kw:
            out["export_limit_kw"] = lr.export_kw
        if f.get("learn_car", True) and lr.car_kw is not None and 1.0 <= lr.car_kw <= 22:
            out["ev_charger_kw"] = lr.car_kw
        return out

    # --- cold-battery caution --------------------------------------------------------------------------

    def _cold_settings(self) -> learning.ColdSettings:
        s = self.cfg.safety
        where = self.cfg.system.get("battery_location", "garage")
        lag = LOCATION_LAG_H.get(where, s.get("battery_temp_lag_h", 24.0))      # "custom": the setting
        return learning.ColdSettings(threshold_c=s.get("cold_caution_temp_c", 4.0),
                                     factor=s.get("cold_charge_pct", 50) / 100,
                                     release_c=s.get("cold_release_c", 3.0), lag_h=lag)

    def _temperature(self, role):
        """A mapped temperature input (°C), or None."""
        eid = self._role_entity(role)
        if not eid:
            return None
        try:
            return float(self.get_state(eid))
        except (TypeError, ValueError):
            return None

    def _cold_in_use(self):
        """(threshold, factor, learned?) the caution uses now."""
        c = self._cold_settings()
        lr = getattr(self, "learned", None)
        if self.cfg.features.get("cold_learning", True) and lr is not None:
            thr = lr.cold_threshold_c if lr.cold_threshold_c is not None else c.threshold_c
            fac = lr.cold_factor if lr.cold_factor is not None else c.factor
            return thr, fac, lr.cold_threshold_c is not None or lr.cold_factor is not None
        return c.threshold_c, c.factor, False

    def _refresh_temps(self, now):
        """Hourly: the last 3 days and next 3 of outside temperature (Open-Meteo), and the battery estimate."""
        f = self.cfg.features
        if not (f.get("cold_caution", True) or f.get("cold_learning", True)):
            self._tb, self._caution = {}, {}
            return
        last = getattr(self, "_temps_at", None)
        if last is not None and (now - last).total_seconds() < 3600:
            return
        self._temps_at = now
        w = getattr(self, "_wlive", None)
        if w is None:
            try:
                lat = float(self.get_state("zone.home", attribute="latitude"))
                lon = float(self.get_state("zone.home", attribute="longitude"))
            except (TypeError, ValueError):
                return
            w = self._wlive = Weather(os.path.join(self._sim_folder(), "weather_live.json"), lat, lon)
        try:
            w.refresh_recent()
        except Exception as err:
            self.log(f"Outside temperature not available (Open-Meteo): {err!r}", level="WARNING")
        local = self._temperature("outside_temperature")
        if local is not None:                             # your own sensor wins for the hours it has seen
            w.set_local(now, local)
        try:
            w.save()
        except OSError as err:
            self.log(f"Could not save temperatures: {err!r}", level="WARNING")
        self._cold_model(now)

    def _cold_model(self, now):
        w = getattr(self, "_wlive", None)
        if w is None:
            return
        c = self._cold_settings()
        outside = w.series(now - timedelta(days=4), now + timedelta(days=3))
        measured = self._temperature("battery_temperature")
        anchor = (learning.hour_of(now), measured) if measured is not None else None
        self._tb = learning.battery_temps(outside, c.lag_h, anchor=anchor)
        thr, _, _ = self._cold_in_use()
        self._caution = learning.caution_by_hour(self._tb, thr, c.release_c)
        tb_now = self._tb.get(learning.hour_of(now))
        self._publish_if_changed("diag_battery_temperature", tb_now if tb_now is not None else "unknown", {
            "outside_c": outside.get(learning.hour_of(now)), "caution_now": bool(self._caution.get(
                learning.hour_of(now))), "threshold_c": thr, "lag_h": c.lag_h,
            "measured": measured is not None, "location": self.cfg.system.get("battery_location", "garage"),
            "outside_source": "your sensor" if self._role_entity("outside_temperature") else "Open-Meteo forecast",
            "note": "the battery's own sensor now; estimated ahead" if measured is not None
            else "estimated from the outside temperature; it lags behind it"})
        self._plan_sig = None                                          # re-plan with the new temperatures

    def _apply_cold(self, slots, now):
        """Slow the planned charge rate in slots where the battery is expected to be cold."""
        if not self.cfg.features.get("cold_caution", True) or not getattr(self, "_caution", None):
            return slots, None
        thr, fac, was_learned = self._cold_in_use()
        starts = [s.start for s in slots]
        factors = learning.slot_factors(starts, self._caution, fac)
        slots = [dataclasses.replace(s, charge_factor=f) if f != 1.0 else s for s, f in zip(slots, factors,
                                                                                           strict=True)]
        return slots, learning.cold_summary(starts, factors, self._tb, now, thr, fac, was_learned, self.tz)

    def _learn(self):
        """Re-learn limits from the recorded half-hours (daily, and at start-up)."""
        if self.costbook is None or self.cfg is None:
            return
        try:
            p = params_from(self.cfg)
            halves = self.costbook.halves(self._today())
            self.learned = learning.learn(halves, p.max_charge_kw, p.max_discharge_kw, p.min_reserve_soc,
                                          p.export_limit_kw, self._cold_settings())
            lr, used = self.learned, self._params()
            thr, fac, cold_learned = self._cold_in_use()
            rows = [
                ("Max charge rate", f"{p.max_charge_kw:.2f} kW", lr.max_charge_kw, "kW", lr.charge_samples,
                 f"{used.max_charge_kw:.2f} kW"),
                ("Max discharge rate", f"{p.max_discharge_kw:.2f} kW", lr.max_discharge_kw, "kW",
                 lr.discharge_samples, f"{used.max_discharge_kw:.2f} kW"),
                ("Charge taper near full", "none", ", ".join(f"{int(a)}%+: {b:.0%}" for a, b in lr.taper) or None,
                 "", lr.taper_samples, ", ".join(f"{int(a)}%+: {b:.0%}" for a, b in used.taper) or "none"),
                ("Discharge taper near empty", "none",
                 ", ".join(f"<{int(a)}%: {b:.0%}" for a, b in lr.dtaper) or None, "", lr.dtaper_samples,
                 ", ".join(f"<{int(a)}%: {b:.0%}" for a, b in used.dtaper) or "none"),
                ("Inverter conversion (charge / sell)", "not counted",
                 f"{lr.charge_conv:.0%} / {lr.discharge_conv:.0%}" if lr.charge_conv and lr.discharge_conv else None,
                 "", lr.conv_samples, f"round trip {used.efficiency ** 2:.0%} grid to grid"),
                ("Reserve (where discharge stops)", f"{p.min_reserve_soc:g}%", lr.reserve_soc, "%",
                 lr.reserve_samples, f"{used.min_reserve_soc:g}%"),
                ("Export limit", f"{p.export_limit_kw:g} kW", lr.export_kw, "kW", lr.export_samples,
                 f"{used.export_limit_kw:g} kW"),
                ("Car charge rate", f"{p.ev_charger_kw:g} kW", lr.car_kw, "kW", lr.car_samples,
                 f"{used.ev_charger_kw:g} kW"),
                ("Cold caution below", f"{self._cold_settings().threshold_c:g} °C", lr.cold_threshold_c, "°C",
                 lr.cold_slow + lr.cold_fast, f"{thr:g} °C"),
                ("Cold charge rate", f"{self._cold_settings().factor:.0%}",
                 f"{lr.cold_factor:.0%}" if lr.cold_factor is not None else None, "", lr.cold_slow, f"{fac:.0%}"),
            ]
            table = [{"what": a, "configured": b, "learned": (f"{c:g} {u}".strip() if isinstance(c, float) else c)
                      if c is not None else "not yet", "samples": n, "in_use": e} for a, b, c, u, n, e in rows]
            n = sum(1 for r in table if r["learned"] != "not yet")
            self._publish_state("diag_learned", f"{n} learned", {"rows": table, "raw": lr.as_dict(),
                                                                  "cold_learned": cold_learned,
                                                                  "days": len({h["start"][:10] for h in halves})})
            if getattr(self, "_wlive", None) is not None:
                self._cold_model(datetime.now(timezone.utc))      # the learned threshold may have moved
        except Exception as err:
            self.log(f"Could not learn from recorded use: {err!r}", level="WARNING")

    def _measure(self, revalue=True):
        """Measure battery efficiency and system losses; publish them; re-value costs if the efficiency moved."""
        if self.costbook is None or self.cfg is None:
            return
        try:
            old = self.measured or {}
            before = old.get("efficiency")
            cap_before = old.get("capacity_kwh") if old.get("capacity_measured") and use_measured(
                self.cfg, "battery_capacity") else None
            self.measured = self.costbook.measure(self._today(), params_from(self.cfg).capacity_kwh)
            m = self.measured
            configured = params_from(self.cfg).efficiency
            eff = m["efficiency"] if m["measured"] and use_measured(self.cfg, "battery_round_trip") else configured
            self._publish_state("diag_battery_efficiency", round(eff * eff * 100, 1), {
                "measured": m["measured"], "one_way": round(eff, 4), "configured_one_way": configured,
                "measured_round_trip": round(m["rte"] * 100, 1) if m["measured"] else None,
                "use_measured": use_measured(self.cfg, "battery_round_trip"),
                "days": m["days"], "battery_in_kwh": m["battery_in"], "battery_out_kwh": m["battery_out"],
                "note": "measured over the last 30 days" if m["measured"]
                else f"estimated (configured figure) until {MIN_MEASURE_DAYS} full days are recorded"})
            self._publish_state("diag_system_losses", m.get("losses_yesterday") if m.get("losses_yesterday") is not None
                                else "unknown", {"average_kwh": m.get("losses_avg"),
                                                 "percent_yesterday": m.get("losses_pct_yesterday"),
                                                 "average_percent": m.get("losses_pct_avg"), "by_day": m["losses"]})
            if m["measured"]:
                self.log(f"Battery round trip measured at {m['rte'] * 100:.1f}% over {m['days']} days")
            self._publish_state("diag_battery_capacity", m["capacity_kwh"] if m.get("capacity_kwh") else "unknown", {
                "measured": m.get("capacity_measured"), "configured_kwh": params_from(self.cfg).capacity_kwh,
                "use_measured": use_measured(self.cfg, "battery_capacity"),
                "in_use_kwh": self._params().capacity_kwh,
                "samples": m.get("capacity_samples"), "max_charge_kw": m.get("max_charge_kw"),
                "max_discharge_kw": m.get("max_discharge_kw"), "min_soc_seen": m.get("min_soc")})
            cap_now = m.get("capacity_kwh") if m.get("capacity_measured") and use_measured(
                self.cfg, "battery_capacity") else None
            eff_moved = m["measured"] and use_measured(self.cfg, "battery_round_trip") and (
                before is None or abs(m["efficiency"] - before) > 0.002)
            cap_moved = cap_now is not None and (cap_before is None or abs(cap_now - cap_before) > 0.2)
            self._learn()
            if revalue and (eff_moved or cap_moved):
                n = self.costbook.revalue(**self._cost_params())
                self.log(f"Costs re-valued ({n} half-hours) with the measured battery efficiency/capacity")
                self._refresh_months()
        except Exception as err:
            self.log(f"Could not measure losses: {err!r}", level="WARNING")

    def _cost_params(self):
        p = self._params(conversion=False)
        return {"capacity": p.capacity_kwh, "eff": p.efficiency, "floor_soc": p.min_reserve_soc,
                "max_kw": p.max_discharge_kw, "includes_ev": p.hold_for_car, "axle_value": p.axle_value,
                "axle_plus_export": p.axle_plus_export}

    def _scenario_params(self):
        """The subset of _cost_params() that pe_core.costs.day_scenarios() (via day_summary) understands."""
        p = self._cost_params()
        return {"capacity": p["capacity"], "eff": p["eff"], "floor_soc": p["floor_soc"],
                "max_kw": p["max_kw"], "includes_ev": p["includes_ev"], "axle_value": p["axle_value"]}

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
        sp = self._scenario_params() if self.cfg is not None else None
        for key, (state, attrs) in cost_entity_states(self.costbook, self._today(), self._months, sp).items():
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
            if not self.__dict__.get("_load_history_noted"):        # once, and only information: not a fault
                self._load_history_noted = True
                self.log("Load history: no house-load input mapped yet")
            return
        def unit(eid):
            try:
                return self.get_state(eid, attribute="unit_of_measurement") if eid else None
            except Exception:
                return None
        now = datetime.now(timezone.utc)
        ids = {"house": house, "car": car, "grid": None, "check": None}
        if self.cfg.features.get("use_check_meter", True) and self._role_entity("grid_power_reference"):
            ids["grid"], ids["check"] = self._role_entity("grid_power"), self._role_entity("grid_power_reference")
        self._hist_job = {"now": now, "days": list(range(HISTORY_DAYS, 0, -1)), "errors": 0, "ids": ids,
                          "units": {k: unit(e) for k, e in ids.items()}, "rows": {k: [] for k in ids}}
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
            for key in job["ids"]:
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
        self._hist_fixed = set()
        if job["rows"].get("grid") and job["rows"].get("check"):
            def signed(key, role):
                flip = -1.0 if (self.cfg.inputs.get(role) or {}).get("invert") else 1.0
                return half_hour_means([(t, flip * v) for t, v in job["rows"][key]], job["now"])
            self._hist_means, self._hist_fixed = meter_corrected(
                self._hist_means, signed("grid", "grid_power"), signed("check", "grid_power_reference"))
            self.log(f"Load history: {len(self._hist_fixed)} half-hours corrected by the check meter")
        failed = f" ({job['errors']} day(s) failed)" if job["errors"] else ""
        self.log(f"Load history from HA: {len(house_rows)} house readings, {len(car_rows)} car readings "
                 f"-> {len(self._hist_means)} half-hours{failed}")
        if not self._hist_means:
            self.log("Load history from HA was empty; retrying in an hour. "
                     "PowerEngine's own load record is still used.", level="WARNING")
            self.run_in(self._learn_load, 3600)
        self._rebuild_profile()

    def _grid_check(self, r):
        """Compare the inverter's grid meter with a second one, when one is mapped (Configuration: Grid and house)."""
        if r.grid_ref_power is None:
            return
        try:
            gc = getattr(self, "_gridcheck", None)
            if gc is None:
                gc = self._gridcheck = gridcheck.GridCheck()
            inv = r.grid_power_inverter
            gc.add(str(self._today()), r.now, r.battery_power, inv, r.grid_ref_power, r.grid_ref_at)
            last = getattr(self, "_gridcheck_published", None)
            if last is None or (r.now - last).total_seconds() >= 60:
                self._gridcheck_published = r.now
                diff = None if inv is None else round(inv - r.grid_ref_power)
                attrs = {"inverter_meter_w": inv, "reference_meter_w": r.grid_ref_power, "in_use": r.grid_source,
                         "battery_w": r.battery_power, "battery_state": gridcheck.battery_state(r.battery_power),
                         **gc.summary()}
                self._publish_state("diag_grid_check", "unknown" if diff is None else diff, attrs)
        except Exception as err:
            self.log(f"Grid meter cross-check failed: {err!r}", level="WARNING")

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
        # PowerEngine's own record wins, except where the history was corrected by the check meter (the record from
        # before the correction was live holds the uncorrected figure)
        fixed = getattr(self, "_hist_fixed", set())
        means = {**self._hist_means, **{s: w for s, w in self.loadstore.means.items() if s not in fixed}}
        if not means:
            return
        self.profile = profile_from_means(means, datetime.now(timezone.utc), self.tz)
        self.log(f"Load profile: {self.profile.days:.1f} days ({len(self._hist_means)} half-hours from HA history, "
                 f"{len(self.loadstore.means)} recorded by PowerEngine)")
        self._plan_sig = None                                       # re-plan with the new profile

    def _site(self) -> Site:
        """Which plant this home has (config.yaml's `site:`; today's plant until the config is read)."""
        return self.cfg.site if self.cfg else Site()

    def _unverified(self):
        """Why Active is refused for the site's inverter (a definition not yet proven on real hardware), or None.
        Not checked in a demo: nothing there is controlled."""
        if self._demo or self.cfg is None:
            return None
        site = self._site()
        return active_refusal(site.inverter, site.inverter_firmware)

    def _adapter(self, attr, kind, key, *args):
        """The adapter the site names for `key`, built once and rebuilt when the site changes it. "none" is the null
        adapter (reads nothing). `attr` is where it is kept."""
        name = getattr(self._site(), key)
        held = getattr(self, attr, None)
        if held is None or held[0] != name:
            factory = NULL_ADAPTERS[key] if name == "none" else registry.get(kind, name)
            held = (name, factory(self._role_entity, *args))
            setattr(self, attr, held)
        return held[1]

    def _forecast(self):
        """The generation forecast adapter (the site's: Solcast, or none)."""
        return self._adapter("_forecast_adapter", "forecast", "forecast", self.tz)

    def _solar_forecast(self):
        """The forecast as neutral half-hourly points, from every mapped forecast role."""
        fc = self._forecast()
        return fc.points(fc.read(lambda eid, attr: self.get_state(eid, attribute=attr),
                                 [self._role_entity(role) for role in FORECAST_ROLES]))

    def _car_idle(self, r) -> bool:
        """The last smart slot passed with the car drawing nothing (it's full)."""
        try:
            return bool(self.slots.car_idle(r.now)) if getattr(self, "slots", None) else False
        except Exception:
            return False

    def _maybe_replan(self, r, force=False):
        r.car_idle = self._car_idle(r)
        sig = (len(r.rates), r.rates[0].start if r.rates else None,
               tuple((w.start, w.end) for w in r.dispatches), r.axle_start, r.axle_end, r.free_start, r.free_end,
               self.profile.days if self.profile else None, json.dumps(self.cfg.safety, sort_keys=True),
               json.dumps(self.cfg.features, sort_keys=True), r.ev_state(), r.car_idle,  # car starts/stops: re-plan
               json.dumps(self._active_override().as_dict()) if self._active_override() else None)  # set / cancelled
        due = self._plan_time is None or (r.now - self._plan_time).total_seconds() >= REPLAN_SECONDS
        if (sig == self._plan_sig and not due and not force) or r.battery_soc is None:
            return
        cert = Certainty(self.slots.slots, self.tz)
        window = overnight_window(self.costbook.cheap_history) if self.costbook is not None else set()
        slots = build_slots(r, self._solar_forecast(), self.profile, self.tz, certainty=cert,
                            first_seen={k: v.get("first_seen") for k, v in self.slots.slots.items()},
                            overnight=window, whole_house=bool(self.cfg.features.get("slots_whole_house", True)))
        slots, cold = self._apply_cold(slots, r.now)
        slots = self._mark_manual(slots, r.now)
        strategy = "optimiser" if self.cfg.features.get("optimised_plan", True) else "rules"
        first_h = self._first_slot_hours(slots, r.now)
        self.plan = make_plan(slots, r.battery_soc, self._params(r), r.now, self.tz,
                              auto_cheap=bool(self.cfg.features.get("auto_cheap_threshold", True)),
                              wear_p=self.cfg.safety.get("battery_wear_p", 2.0), strategy=strategy,
                              prev_action=self._decision.action if getattr(self, "_decision", None) else None,
                              stick=0.0 if force else self._mid_slot_stick(r.now), first_h=first_h)
        self._plan_sig, self._plan_time = sig, r.now
        self._snapshot_plan(r.now)
        self._record_ran(r.now)
        extra = {"load_profile_days": round(self.profile.days, 1) if self.profile else 0,
                 "slot_certainty": slot_certainty_rows(slots, self.tz), "certainty": cert.summary(), "cold": cold}
        if self.plan.strategy != "optimiser":
            try:                                          # the optimiser, for comparison only
                p = self._params(r)
                opt = optimise(slots, r.battery_soc, p, wear=p.wear_p / 100, first_h=first_h)
                extra["optimiser"] = compare(self.plan, opt, p)
            except Exception as err:
                self.log(f"Optimiser comparison failed: {err!r}", level="WARNING")
        try:                                              # the writes the plan implies, for the plan chart
            sm = self._slot_map() if self._control_method() != "ram_remote" else None
            if sm is None and self.cfg is not None and self._control_method() == "ram_remote":
                extra["writes_forecast"], extra["writes_forecast_24h"] = {}, 0
            if sm and self.plan is not None:
                ents = self._slot_keys(sm)
                have = self._inverter().read(ents)
                pc = self._control_params(r)
                now_local = r.now.astimezone(self.tz) if self.tz else r.now
                wf = forecast_writes(self.plan.slots, now_local, self.tz, have, BATTERY_VOLTS,
                                     pc.max_charge_kw * 1000, pc.max_discharge_kw * 1000)
                extra["writes_forecast"], extra["writes_forecast_24h"] = wf, sum(wf.values())
        except Exception as err:
            self.log(f"Could not forecast inverter writes: {err!r}", level="WARNING")
        for key, (state, attrs) in plan_entity_states(self.plan, extra).items():
            self._publish_if_changed(key, state, attrs)

    def _snapshot_plan(self, now):
        """Keep the first plan of each local day (Health tab accuracy), and the first plan of each hour (Plan
        history tab)."""
        if self.costbook is None or self.plan is None:
            return
        tz = self.tz or timezone.utc
        local = now.astimezone(tz)
        day, hour = local.date(), f"{local.hour:02d}:00"
        if getattr(self, "_snap_hour", None) == (day, hour):
            return
        self._snap_hour = (day, hour)
        try:
            start = datetime(day.year, day.month, day.day, tzinfo=tz)
            end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=tz)
            snap = plan_snapshot(self.plan, start, end)
            if self.costbook.plan_snapshot(day) is None:
                self.costbook.save_plan_snapshot(day, snap)
            if hour not in self.costbook.plan_history(day):
                self.costbook.save_plan_history(day, hour, snap)
        except Exception as err:
            self.log(f"Could not save the plan snapshot: {err!r}", level="WARNING")

    def _record_ran(self, now):
        """Keep the plan that actually ran (Plan history, "As run"): the plan's slot for the half-hour now running,
        written when it first appears and again if a replan changes it."""
        if self.costbook is None or self.plan is None:
            return
        try:
            tz = self.tz or timezone.utc
            local = now.astimezone(tz)
            start = local.replace(minute=local.minute - local.minute % 30, second=0, microsecond=0)
            ps = next((x for x in self.plan.slots if x.slot.start == start), None)
            if ps is None:
                return
            slot = slot_snapshot(ps)
            window = next(({k: w[k] for k in WINDOW_KEYS if k in w} for w in getattr(self.plan, "windows", []) or []
                           if w.get("start") and w.get("end")
                           and datetime.fromisoformat(w["start"]) <= start < datetime.fromisoformat(w["end"])), None)
            if getattr(self, "_ran_last", None) == (slot, window):
                return
            self.costbook.record_ran(local.date(), slot, window)
            self._ran_last = (slot, window)
        except Exception as err:
            self.log(f"Could not save the plan that ran: {err!r}", level="WARNING")

    def _on_history_day(self, event_name, data, kwargs):
        """The card's date picker (pe_history_day {date}): show that day, if the app can still hold it."""
        if event_name != HISTORY_DAY_EVENT or self.costbook is None:
            return
        try:
            day = date.fromisoformat(str((data or {}).get("date")))
        except ValueError:
            return
        today = datetime.now(timezone.utc).astimezone(self.tz or timezone.utc).date()
        if not today - timedelta(days=KEEP_DAYS) <= day <= today:
            return
        self._history_date = day
        self._publish_history()

    def _publish_history(self, *args, **kwargs):
        """The Plan history tab: the chosen day and plan against what happened."""
        if self.costbook is None or self._get_publisher() is None:
            return
        try:
            tz = self.tz or timezone.utc
            now = datetime.now(timezone.utc)
            today = now.astimezone(tz).date()
            day = self._history_date or today - timedelta(days=1)
            label, snap = chosen_plan(self.costbook.plan_snapshot(day), self.costbook.ran_plan(day))
            view = day_view(day, self.costbook.day_records(day), snap, label, tz, now)
            recorded = self.costbook.recorded_days()
            view["earliest"] = min(recorded[0], day.isoformat()) if recorded else day.isoformat()  # for the picker
            view["latest"] = today.isoformat()
            self._publish_state("plan_history", day.isoformat(), view)
        except Exception as err:
            self.log(f"Could not build the plan history: {err!r}", level="WARNING")

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
            if n["service"] == NOTIFY_DEFAULT:
                self.call_service("persistent_notification/create", title=title, message=message,
                                  notification_id=_notice_id(key))
            else:
                self.call_service(n["service"].replace(".", "/", 1), title=title, message=message)
            self.notifier.mark(key, now)
            self.log(f"Notified: {title}")
        except Exception as err:
            self.log(f"Could not send a notification via {n['service']}: {err!r}", level="WARNING")

    def _clear_notice(self, key):
        """The problem behind a notification is over: allow it again, and take it out of HA's notification area."""
        self.notifier.clear(key)
        if self.cfg is not None and self.cfg.notifications["service"] == NOTIFY_DEFAULT:
            try:
                self.call_service("persistent_notification/dismiss", notification_id=_notice_id(key))
            except Exception as err:
                self.log(f"Could not dismiss a notification: {err!r}", level="WARNING")

    def _guard_state(self, eid):
        """A guard's state. AppDaemon's copy of HA's states can miss an entity that was re-created after it started
        (Predbat restarting does this), so when it has nothing, ask Home Assistant directly. If that can't be
        checked either, the guard is "unverified", which never counts as safe."""
        state = self.get_state(eid)
        if state is not None:
            return state
        try:
            live = str(self.render_template(f"{{{{ states('{eid}') }}}}")).strip()
        except Exception as err:
            self.log(f"Couldn't check {eid} with Home Assistant: {err!r}", level="WARNING")
            return UNVERIFIED
        self._guard_live = True                       # AppDaemon can't tell us when it changes: keep rechecking
        return live or UNVERIFIED

    def _note_absent_guards(self, absent):
        """Guard entities HA doesn't have (e.g. Predbat not connected) count as safe; say so once, and when they're
        back."""
        before = set(getattr(self, "_absent_guards", ()))
        now = set(absent)
        if now - before:
            names = ", ".join(sorted(now - before))
            self.log(f"Handover guard not available ({names}); treated as safe: it can't be controlling anything",
                     level="WARNING")
            self._notify("health", ("guard:absent", "PowerEngine: handover guard not available",
                                    f"{names} isn't in Home Assistant right now (e.g. Predbat not connected). "
                                    "PowerEngine treats it as safe, since it can't be controlling the inverter, "
                                    "and carries on."))
        if before and not now:
            self.log("Handover guards available again")
            self._clear_notice("guard:absent")
        self._absent_guards = now

    def _watch_inputs(self, checks, required):
        now = datetime.now(timezone.utc)
        for key in required:
            status, message = checks.get(key, ("unmapped", "Not set"))
            if status == OK:
                if self._bad_since.pop(key, None) is not None:
                    self._clear_notice(f"input:{key}")
                continue
            since = self._bad_since.setdefault(key, now)
            if (now - since).total_seconds() >= 15 * 60:
                role = ROLE_BY_KEY.get(key)
                self._notify("inputs", input_message(key, fill(role.label) if role else key, status, message))

    def _watch_events(self, r):
        if r.axle_start and r.axle_start > r.now:
            self._notify("axle", axle_message(r.axle_start, r.axle_end, r.now, self.tz))
        if r.free_start and r.free_start > r.now:
            self._notify("free_power", free_message(r.free_start, r.free_end, r.now, self.tz))

    # --- inverter writes (EEPROM wear) ---------------------------------------------------

    CONTROL_ROLES = ("timed_charge_start_hour", "timed_charge_start_minute", "timed_charge_end_hour",
                     "timed_charge_end_minute", "timed_charge_current", "timed_discharge_start_hour",
                     "timed_discharge_start_minute", "timed_discharge_end_hour", "timed_discharge_end_minute",
                     "timed_discharge_current", "timed_update_button", "storage_mode", "inverter_export_limit",
                     "inverter_clock_sync")

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
                self._write_listeners.append(self._listen_state(self._on_control_change, eid))
        for eid in [self._role_entity(key) for key, _ in GUARDS] + [PAUSE_ENTITY]:
            if eid:                                   # re-check the mode as soon as a guard or pause changes
                self._write_listeners.append(self._listen_state(self._on_guard_change, eid))
        self._write_listeners = [h for h in self._write_listeners if h is not None]

    def _listen_state(self, callback, entity_id):
        """listen_state, except for the demo world's entities: they live inside the app, not in Home Assistant, so
        AppDaemon would warn that they don't exist, and nothing could ever change them from outside anyway."""
        if self._demo and self._demo_world().is_demo(entity_id):
            return None
        return self.listen_state(callback, entity_id)

    def _on_guard_change(self, entity, attribute, old, new, kwargs):
        if old != new:
            self._evaluate()

    def _on_control_change(self, entity, attribute, old, new, kwargs):
        if old == new or old in (None, "unknown", "unavailable") or new in (None, "unknown", "unavailable"):
            return
        if is_staged(entity):                    # window times stay in HA until the update button sends them
            return
        self.writes.observed(self._today(), entity)

    def _count_would_writes(self, r, decision):
        try:
            p = self._control_params(r)
            events = self.write_model.step(r.now, decision, p.max_charge_kw * 1000, p.max_discharge_kw * 1000)
            self.writes.would(self._today(), len(events))
            block_end = None
            if self.plan is not None and self.plan.windows and self.plan.windows[0]["action"] == decision.action:
                block_end = datetime.fromisoformat(self.plan.windows[0]["end"])
            events = self.block_model.step(r.now, decision, p.max_charge_kw * 1000, p.max_discharge_kw * 1000,
                                           block_end, self.tz)
            self.writes.would(self._today(), len(events), "would_block")
            slots = self._slot_map()
            if slots and self.plan is not None:                 # the three-slot strategy on a virtual inverter
                now_local = r.now.astimezone(self.tz) if self.tz else r.now
                virt = getattr(self, "_virtual", None)
                if virt is None:
                    virt = self._virtual = self._inverter().read(self._slot_keys(slots))
                want, ws = self._inverter().slot_writes(
                    periods(self.plan.slots, now_local, self.tz, decision.action), virt, now_local,
                    decision.action, decision.power_w, p.max_charge_kw * 1000, p.max_discharge_kw * 1000)
                for w in ws:
                    if w.kind != "button":
                        virt[w.role] = w.value
                self.writes.would(self._today(), len(ws), "would_slots")
            if r.now.minute % 10 == 0 and r.now.second < CYCLE_SECONDS:     # save every 10 minutes
                self._publish_writes()
                self._publish_writes_today()
        except Exception as err:
            self.log(f"Could not count inverter writes: {err!r}", level="WARNING")

    # --- inverter control (Active mode; previewed in Passive) --------------------------------

    def _inverter(self):
        """The inverter adapter the site names (from its definition, for the site's firmware). Rebuilt when the
        inverter or its firmware changes."""
        site = self._site()
        key = (site.inverter, site.inverter_firmware)
        held = getattr(self, "_inv", None)
        if held is None or held[0] != key:
            held = (key, registry.get("inverter", site.inverter)(self, self._role_entity, BATTERY_VOLTS,
                                                                 firmware=site.inverter_firmware))
            self._inv = held
        return held[1]

    def _tariff(self):
        """The tariff adapter: the site's, or (site tariff "auto") Kraken's EDF or Octopus, told apart by the rate
        sensor's integration."""
        tariff = getattr(self, "_tariff_adapter", None)
        name = self._site().tariff
        if name == "auto":
            name = supplier_of(self._role_entity("import_rate_now") if self.cfg else None)
        if tariff is None or tariff.name != name:  # rebuilt if the site or the rate sensor moves supplier
            tariff = self._tariff_adapter = registry.get("tariff", name)(self._role_entity)
        return tariff

    def _ev(self):
        """The car charger adapter the site names (Zappi, or none)."""
        return self._adapter("_ev_adapter", "ev", "ev_charger")

    def _names(self):
        """What this user's supplier and devices are called, from the adapters (neutral words before the config is
        read). Also what the pure functions read for their texts."""
        if self.cfg is None:
            names = neutral_names()
        else:
            names = build_names(self._tariff(), self._ev(), self._forecast(), self._inverter(), self._events())
        set_current(names)
        return names

    def _demo_days_preview(self):
        """The demo days the welcome offers, titled as the demo will show them (its own names, e.g. the event
        provider's, which the unconfigured app has no adapters to know), so the welcome and the banner agree."""
        try:
            names = default_names()
            return [{"key": k, "title": fill(v.get("title", k), names)} for k, v in load_demo_pack()["days"].items()]
        except Exception:
            return []

    def _publish_names(self):
        """The version sensor carries the names map (a small attribute; the card fills its placeholders from it)."""
        attrs = {"names": self._names(), "min_card_version": MIN_CARD_VERSION,
                 "setup": "configured" if self._real_config_exists() else "unconfigured",
                 "demo": self._demo_info() if self._demo else None}
        if attrs["setup"] == "unconfigured" and not self._demo:
            attrs["demo_days"] = self._demo_days_preview()
        attrs.update(self._site_attributes())
        self._publish_state("diag_version", __version__, attrs)

    def _site_attributes(self):
        """What the card's "Your system" block reads (compact: the version sensor's attributes must stay small):
        `site` (the current choices), `site_options` (per key: {id, name, status, firmware_variants}),
        `firmware_detected` (what the inverter reports, when its definition names an entity for that, else null) and
        `retest_required` (the site's inverter or firmware changed and the supervised tests are still to be re-run),
        and `wizard` (what the card's setup wizard looks for: pe_core/wizard.py)."""
        try:
            detected = self._inverter().firmware_detected()
        except Exception:
            detected = None
        out = {"site": self._site().as_dict(), "site_options": site_options(), "wizard": wizard_info(),
               "firmware_detected": detected, "retest_required": self._retest_required()}
        if self.cfg is not None and self.cfg.devices:            # read-only devices (M1); absent for one inverter
            out["devices"] = [{"id": d.id, "name": d.name, "adapter": d.adapter, "control": d.control,
                               "inputs": sorted(d.inputs)} for d in self.cfg.devices]
        return out

    def _events(self):
        """The grid-event adapter the site names (Axle, or none)."""
        return self._adapter("_events_adapter", "event", "events")

    # --- the site: which plant this home has ---------------------------------------------------

    def _add_site(self):
        """A real config with no `site:` gets one describing how PowerEngine has always run (Solis, Zappi, tariff
        detected, Solcast, Axle), saved through the usual save-with-backup path. Never in demo mode, never with no
        real config (nothing to write), and once only: a config that has a `site` is left alone. If the save fails the
        app carries on with the same choices in memory and tries again at the next start.
        The parts are not left out ("none") when their inputs are unmapped: the adapters read nothing from an
        unmapped role either way, but "none" would change the words in user text (the names map), so only the site's
        owner picks it."""
        cfg = self.cfg
        if cfg is None or self._demo or "site" in cfg.raw or not self._real_config_exists():
            return
        if self.cfg_path not in self._real_paths:
            return
        assumed = False
        try:
            site = Site()
            inverter = registry.get("inverter", site.inverter)(self, self._role_entity, BATTERY_VOLTS)
            firmware = inverter.firmware_detected()
            if firmware is None:                           # not readable: the variant PowerEngine has always used
                firmware = (load_definition(site.inverter).get("firmware") or {}).get("default")
                assumed = firmware is not None
            site = dataclasses.replace(site, inverter_firmware=firmware)
        except Exception as err:
            self.log(f"Site: could not detect the inverter firmware: {err!r}", level="WARNING")
            site = Site()
        fw = (f"firmware {site.inverter_firmware}" + (" (assumed: the variant PowerEngine has always used; not "
                                                       "readable from the inverter)" if assumed else "")
              if site.inverter_firmware else "firmware not set")
        text = (f"inverter {site.inverter} ({fw}), "
                f"ev_charger {site.ev_charger}, car {site.car}, tariff {site.tariff}, forecast {site.forecast}, "
                f"events {site.events}")
        try:
            new = {**copy.deepcopy(cfg.raw), "site": site.as_dict()}
            self.cfg, backup = save_config(self._save_path(), new)
        except (ConfigError, OSError) as err:
            self.log(f"Could not add the site to the configuration ({err}); carrying on with the same choices "
                     f"in memory: {text}", level="WARNING")
            return
        self.log(f"Site added to the configuration: {text}; backup: {backup or 'none'}")

    def _state_file(self):
        return os.path.join(os.path.dirname(self._save_path()), "site_state.json")

    def _retest_required(self):
        """True after the site's inverter or firmware changed, until a supervised RC test passes."""
        try:
            with open(self._state_file(), encoding="utf-8") as fh:
                return bool(json.load(fh).get("retest_required"))
        except (OSError, ValueError, AttributeError):
            return False

    def _set_retest(self, required):
        if self._demo or self._retest_required() == required:
            return
        try:
            with open(self._state_file(), "w", encoding="utf-8") as fh:
                json.dump({"retest_required": required}, fh)
        except OSError as err:
            self.log(f"Could not save the test state: {err}", level="WARNING")

    @staticmethod
    def _variant(site):
        """What the site's inverter and firmware come to: the definition and the firmware variant that applies (so
        no firmware and the definition's default one are the same)."""
        try:
            return site.inverter, load_definition(site.inverter, site.inverter_firmware).variant
        except Exception:                                  # no definition file: the firmware as given
            return site.inverter, site.inverter_firmware

    def _site_guard(self, new):
        """(the config to save, whether the inverter or its firmware changed from the saved site). A card that doesn't
        know the `site:` section keeps the saved one. When the inverter or the firmware variant that applies changes,
        PowerEngine goes to Passive whatever was asked: the definition, and so the writes, are different now, and the
        supervised tests have to be run again on it. Other site keys only rebuild their adapters."""
        old = self.cfg.raw.get("site") if self.cfg else None
        if old is not None and "site" not in new:
            new = {**new, "site": old}
        if old is None:
            return new, False
        before, after = parse_config({"site": old}).site, parse_config(new).site
        if self._variant(before) == self._variant(after):
            return new, False
        return (with_operation(new, "passive") if (new.get("operation") or {}).get("mode") == "active" else new), True

    def _slot_map(self):
        """{slot n: {role: entity}} when all the inverter's timed slots exist (SolaX '_2'/'_3' names), else None."""
        if self.cfg is None:
            return None
        m, warn = self._inverter().slot_map(datetime.now(timezone.utc))
        if warn:
            n = self._inverter().slot_count
            self.log(f"Inverter windows {'2 and 3' if n == 3 else f'2 to {n}'} not found (the '_2'/'_3' entities); "
                     "using the single rolling window for now, rechecking every 10 minutes", level="WARNING")
        return m

    def _slot_keys(self, slots):
        return self._inverter().slot_keys(slots)

    def _control(self, r, decision):
        method = self._control_method()
        if method == "ram_remote":
            return self._control_ram(r, decision)
        if getattr(self, "_ram_was_on", False) and self.mode.effective == "active":
            self._ram_off("control method changed to timed windows")
        self._publish_method("timed_windows")
        slots = self._slot_map()
        if slots:
            return self._control_slots(r, decision, slots)
        try:
            p = self._control_params(r)
            now_local = r.now.astimezone(self.tz) if self.tz else r.now
            kind = KINDS.get(decision.action)
            ctl = getattr(self, "_ctl", {"kind": None, "end": None, "last_write": None})
            block_end = None
            if self.plan is not None and self.plan.windows and self.plan.windows[0]["action"] == decision.action:
                be = datetime.fromisoformat(self.plan.windows[0]["end"])
                block_end = be.astimezone(self.tz) if self.tz else be
            current_end = ctl["end"] if ctl["kind"] == kind else None
            kind, end, want = self._inverter().rolling(decision, now_local, current_end, block_end,
                                                       CONTROL_STRATEGY, p.max_charge_kw * 1000,
                                                       p.max_discharge_kw * 1000)
            entities = {role: self._role_entity(role) for role in list(want) + [self._inverter().button_role]}
            missing = sorted(role for role, eid in entities.items() if not eid)
            have = self._inverter().read(entities)
            writes = writes_needed(want, have, self._inverter().button_role)
            state = "not mapped" if missing else (f"{len(writes)} write{'s' if len(writes) != 1 else ''}"
                                                   if writes else "no change")
            attrs = {"decision": decision.action, "strategy": CONTROL_STRATEGY, "missing": missing,
                     "window_end": end.strftime("%H:%M") if end else None,
                     "writes": [w.as_dict() for w in writes],
                     "settings": {role: {"want": v, "now": have.get(role)} for role, v in want.items()}}
            self._publish_if_changed("diag_control", state, attrs)
            recent = ctl["last_write"] is not None and (r.now - ctl["last_write"]).total_seconds() < 60
            if (self.mode.effective == "active" and not missing and writes and not getattr(self, "_halted", False)
                    and not self._test_running()
                    and (not recent or kind != ctl["kind"]) and self._within_write_limit(_real(writes))):
                self._write_why = f"rolling window: {decision.action} ({decision.rule})"
                self._execute(writes, entities)
                ctl["last_write"] = r.now
            ctl["kind"], ctl["end"] = kind, end
            self._ctl = ctl
        except Exception as err:
            self.log(f"Control step failed: {err!r}", level="WARNING")

    def _shadows(self):
        sh = getattr(self, "_damp_shadows", None)
        if sh is None:
            sh = self._damp_shadows = {k: damping.Shadow(*v) for k, v in damping.SHADOWS.items()}
        return sh

    def _shadow_damping(self, now, now_local, pers, decision, have, p):
        """Measure the dampening settings: run the three shadow inverters and count their writes (Active only)."""
        try:
            sft, day = self.cfg.safety, self._today()
            for kind, sh in self._shadows().items():
                n = sh.step(now, now_local, pers, decision.action, decision.power_w, decision.rule, have,
                            BATTERY_VOLTS, p.max_charge_kw * 1000, p.max_discharge_kw * 1000, str(day),
                            float(sft.get("damp_burst_window_min", 10)), float(sft.get("damp_burst_settle_min", 5)),
                            self._inverter().slot_count, self._inverter().button_role)
                if n:
                    self.writes.would(day, n, kind)
        except Exception as err:
            self.log(f"Could not measure dampening: {err!r}", level="WARNING")

    def _damp_restart(self, now):
        if not hasattr(self, "damper"):
            self.damper = damping.Damper()
        cfg = getattr(self, "cfg", None)                  # not loaded yet when called from initialize()
        try:
            mins = float(cfg.safety.get("damp_restart_min", 5)) if cfg else 5.0
        except (TypeError, ValueError):
            mins = 5.0
        self.damper.restarted(now, mins)
        for sh in self._shadows().values():
            sh.damper.restarted(now, mins)

    def _damp(self, now, writes, want, decision):
        """Dampening tuning (config page): the writes to make now, or none while held back."""
        if not hasattr(self, "damper"):
            self.damper = damping.Damper()
        f, sft = self.cfg.features, self.cfg.safety
        day = str(self._today())
        before = self.damper.last_reason
        out = self.damper.filter(now, day, writes, want, decision.rule,
                                 bool(f.get("damp_restart", True)), bool(f.get("damp_bursts", False)),
                                 float(sft.get("damp_burst_window_min", 10)),
                                 float(sft.get("damp_burst_settle_min", 5)))
        if not out and self.damper.last_reason and self.damper.last_reason != before:
            self.damper.count_held(day)
            self.log(f"Writes held back ({self.damper.last_reason})")
        return out

    # --- RAM remote control (control method) ---------------------------------------------------

    def _ram(self):
        rc = getattr(self, "_ramctl", None)
        if rc is None:
            rc = self._ramctl = ramcontrol.RamController()
        return rc

    def _control_method(self):
        """The configured control method, falling back to timed windows (and saying so) if RAM remote control is
        chosen but SolaX Modbus's Battery control override entities can't be found."""
        method = (self.cfg.system.get("control_method") if self.cfg else None) or "timed_windows"
        self._ram_fallback = None
        if method == "ram_remote":
            gone = self._inverter().rc_missing(self._rc_entities())
            if gone:
                self._ram_fallback = "remote-control entities not found: " + ", ".join(gone)
                self._notify("health", ("control:ram_missing", "PowerEngine: using timed windows",
                                        "RAM remote control is selected but SolaX Modbus's Battery control override "
                                        f"entities weren't found ({', '.join(gone)}). PowerEngine is using the "
                                        "timed windows until they're back."))
                return "timed_windows"
        return method

    def _publish_method(self, method, extra=None):
        ram = self._ram()
        day = str(self._today())
        attrs = {"method": method, "fallback": getattr(self, "_ram_fallback", None),
                 "refresh_min": float(self.cfg.safety.get("ram_refresh_min", 1)) if self.cfg else None,
                 "ram_changes_today": ram.changes.get(day, 0), "ram_refreshes_today": ram.refreshes.get(day, 0),
                 "following": ram.follow if method == "ram_remote" else None}
        attrs.update(extra or {})
        label = "RAM remote control" if method == "ram_remote" else "Timed windows"
        if attrs.get("fallback"):
            label = "Timed windows (fallback)"
        self._publish_if_changed("state_control_method", label, attrs)

    def _control_ram(self, r, decision):
        try:
            ram, rc, now = self._ram(), self._rc_entities(), r.now
            p = self._control_params(r)
            want = self._inverter().ram_command(decision.action, decision.power_w, p.max_charge_kw * 1000,
                                                p.max_discharge_kw * 1000,
                                                float(self.cfg.safety.get("ram_max_power_w", 5000)))
            limits = self._bms_limits(r, p.max_charge_kw * 1000)
            want, capped = bms.cap_command(want, limits)             # never ask for more than the battery will take
            want = ram.apply_ceiling(now, want)                      # ...nor above a step-down after a miss
            self._note_bms_cap(capped)
            active = self.mode.effective == "active"
            if self._test_running():
                ram.forget()                                   # the test drives remote control itself
            elif active and not getattr(self, "_halted", False):
                if not getattr(self, "_ram_was_on", False):     # taking over: close the timed windows once
                    self.log("RAM remote control takes over: timed windows closed, then left alone")
                    self._release()
                    self._ram_was_on = True
                    ram.forget()
                refresh = timedelta(minutes=float(self.cfg.safety.get("ram_refresh_min", 1)))
                writes, why = ram.step(now, want, refresh)
                if writes and self._ram_send(writes, rc, why, want):
                    ram.done(now, str(self._today()), want, why)
                    if why == "change" and want.power_role:        # and once more once the mode has landed
                        self.run_in(self._ram_relatch, 5, role=want.power_role, watts=want.watts)
                floor = float(self.cfg.safety.get("min_reserve_soc", 12))
                expected = ramcontrol.inverter_expected_w(
                    ram.sent, bms.expected_w(ram.sent, limits),
                    float(self.cfg.safety.get("inverter_max_output_w", 6000)), r.solar_power)
                follow = ram.check_following(now, r.battery_power, r.battery_soc, floor, expected)
                self._bms_sample(now, r, ram, limits, follow, expected)
                if follow == "not following" and self._ram_step_down(now, ram, r):
                    pass                                   # re-sent lower next cycle; reported once by _ram_step_down
                elif follow == "not following":
                    pw = self.get_state(rc.get(ram.sent.power_role)) if ram.sent.power_role else None
                    self.log(f"RAM remote control: inverter not following {ram.sent.text()} (battery "
                             f"{round(r.battery_power or 0)} W, SoC {r.battery_soc}, power setting reads {pw}, "
                             f"mode reads {self.get_state(rc.get('rc_mode'))})", level="WARNING")
                    self._notify("health", (f"control:ram_follow:{ram.changed_at}",
                                            "PowerEngine: inverter not following remote control",
                                            f"Asked for {ram.sent.text()} but the battery is at "
                                            f"{round(r.battery_power or 0)} W (+ discharging) after 3 minutes "
                                            f"(power setting reads {pw}). Check the inverter and SolaX Modbus; "
                                            "switch the Control method back to Timed windows if it persists."))
            if active and not self._test_running():
                self._simulate_windows(r, decision, p)          # what timed windows would have written meanwhile
            sent = ram.sent
            self._publish_method("ram_remote", {
                "command": sent.text() if sent and active else want.text() + (" (would send)" if not active else ""),
                "last_sent": ram.sent_at.isoformat(timespec="seconds") if ram.sent_at else None,
                "entities": rc})
            self._publish_if_changed("diag_control", "RAM remote control", {
                "decision": decision.action, "strategy": "ram_remote", "command": want.text(),
                "rows": [{"kind": "remote control", "slot": "", "want": want.text(),
                          "now": sent.text() if sent else "unknown"}],
                "writes": [w.as_dict() for w in want.writes()], "following": ram.follow})
        except Exception as err:
            self.log(f"RAM control step failed: {err!r}", level="WARNING")

    def _bms_limits(self, r, rated_charge_w):
        """The battery's own limits now (bms.Limits), from the optional BMS sensors. With none mapped and no cold
        caution, every field is None and nothing changes."""
        def raw(role):
            eid = self._role_entity(role)
            return self.get_state(eid) if eid else None
        try:
            return bms.read_limits(raw("battery_bms_charge_limit"), raw("battery_bms_discharge_limit"),
                                   None, BATTERY_VOLTS, r.battery_power,
                                   self._cold_charge_w(r.now, rated_charge_w))
        except Exception as err:
            self.log(f"BMS limits not read: {err!r}", level="WARNING")
            return bms.Limits()

    def _cold_charge_w(self, now, rated_w):
        """Fallback when the BMS charge limit is unusable: the charge power the cold-battery caution allows now."""
        try:
            if not self.cfg.features.get("cold_caution", True) or not getattr(self, "_caution", None):
                return None
            if not self._caution.get(learning.hour_of(now)):
                return None
            _, fac, _ = self._cold_in_use()
            return rated_w * fac if fac < 1.0 else None
        except Exception:
            return None

    def _note_bms_cap(self, capped):
        """Log a BMS cap once per change of reason."""
        if capped != getattr(self, "_bms_note", None):
            self._bms_note = capped
            if capped:
                self.log(f"RAM remote control: command {capped}")

    def _bms_sample(self, now, r, ram, limits, follow, expected=None):
        """One row for the diagnostics export: command, expected and actual battery power, BMS limits."""
        sent = ram.sent
        if sent is None or sent.option == rctest.OPTION_OFF:
            return
        ring = self.__dict__.get("_bms_ring")
        if ring is None:
            ring = self.__dict__["_bms_ring"] = bms.SampleRing()
        exp = bms.expected_w(sent, limits) if expected is None else expected
        ring.add(now, sent.text(), {"expected_w": exp, "solar_w": r.solar_power, "battery_w": r.battery_power,
                                    "soc": r.battery_soc,
                                    "follow": follow, "limits": limits.as_dict()})

    def _ram_step_down(self, now, ram, r):
        """A charge or discharge that still isn't being followed after the alarm time: one step lower (5000 -> 4000
        -> 3000 W), reported once. False at the floor, so the normal 'not following' report goes out."""
        was, first = ram.sent, ram.ceiling_w is None
        new = ram.step_down(now, r.battery_power)
        if new is None:
            return False
        self.log(f"RAM remote control: the command {was.text()} did not take (battery "
                 f"{round(r.battery_power or 0)} W, SoC {r.battery_soc}); trying {new} W", level="WARNING")
        if first:                                      # the notification once; each step is in the log
            self._notify("health", (f"control:ram_stepdown:{now.date()}:{was.option}",
                                    "PowerEngine: remote control stepped down",
                                    f"Asked for {was.text()} but the battery is at {round(r.battery_power or 0)} W "
                                    f"(+ discharging). Trying lower powers, down to {ramcontrol.STEP_DOWN_FLOOR_W} W; "
                                    "it goes back up when the command changes or after an hour."))
        return True

    def _simulate_windows(self, r, decision, p):
        """While on RAM control: run the timed-window shadow inverters on the same plan and decisions, so the EEPROM
        writes timed windows would have made are counted ('simulated' on the Writes today tile). The plan under RAM
        switches more freely (lower switch cost), so this is an upper estimate for timed windows."""
        slots = self._slot_map()
        if not slots:
            return
        now_local = r.now.astimezone(self.tz) if self.tz else r.now
        have = self._inverter().read(self._slot_keys(slots))
        pers = periods(self.plan.slots if self.plan else [], now_local, self.tz, decision.action)
        self._shadow_damping(r.now, now_local, pers, decision, have, p)

    def _simulated_today(self) -> int | None:
        """Timed-window writes today from the shadow inverter with your current dampening settings."""
        if self.cfg is None:
            return None
        f = self.cfg.features
        kind = ("damp_both" if f.get("damp_bursts", False) else "damp_restart") if f.get("damp_restart", True) \
            else "damp_none"
        return self.writes.data.get(kind, {}).get(self._today().isoformat(), 0)

    def _ram_relatch(self, kwargs):
        """The power once more, a few seconds after a change of command (see ramcontrol.Command.writes)."""
        ram = self._ram()
        if ram.sent is None or ram.sent.power_role != kwargs["role"] or ram.sent.watts != kwargs["watts"]:
            return                                         # superseded
        eid = self._rc_entities().get(kwargs["role"])
        if eid and self.mode.effective == "active":
            try:
                service, data = self._inverter().service_for(Write(kwargs["role"], kwargs["watts"], "number"))
                self.call_service(service, entity_id=eid, **data)
            except Exception as err:
                self.log(f"RAM remote control: could not re-send {eid}: {err!r}", level="WARNING")

    def _ram_send(self, writes, rc, why, want):
        """Send remote-control writes (temporary settings: not counted as EEPROM writes). Changes are journalled;
        refreshes aren't. True if all were sent."""
        now = datetime.now(timezone.utc)
        journal = getattr(self, "journal", None)
        ok = True
        for w in writes:
            eid = rc.get(w.role)
            if not eid:
                ok = False
                continue
            try:
                if why == "change" and journal is not None:
                    journal.add(now, eid, w.value, self.get_state(eid), f"RAM remote control: {want.text()}")
                service, data = self._inverter().service_for(w)
                self.call_service(service, entity_id=eid, **data)
            except Exception as err:
                ok = False
                self._ram().errors += 1
                self.log(f"RAM remote control: could not set {eid}: {err!r}", level="WARNING")
        if why == "change":
            self.log(f"RAM remote control: {want.text()}")
            try:
                if journal is not None:
                    journal.save(now)
            except OSError:
                pass
            self._publish_writes_today()
        return ok

    def _ram_off(self, reason):
        rc = self._rc_entities()
        self._ram_was_on = False
        self._ram().forget()
        if rc.get("rc_mode"):
            self.log(f"RAM remote control Off ({reason})")
            cmd = self._inverter().ram_off_command()
            self._ram_send([cmd.writes()[0]], rc, "change", cmd)

    @staticmethod
    def _first_slot_hours(slots, now):
        """Hours of the plan's first half-hour still to run, so a plan made part-way through it is for the rest of it
        (at least a minute, so a plan made just before the boundary stays sensible). None: a whole half-hour."""
        if not slots:
            return None
        left = (slots[0].end - now).total_seconds()
        if left >= 1800:
            return None
        return max(left, 60.0) / 3600

    def _mid_slot_stick(self, now):
        """A replan part-way through a half-hour (not in its first two minutes, when the plan's next half-hour takes
        over) keeps the running action unless changing it clearly pays: near-ties flip-flopped the inverter."""
        from pe_core.optimiser import MID_SLOT_STICK
        local = now.astimezone(self.tz) if self.tz else now
        into = (local.minute % 30) * 60 + local.second
        started = getattr(self, "_started_at", None)
        if started is not None:
            # just started: the first plans use a default load profile and no weather yet, so their choice isn't
            # worth keeping. Stay free for the rest of the half-hour the warm-up ends in (not just the warm-up
            # itself), so a choice made on unsettled inputs is never locked in for that half-hour.
            warm = (started + STICK_WARMUP).astimezone(self.tz) if self.tz else started + STICK_WARMUP
            free_until = warm.replace(minute=warm.minute - warm.minute % 30, second=0, microsecond=0) \
                + timedelta(minutes=30)
            if now < free_until:
                return 0.0
        return MID_SLOT_STICK if into > 120 and getattr(self, "_decision", None) is not None else 0.0

    def _control_slots(self, r, decision, slots):
        """The three-slot strategy: program the plan's next charge and discharge periods into the inverter."""
        try:
            p = self._control_params(r)
            now_local = r.now.astimezone(self.tz) if self.tz else r.now
            ctl = getattr(self, "_ctl", {"kind": None, "end": None, "last_write": None})
            entities = self._slot_keys(slots)
            missing = sorted(k for k, e in entities.items() if not e)
            have = self._inverter().read(entities)
            pers = periods(self.plan.slots if self.plan else [], now_local, self.tz, decision.action)
            want, writes = self._inverter().slot_writes(pers, have, now_local, decision.action, decision.power_w,
                                                        p.max_charge_kw * 1000, p.max_discharge_kw * 1000)
            state = "not mapped" if missing else (f"{len(writes)} write{'s' if len(writes) != 1 else ''}"
                                                   if writes else "no change")
            sched = {k: [f"{want[f'timed_{k}_start_hour#{n}']:02d}:{want[f'timed_{k}_start_minute#{n}']:02d}-"
                         f"{want[f'timed_{k}_end_hour#{n}']:02d}:{want[f'timed_{k}_end_minute#{n}']:02d}"
                         for n in (1, 2, 3)] for k in ("charge", "discharge")}
            def hhmm(src, k, n):
                try:
                    v = [int(float(src.get(f"timed_{k}_{p}#{n}"))) for p in
                         ("start_hour", "start_minute", "end_hour", "end_minute")]
                except (TypeError, ValueError):
                    return "?"
                return "closed" if v == [0, 0, 0, 0] else f"{v[0]:02d}:{v[1]:02d}-{v[2]:02d}:{v[3]:02d}"
            rows = [{"kind": k, "slot": n, "want": hhmm(want, k, n), "now": hhmm(have, k, n)}
                    for k in ("charge", "discharge") for n in (1, 2, 3)]
            rows += [{"kind": f"{k} current (A)", "slot": "", "want": want.get(f"timed_{k}_current", "unchanged"),
                      "now": have.get(f"timed_{k}_current")} for k in ("charge", "discharge")]
            rows.append({"kind": "storage mode", "slot": "", "want": want.get("storage_mode"),
                         "now": have.get("storage_mode")})
            attrs = {"decision": decision.action, "strategy": "slots", "missing": missing, "windows": sched,
                     "rows": rows, "writes": [w.as_dict() for w in writes]}
            self._publish_if_changed("diag_control", state, attrs)
            recent = ctl["last_write"] is not None and (r.now - ctl["last_write"]).total_seconds() < 60
            if writes and not urgent(writes, want, have, now_local):
                ok, self._pending_want = settled(getattr(self, "_pending_want", None), want, r.now)
                if not ok:
                    writes = []                          # only later windows change: wait for the plan to settle
            else:
                self._pending_want = None
            if self.mode.effective == "active" and not missing:
                self._shadow_damping(r.now, now_local, pers, decision, have, p)
            if self.mode.effective == "active" and writes and not missing:
                wanted = writes
                writes = self._damp(r.now, writes, want, decision)
                if wanted and not writes:
                    attrs["damping"] = self.damper.last_reason
                    self._publish_if_changed("diag_control", f"held: {len(wanted)} writes", attrs)
            if (self.mode.effective == "active" and not missing and writes and not getattr(self, "_halted", False)
                    and not self._test_running() and not recent and self._within_write_limit(_real(writes))):
                self._write_why = f"three windows: {decision.action} ({decision.rule})"
                self._execute(writes, entities)
                self.damper.wrote(r.now, writes)
                ctl["last_write"] = r.now
            self._ctl = ctl
        except Exception as err:
            self.log(f"Control step failed: {err!r}", level="WARNING")

    def _write(self, writes, entities):
        """Send writes to the inverter's control entities (never bump/boost ones). Each is journalled with why."""
        now = datetime.now(timezone.utc)
        journal = getattr(self, "journal", None)
        if journal is None:
            try:
                journal = self.journal = WriteJournal(os.path.join(os.path.dirname(self._save_path()),
                                                                   "write_journal.json"))
            except Exception:                         # no data folder (tests): write without a journal
                journal = None
        why = getattr(self, "_write_why", "") or "control"
        numbers, buttons = [], []
        for w in writes:
            eid = entities.get(w.role)
            if not eid or is_forbidden_control(eid):
                continue
            if w.kind == "button":
                buttons.append(eid)
                continue
            if journal is not None:
                journal.add(now, eid, w.value, self.get_state(eid), why)
            service, data = self._inverter().service_for(w)
            self.call_service(service, entity_id=eid, **data)
            if w.kind == "number":
                numbers.append((eid, w.value))
            if self._inverter().write_storage(w) != "staged":  # window times only sent by the update button
                self.writes.own(self._today())      # 'observed' is counted by the state listener
        if buttons:
            # The Solis "update times" button sends the window entities' current values to the inverter. Pressed at
            # once, it can go before the new values have landed and send the old ones (the inverter then runs one
            # change behind), so press it once they read back, a few seconds later.
            args = {"buttons": buttons, "numbers": numbers, "why": why, "tries": 1}
            if numbers:
                self.run_in(self._press_buttons, BUTTON_DELAY_S, **args)
            else:
                self._press_buttons(args)            # nothing to wait for (e.g. the clock sync button)
        try:
            if journal is not None:
                journal.save(now)
        except OSError as err:
            self.log(f"Could not save the write journal: {err!r}", level="WARNING")
        self._publish_writes_today()

    def _press_buttons(self, kwargs):
        pending = [eid for eid, v in kwargs["numbers"] if not self._inverter().confirmed(eid, v)]
        if pending and kwargs["tries"] < 4:
            self.run_in(self._press_buttons, BUTTON_DELAY_S, **dict(kwargs, tries=kwargs["tries"] + 1))
            return
        if pending:
            self.log(f"Window values not confirmed before applying them ({pending}); applying anyway",
                     level="WARNING")
        now = datetime.now(timezone.utc)
        journal = getattr(self, "journal", None)
        for eid in kwargs["buttons"]:
            self.call_service("button/press", entity_id=eid)
            self.writes.own(self._today())
            if journal is not None:
                journal.add(now, eid, None, None, kwargs["why"])
        try:
            if journal is not None:
                journal.save(now)
        except OSError as err:
            self.log(f"Could not save the write journal: {err!r}", level="WARNING")
        self._publish_writes_today()

    def _execute(self, writes, entities, attempt=1):
        """Active mode, pause and leaving Active. Write, then verify."""
        self._write(writes, entities)
        self.run_in(self._verify_writes, 15, writes=[w.as_dict() for w in writes if w.kind != "button"],
                    entities=entities, attempt=attempt)

    def _verify_writes(self, kwargs):
        bad = [w for w in kwargs["writes"]
               if not self._inverter().confirmed(kwargs["entities"][w["role"]], w["value"])]
        if not bad:
            return
        if kwargs["attempt"] == 1:
            self.log(f"Inverter read-back mismatch ({[w['role'] for w in bad]}); retrying once", level="WARNING")
            self._write_why = "read-back retry"
            self._execute([Write(w["role"], w["value"], w["kind"]) for w in bad], kwargs["entities"], attempt=2)
            return
        self._halted = True                              # no more writes until AppDaemon restarts
        self._publish_status()
        self.log("Inverter writes could not be confirmed; control stopped until restart", level="WARNING")
        self._notify("health", ("control:verify", "PowerEngine: inverter writes not confirmed",
                                "Settings written to the inverter didn't read back correctly twice. Check the "
                                "inverter and the SolaX Modbus integration."))

    def _within_write_limit(self, n):
        """False (and pause control) if these writes would take today's own writes past the daily limit."""
        limit = int(self.cfg.safety.get("max_writes_per_day", 150))
        today = self.writes.own_today(self._today())
        base = self._cap_base = self.writes.base(self._today())     # 0 on a new day or if never resumed today
        if today - base + n <= limit:
            return True
        self.log(f"Daily write limit reached ({today - base} writes, limit {limit}); pausing control",
                 level="WARNING")
        pub = self._get_publisher()
        if pub is not None:                       # the pause switch; its change returns the inverter to Self-Use
            pub.preset("ctl_pause", "ON")
        self._notify("health", (f"control:limit:{self._today()}", "PowerEngine: control paused",
                                f"PowerEngine made {today - base} inverter writes today, reaching the daily limit "
                                f"of {limit}. The inverter is back on Self-Use. Check the Health tab, then resume "
                                "from the Monitoring tab."))
        return False

    def _control_entities(self, roles):
        entities = {role: self._role_entity(role) for role in list(roles) + [self._inverter().button_role]}
        missing = sorted(role for role, eid in entities.items() if not eid)
        return entities, missing

    def _leave_active(self, old, new, guards=()):
        """Leaving Active hands the inverter back to Self-Use once (pause, choosing Passive, or inputs that stopped
        working), except when a handover guard tripped: then another controller has taken over and PowerEngine
        writes nothing to the timed windows. With RAM remote control, remote control is always switched Off (a
        temporary setting, so harmless even when something else has taken over)."""
        if old is None or old.effective != "active" or new.effective == "active":
            return
        if getattr(self, "_ram_was_on", False):
            self._ram_off(f"leaving Active ({new.label})")
        if guards and new.configured == "active":
            self.log(f"Leaving Active: {new.reason}", level="WARNING")
            self._notify("health", ("control:guard", "PowerEngine: control stopped", new.reason))
            return
        if new.effective == "unconfigured" and new.configured == "active" and not self.cfg_error:
            # inputs gone (e.g. HA or the inverter integration restarting): leave the programmed windows running
            # for a while rather than rewriting them; hand back to Self-Use only if the inputs stay missing
            self._release_due = datetime.now(timezone.utc) + timedelta(seconds=INPUT_GRACE_SECONDS)
            self.log(f"Inputs not ready ({new.reason}); the inverter keeps its programmed windows for "
                     f"{INPUT_GRACE_SECONDS // 60} minutes while they come back", level="WARNING")
            return
        self.log(f"Leaving Active ({new.label}: {new.reason}); returning the inverter to Self-Use",
                 level="INFO" if new.effective == "paused" or new.configured == "passive" else "WARNING")
        if new.effective == "paused":
            self._logbook("control paused; inverter returned to Self-Use")
        elif new.configured == "passive":
            self._logbook("control stopped (Passive); inverter returned to Self-Use")
        else:
            self._logbook(f"control stopped ({new.reason}); inverter returned to Self-Use")
            self._notify("health", ("control:inputs", "PowerEngine: control stopped",
                                    f"{new.reason} The inverter is back on Self-Use; control resumes by itself "
                                    "when the inputs are working again."))
        self._release()

    def _release_if_still_missing(self):
        """After the grace period, inputs still missing: hand the inverter back to Self-Use, once."""
        due = getattr(self, "_release_due", None)
        if due is None:
            return
        if self.mode.effective == "active":
            self._release_due = None
            self.log("Inputs back; control resumed without rewriting the inverter")
            return
        if datetime.now(timezone.utc) < due:
            return
        self._release_due = None
        if self.mode.effective != "unconfigured":
            return                                   # paused, Passive or a guard since: handled when that happened
        reason = self.mode.reason
        self.log(f"Inputs still not ready after {INPUT_GRACE_SECONDS // 60} minutes ({reason}); returning the "
                 "inverter to Self-Use", level="WARNING")
        self._logbook(f"control stopped ({reason}); inverter returned to Self-Use")
        self._notify("health", ("control:inputs", "PowerEngine: control stopped",
                                f"{reason} The inverter is back on Self-Use; control resumes by itself when the "
                                "inputs are working again."))
        self._release()

    def _release(self):
        slots = self._slot_map()
        if slots:
            try:
                entities = self._slot_keys(slots)
                have = self._inverter().read(entities)
                writes = self._inverter().release_slots(entities, have)
                if writes:
                    self._write_why = "return to Self-Use"
                    self._execute(writes, entities)
            except Exception as err:
                self.log(f"Could not return the inverter to Self-Use: {err!r}", level="WARNING")
            return
        try:
            want = self._inverter().release_rolling()
            entities, missing = self._control_entities(want)
            if missing:
                return
            have = self._inverter().read(entities)
            writes = writes_needed(want, have, self._inverter().button_role)
            if writes:
                self._write_why = "return to Self-Use"
                self._execute(writes, entities)
        except Exception as err:
            self.log(f"Could not return the inverter to Self-Use: {err!r}", level="WARNING")

    # --- inverter clock ------------------------------------------------------------------------

    def _clock_step(self, kwargs):
        try:
            eid, button = self._inverter().clock_entities()
            if not eid:
                return
            now = datetime.now(timezone.utc)
            status = self._inverter().clock_status(now, self.tz)
            drift = status["drift"]
            old = getattr(self, "_clock_drift", None)
            self._clock_drift = drift
            synced, due = status["synced"], status["due"]
            self._publish_if_changed("diag_inverter_clock", drift if drift is not None else "unknown", {
                "inverter_time": status["inverter_time"],
                "last_sync": synced.isoformat(timespec="seconds") if synced else None,
                "sync_due": due, "sync_button": button})
            if due and button and self.mode.effective == "active" and not self._test_running():
                self.log(f"Syncing the inverter clock ({due}; drift {drift} s)")
                self._write_why = "clock sync"
                self._write([self._inverter().clock_sync_write()], {"inverter_clock_sync": button})
                self._logbook(f"inverter clock synced ({due}, was {drift} s out)")
            if (clock.finding(old) is None) != (clock.finding(drift) is None):
                self._health()
        except Exception as err:
            self.log(f"Inverter clock check failed: {err!r}", level="WARNING")

    # --- supervised test writes ------------------------------------------------------------

    def _test_running(self):
        run = getattr(self, "_test", None)
        return run is not None and not run.done

    def _publish_test(self):
        run = self._test
        self._publish_state("diag_test_write", run.status, run.as_dict())

    def _battery_now(self):
        try:
            r = read(self.cfg, lambda eid: self.get_state(eid, attribute="all"), None,
                     self._tariff(), self._events(), self._ev(), self._forecast())
            return {"soc": r.battery_soc, "battery_w": r.battery_power, "grid_w": r.grid_power}
        except Exception:
            return {}

    def _on_test(self, event_name, data, kwargs):
        user = self._user_name(data)
        now = datetime.now(timezone.utc)
        if data.get("action") == "stop":
            if self._test_running():
                self.log(f"Supervised test stopped by {user}")
                self._test_end({"stopped": True})
            return
        req_roles = self._inverter().test_roles()
        entities, missing = self._control_entities(req_roles) if self.cfg else ({}, ["config"])
        guards = guard_problems(self.cfg, self._guard_state) if self.cfg else ["no config"]
        req, err = testwrite.validate(data, guards, missing, self._test_running())
        if not err and self.mode.effective == "active":
            err = "PowerEngine is in control; pause it first"
        if err:
            self.log(f"Supervised test by {user} refused: {err}", level="WARNING")
            self._test = testwrite.TestRun({"action": data.get("action")}, now)
            self._test.problems.append(err)
            self._test.finish("refused")
            self._publish_test()
            self.fire_event("pe_test_result", ok=False, message=f"Refused: {err}")
            return
        rc = self._rc_entities() if req and req["action"] in rctest.RC_TESTS else {}
        if req and not err and req["action"] in rctest.RC_TESTS:
            err = self._inverter().rc_test_problem(rc, req["action"])
            if err:
                self.log(f"Supervised test by {user} refused: {err}", level="WARNING")
                self._test = testwrite.TestRun({"action": req["action"]}, now)
                self._test.problems.append(err)
                self._test.finish("refused")
                self._publish_test()
                self.fire_event("pe_test_result", ok=False, message=f"Refused: {err}")
                return
        run = self._test = testwrite.TestRun(req, now)
        if req["action"] in rctest.RC_TESTS:
            self._rc_start(req, run, rc, now, user)
            return
        p = self._control_params(self._last_readings) if getattr(self, "_last_readings", None) else None
        max_c, max_d = (p.max_charge_kw * 1000, p.max_discharge_kw * 1000) if p else (4800, 4800)
        now_local = now.astimezone(self.tz) if self.tz else now
        want = self._inverter().test_window(req, now_local, max_c, max_d)
        have = self._inverter().read({role: entities[role] for role in want})
        writes = writes_needed(want, have, self._inverter().button_role)
        run.step(now, "before", **self._battery_now(), settings=have)
        self._write_why = f"supervised test: {req['action']}"
        self._write(writes, entities)
        run.step(now, "wrote", writes=[w.as_dict() for w in writes])
        self.log(f"Supervised test started by {user}: {req['action']} for {req['minutes']} min "
                 f"({len(writes)} writes)")
        self._logbook(f"supervised test started by {user}: {req['action']} for {req['minutes']} min")
        self._publish_test()
        self.fire_event("pe_test_result", ok=True, message=f"Test started: {req['action']} for {req['minutes']} min")
        self._test_handles = [self.run_in(self._test_check, 10, phase="start", want=want, entities=entities),
                              self.run_every(self._test_observe, "now+60", 60),
                              self.run_in(self._test_end, req["minutes"] * 60)]

    def _test_observe(self, kwargs):
        if self._test_running():
            self._test.step(datetime.now(timezone.utc), "battery", **self._battery_now())
            self._publish_test()

    def _test_check(self, kwargs):
        run = self._test
        have = {role: self.get_state(kwargs["entities"][role]) for role in kwargs["want"]}
        bad = readback_mismatches(kwargs["want"], have)
        run.step(datetime.now(timezone.utc), f"read back ({kwargs['phase']})", ok=not bad, mismatched=bad,
                 **self._battery_now())
        if bad:
            run.problems.append(f"{kwargs['phase']}: {', '.join(bad)} did not read back")
        if kwargs["phase"] == "end":
            run.finish("stopped" if kwargs.get("stopped") and not run.problems else None)
            self.log(f"Supervised test {run.status}" + (f": {run.problems}" if run.problems else ""),
                     level="WARNING" if run.problems else "INFO")
            self._logbook(f"supervised test {run.status}")
            self._notify("health", (f"test:{run.started.isoformat()}", f"PowerEngine test {run.status}",
                                    "; ".join(run.problems) or "Settings read back correctly; inverter is back on "
                                    "Self-Use."))
        self._publish_test()

    def _test_end(self, kwargs):
        run = self._test
        if run.req.get("action") in rctest.RC_TESTS:
            self._rc_end(kwargs)
            return
        for handle in getattr(self, "_test_handles", []):
            try:
                self.cancel_timer(handle)
            except Exception:
                pass
        self._test_handles = []
        run.status = "reverting"
        want = release()
        entities, _ = self._control_entities(want)
        have = {role: self.get_state(entities[role]) for role in want}
        writes = writes_needed(want, have, self._inverter().button_role)
        self._write_why = "supervised test: revert"
        self._write(writes, entities)
        run.step(datetime.now(timezone.utc), "reverted to Self-Use", writes=[w.as_dict() for w in writes])
        self._publish_test()
        self.run_in(self._test_check, 10, phase="end", want=want, entities=entities, stopped=kwargs.get("stopped"))

    # --- supervised remote-control (RC register) tests ------------------------------------------

    def _rc_entities(self):
        """SolaX Modbus's remote-control entities, found by name (looked up at most every 10 minutes)."""
        return self._inverter().rc_entities(datetime.now(timezone.utc))

    def _rc_start(self, req, run, rc, now, user):
        test = req["action"]
        power = req["power_w"] = rctest.power_for(test, req.get("power_w"))
        # the timed windows closed first, so they can't be what moves the battery
        want = release()
        windows, _ = self._control_entities(want)
        have = self._inverter().read({role: windows[role] for role in want})
        writes = writes_needed(want, have, self._inverter().button_role)
        run.step(now, "before", **self._battery_now(), rc_entities=rc)
        self._write_why = f"supervised RC test: {test} (timed windows closed first)"
        self._write(writes, windows)
        run.step(now, "timed windows closed", writes=[w.as_dict() for w in writes])
        prole, option, rc_writes = self._inverter().rc_test_writes(test, power)
        self._write_why = f"supervised RC test: {test}"
        self._write(rc_writes, rc)
        run.step(now, f"remote control: {option} at {power} W")
        self._rc = {"rc": rc, "prole": prole, "power": power, "want": want, "windows": windows,
                    "stop_at": None, "samples": []}
        self.log(f"Supervised RC test started by {user}: {test} at {power} W for {req['minutes']} min")
        self._logbook(f"supervised RC test started by {user}: {test} for {req['minutes']} min")
        self._publish_test()
        self.fire_event("pe_test_result", ok=True, message=f"Test started: {test} for {req['minutes']} min")
        self._test_handles = [self.run_in(self._rc_relatch, 5),
                              self.run_every(self._rc_sample, f"now+{rctest.SAMPLE_S}", rctest.SAMPLE_S),
                              self.run_in(self._rc_end, req["minutes"] * 60)]
        if test == "rc_failsafe":
            self._test_handles.append(self.run_in(self._rc_stop_resending, rctest.FAILSAFE_FORCE_S))

    def _rc_relatch(self, kwargs):
        """The power again after the force option: some firmware only takes the power once RC is on."""
        if self._test_running():
            rc = self._rc
            self._write_why = "supervised RC test: power again after the force option"
            self._write([Write(rc["prole"], rc["power"], "number")], rc["rc"])

    def _rc_sample(self, kwargs):
        if not self._test_running():
            return
        now = datetime.now(timezone.utc)
        sample = {"time": now.isoformat(timespec="seconds"), **self._battery_now(),
                  "rc": self.get_state(self._rc["rc"]["rc_mode"])}
        self._rc["samples"].append(sample)
        self._test.step(now, "reading", **{k: v for k, v in sample.items() if k != "time"})
        self._publish_test()

    def _rc_stop_resending(self, kwargs):
        """rc_failsafe: stop SolaX Modbus re-sending the force command (reload it) without writing Off."""
        if not self._test_running():
            return
        now = datetime.now(timezone.utc)
        self._rc["stop_at"] = now.isoformat(timespec="seconds")
        self.call_service("homeassistant/reload_config_entry", entity_id=self._rc["rc"]["rc_mode"])
        self._test.step(now, "stopped re-sending (SolaX Modbus reloaded; Off NOT written) - watching for the "
                             "inverter to drop the force charge by itself")
        self._publish_test()

    def _rc_end(self, kwargs):
        run, rc = self._test, self._rc
        for handle in getattr(self, "_test_handles", []):
            try:
                self.cancel_timer(handle)
            except Exception:
                pass
        self._test_handles = []
        run.status = "reverting"
        self._write_why = "supervised RC test: end (remote control Off)"
        self._write([self._inverter().ram_off_command().writes()[0]], rc["rc"])
        run.step(datetime.now(timezone.utc), "remote control Off", **self._battery_now())
        self._publish_test()
        self.run_in(self._rc_finish, 15, stopped=kwargs.get("stopped"))

    def _rc_finish(self, kwargs):
        run, rc = self._test, self._rc
        now = datetime.now(timezone.utc)
        have = {role: self.get_state(rc["windows"][role]) for role in rc["want"]}
        changed = readback_mismatches(rc["want"], have)
        if changed:
            run.problems.append("timed-window settings changed during the test: " + ", ".join(changed))
        mode = self.get_state(rc["rc"]["rc_mode"])
        if self._inverter().app_option(mode) not in (rctest.OPTION_OFF, None):
            run.problems.append(f"remote control still shows '{mode}' after switching Off")
        run.verdict, run.explanation = rctest.judge(run.req["action"], rc["power"], rc["samples"], rc["stop_at"])
        run.step(now, f"result: {run.verdict}", note=run.explanation, **self._battery_now())
        good = run.verdict in ("worked", "reverted") and not run.problems
        run.finish("stopped" if kwargs.get("stopped") else ("passed" if good else "failed"))
        if good and not kwargs.get("stopped"):
            self._set_retest(False)                        # the tests have been run again on this site
        self.log(f"Supervised RC test {run.status}: {run.verdict} ({run.explanation})"
                 + (f"; {run.problems}" if run.problems else ""), level="INFO" if good else "WARNING")
        self._logbook(f"supervised RC test {run.status}: {run.verdict}")
        self._notify("health", (f"test:{run.started.isoformat()}", f"PowerEngine RC test {run.status}",
                                f"{run.req['action']}: {run.verdict} - {run.explanation}"
                                + ("; " + "; ".join(run.problems) if run.problems else "")))
        self._publish_test()

    def _publish_writes_today(self):
        """Today's real inverter writes (from the journal): count, by reason and the latest ones."""
        try:
            journal = getattr(self, "journal", None)
            if journal is None:
                journal = self.journal = WriteJournal(os.path.join(os.path.dirname(self._save_path()),
                                                                   "write_journal.json"))
            now = datetime.now(timezone.utc)
            local = now.astimezone(self.tz) if self.tz else now
            since = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
            s = day_summary(journal.entries, since.isoformat(timespec="seconds"), self.tz)
            s["limit"] = int(self.cfg.safety.get("max_writes_per_day", 150)) if self.cfg else None
            d = getattr(self, "damper", None)
            s["held"] = d.held.get(str(self._today()), 0) if d else 0
            if self.cfg:
                s["damping"] = {"restart": bool(self.cfg.features.get("damp_restart", True)),
                                "bursts": bool(self.cfg.features.get("damp_bursts", False))}
                try:
                    today = datetime.fromisoformat(str(self._today())).date()
                    days = [(today - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
                    s["damping_week"] = damping.week_summary(self.writes.data, days)
                except Exception:
                    pass
            try:
                s["observed"] = self.writes.summary(self._today())["observed"]["today"]
            except Exception:
                s["observed"] = None
            s["method"] = self._control_method()
            s["simulated"] = self._simulated_today()          # timed windows, same plan (shadow inverter)
            self._publish_state("diag_writes_today", s["writes"], s)
        except Exception as err:
            self.log(f"Could not publish today's writes: {err!r}", level="WARNING")

    def _publish_writes(self):
        self._publish_writes_today()
        try:
            self.writes.save()
            s = self.writes.summary(self._today())
            self._publish_state("diag_inverter_writes", s["would"]["per_day"] if s["would"]["per_day"] is not None
                                else "unknown", s)
        except Exception as err:
            self.log(f"Could not publish inverter writes: {err!r}", level="WARNING")

    # --- smart-charge optimisation (FR-8) -------------------------------------------------

    def _watch_ready_by(self):
        if getattr(self, "_ready_by_handle", None):
            try:
                self.cancel_listen_state(self._ready_by_handle)
            except Exception:
                pass
        self._ready_by_handle = None
        eid = self._role_entity("smart_target_time") if self.cfg else None
        if eid:
            self._ready_by_handle = self._listen_state(self._on_ready_by_change, eid)

    def _on_ready_by_change(self, entity, attribute, old, new, kwargs):
        if old == new or new in (None, "unknown", "unavailable") or old in (None, "unknown", "unavailable"):
            return                                   # coming back after a restart isn't anyone's request
        ours = self._our_write
        if ours and ours[0] == str(new)[:5] and (datetime.now(timezone.utc) - ours[1]).total_seconds() < 120:
            return                                                   # PowerEngine's own change
        r = self._last_readings
        self.smart.observe_external(datetime.now(timezone.utc), str(old)[:5] if old else None, str(new)[:5],
                                    r.dispatches if r else [])
        self._save_smart()

    def _save_smart(self):
        try:
            self.smart.save()
        except OSError as err:
            self.log(f"Could not save smart-charge requests: {err}", level="WARNING")
        self._health()

    def _smart_step(self, r):
        self._last_readings = r
        changed = self.smart.resolve(r.now, r.dispatches)
        eid = self._role_entity("smart_target_time")
        if not eid or not self.cfg.features.get("smart_charge_optimisation"):
            if changed:
                self._save_smart()
            return
        st = self.get_state(eid, attribute="all") or {}
        options = self._tariff().ready_by_options(st)
        cheap_p = cheap_limit(r, self.cfg)
        safety, feats = self.cfg.safety, self.cfg.features
        worth = worth_asking(r.ev_state(), r.dispatches, r.now,
                             r.import_rate is not None and r.import_rate * 100 <= cheap_p, r.battery_soc,
                             safety["grid_charge_target_soc"], bool(feats.get("arbitrage")),
                             r.export_rate * 100 if r.export_rate is not None else None, cheap_p,
                             lookahead=timedelta(hours=safety["smart_lookahead_h"]),
                             whole_house=bool(feats.get("slots_whole_house", True)),
                             car_full=r.ev_complete() or self._car_idle(r),
                             skip_full=bool(feats.get("smart_skip_full_car", False)))
        active = self.mode.effective == "active"
        now_local = r.now.astimezone(self.tz) if self.tz else r.now
        a = self.smart.step(r.now, now_local, worth, options, st.get("state"), r.dispatches, active,
                            settled=self._smart_settled(r.now, st.get("state")),
                            daily_cap=int(safety["smart_max_requests_per_day"]),
                            min_gap=timedelta(minutes=safety["smart_min_gap_min"]))
        if a:
            verb = "Asking" if active else "Would ask"
            self.log(ask_message(verb, a, self._names()))
            if active:
                self._request_slots(eid, a["to"])
        if a or changed:
            self._save_smart()

    def _smart_settled(self, now, ready_by_state):
        """True once PowerEngine has run for SETTLE and EDF's dispatch and ready-by entities have been available
        for SETTLE: right after a restart of AppDaemon, HA or the EDF integration, an empty slot list may only
        mean it hasn't loaded, so no requests are made until things have settled."""
        from pe_core.smartcharge import SETTLE
        disp = self._role_entity("smart_dispatches")
        bad = ready_by_state in (None, "unknown", "unavailable") or (
            disp and self.get_state(disp) in (None, "unknown", "unavailable"))
        since = getattr(self, "_smart_ok_since", None)
        if bad or since is None:
            self._smart_ok_since = None if bad else now
            return False
        return now - since >= SETTLE

    def _request_slots(self, eid, value):
        """Active mode only: set the ready-by time (and keep the charge target at 100%)."""
        try:
            self._our_write = (value, datetime.now(timezone.utc))
            service, data = self._tariff().ready_by_call(eid, value)
            self.call_service(service, **data)
            target = self._role_entity("smart_target_soc")
            call = self._tariff().charge_target_call(target, self.get_state(target) if target else None)
            if call:
                self.call_service(call[0], **call[1])
        except Exception as err:
            self.log(f"Could not request smart-charge slots: {err!r}", level="WARNING")

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

    # --- tariff Simulator (#54), overnight ---------------------------------------------------

    def _sim_folder(self):
        return os.path.join(os.path.dirname(self._save_path()), "simulator")

    def _sim_start(self, kwargs):
        if self.cfg is None or self.costbook is None or not self.cfg.features.get("tariff_simulator", True):
            return
        if getattr(self, "_sim_job", None) is not None:
            return
        try:
            self._sim_store = SimStore(self._sim_folder())
            ctx = self._sim_context()
            region = (ctx.current or {}).get("region")
            if region and self._sim_store.catalogue.get("region") != region:     # tariff codes are per region
                self._sim_store.catalogue.update({"region": region, "products": [], "fetched": None})
            cb = self.costbook
            self._sim_job = sim_run(self._sim_store, cb.recorded_days(),
                                    lambda d: cb.day_records(datetime.fromisoformat(d).date()),
                                    self._params(), self.tz or timezone.utc, datetime.now(timezone.utc),
                                    float(self.cfg.safety.get("ev_charger_kw", 7.4)),
                                    log=lambda m: self.log(m, level="WARNING"), ctx=ctx)
            self._sim_steps, self._sim_began = 0, datetime.now(timezone.utc)
            self.log("Simulator: overnight run started")
            self.run_in(self._sim_step, 1)
        except Exception as err:
            self._sim_job = None
            self.log(f"Simulator could not start: {err!r}", level="WARNING")

    def _sim_step(self, kwargs):
        job = getattr(self, "_sim_job", None)
        if job is None:
            return
        started = datetime.now(timezone.utc)
        try:
            while (datetime.now(timezone.utc) - started).total_seconds() < SIM_SLICE_SECONDS:
                out = next(job)
                self._sim_steps += 1
                if out.get("done"):
                    self._sim_job = None
                    took = (datetime.now(timezone.utc) - self._sim_began).total_seconds()
                    self.log(f"Simulator: done in {took:.0f} s ({self._sim_steps} steps)")
                    self._sim_publish()
                    self._sim_notify(out)
                    return
        except StopIteration:
            self._sim_job = None
            return
        except Exception as err:
            self._sim_job = None
            self.log(f"Simulator run failed: {err!r}", level="WARNING")
            return
        self.run_in(self._sim_step, 1)

    def _sim_context(self) -> SimContext:
        """Your tariff (from the rate sensor's 'tariff' attribute), history, heat pump, location."""
        ctx = SimContext(history=History(os.path.join(self._sim_folder(), "history")),
                         house_includes_car=bool(self.cfg.system.get("house_load_includes_ev", True)),
                         hp=HeatPumpSettings.from_dict(self._sim_settings().get("heat_pump")),
                         equipment=EquipmentSettings.from_dict(self._sim_settings().get("equipment")),
                         auto_cheap=bool(self.cfg.features.get("auto_cheap_threshold", True)))
        eid = self._role_entity("import_rate_now")
        code = str(self.get_state(eid, attribute="tariff") or "") if eid else ""
        if code.startswith("E-1R-") and len(code) > 7:
            supplier = supplier_of(eid)
            ctx.current = {"supplier": supplier, "product": code[5:-2], "tariff": code, "region": code[-1],
                           "name": "your tariff"}
        spec = self.cfg.inputs.get("export_rate") or {}
        try:
            ctx.export_p = float(spec["value"]) if "value" in spec else float(self.get_state(spec.get("entity")))
        except (TypeError, ValueError, KeyError):
            ctx.export_p = None
        try:
            lat = float(self.get_state("zone.home", attribute="latitude"))
            lon = float(self.get_state("zone.home", attribute="longitude"))
            ctx.weather = Weather(os.path.join(self._sim_folder(), "weather.json"), lat, lon)
        except (TypeError, ValueError):
            ctx.weather = None
        return ctx

    def _sim_settings(self) -> dict:
        try:
            with open(os.path.join(self._sim_folder(), "settings.json"), encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def _sim_history_request(self) -> dict:
        """Months the Simulator card should read from HA's statistics, and which sensors."""
        if self.cfg is None or self.costbook is None:
            return {}
        days = self.costbook.recorded_days()
        first = datetime.fromisoformat(days[0]).date() if days else None
        have = set(History(os.path.join(self._sim_folder(), "history")).months())
        check = self.cfg.features.get("use_check_meter", True)
        ent = {"house": [self._role_entity("house_load_today")], "car": [self._role_entity("ev_energy_today")],
               "grid_import": [self._role_entity("grid_import_today_check") if check else None,
                               self._role_entity("grid_import_today")],
               "grid_export": [self._role_entity("grid_export_today_check") if check else None,
                               self._role_entity("grid_export_today")],
               "solar": [p.energy_today.get("entity") for p in self.cfg.solar_plants if p.enabled]}
        ent = {k: [e for e in v if e] for k, v in ent.items()}
        return {"months": [m for m in months_wanted(self._today(), first) if m not in have], "entities": ent,
                "imported": sorted(have)}

    def _on_sim_history(self, event_name, data, kwargs):
        """A month of hourly statistics from the Simulator card (admin's browser)."""
        month = str(data.get("month", ""))
        req = self._sim_history_request()
        if month not in months_wanted(self._today(), None) or not req:
            self.log(f"Simulator: ignored history for '{month}'", level="WARNING")
            return
        hours = parse_upload(data.get("stats") or {}, req["entities"], prefer_first=("grid_import", "grid_export"))
        History(os.path.join(self._sim_folder(), "history")).save_month(month, hours, datetime.now(timezone.utc))
        n = len(hours.get("house", {}))
        self.log(f"Simulator: imported {month} from HA statistics ({n} hours of house load)")
        self._sim_publish()

    def _on_sim_settings(self, event_name, data, kwargs):
        s = HeatPumpSettings.from_dict(data.get("heat_pump"))
        e = EquipmentSettings.from_dict(data.get("equipment"))
        problems = s.problems() + e.problems()
        if problems:
            self.fire_event("pe_sim_result", ok=False, message="Not saved: " + "; ".join(problems))
            return
        path = os.path.join(self._sim_folder(), "settings.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"heat_pump": s.as_dict(), "equipment": e.as_dict()}, fh, indent=1)
        self.log(f"Simulator: settings saved by {self._user_name(data)}")
        self.fire_event("pe_sim_result", ok=True, message="Saved. Tonight's run includes them.")
        self._sim_publish()

    def _sim_publish(self):
        store = getattr(self, "_sim_store", None) or SimStore(self._sim_folder())
        s = store.summary or {}
        meta = {"updated": s.get("updated"), "all_days": s.get("all_days"), "imported_days": s.get("imported_days"),
                "new_products": s.get("new_products", []), "catalogue_updated": store.catalogue.get("fetched"),
                "history_request": self._sim_history_request(), "settings": self._sim_settings(),
                "running": getattr(self, "_sim_job", None) is not None}

        def trim(win, rows=30):
            if not win:
                return None
            out = {k: v for k, v in win.items() if k != "ranking"}
            out["ranking"] = [{k: v for k, v in r.items() if k not in ("import_kwh", "export_kwh")}
                              for r in win.get("ranking", [])[:rows]]
            return out
        short = trim((s.get("windows") or {}).get("30"))
        best = next((r for r in (short or {}).get("ranking", []) if r["id"] != "current"), None)
        self._publish_state("cost_simulator", best["name"][:250] if best else "unknown", {**meta, "window": short})
        year = trim((s.get("windows") or {}).get("year"), 20)        # shares its sensor with the tables below
        self._publish_state("cost_simulator_year", year["days"] if year else "unknown",
                            {"window": year, "heat_pump": s.get("heat_pump"), "planner": s.get("planner"),
                             "equipment": s.get("equipment")})

    # --- low-write study (docs/plans/low-write-mode.md, stage L1): shadow accounting, changes nothing ---------------

    def _lowwrite_start(self, kwargs=None):
        if self.cfg is None or self.costbook is None or self._demo:
            return
        try:
            study = getattr(self, "_lowwrite_study", None) or LowWriteStudy(self._sim_folder())
            self._lowwrite_study = study
            cb = self.costbook
            p = self._params()
            summary = study.run(cb.recorded_days(), lambda d: cb.day_records(datetime.fromisoformat(d).date()), p,
                                float(self.cfg.safety.get("window_switch_cost_p", 5.0)), self.tz or timezone.utc,
                                datetime.now(timezone.utc), limit=LOWWRITE_SLICE)
            self._lowwrite_publish()
            if summary.get("pending"):
                self.run_in(self._lowwrite_start, 60)
        except Exception as err:
            self.log(f"Low-write study failed: {err!r}", level="WARNING")

    def _lowwrite_publish(self):
        study = getattr(self, "_lowwrite_study", None)
        if study is None:
            try:
                study = LowWriteStudy(self._sim_folder())
            except Exception:
                return
        s = study.summary or {}
        rows = {r["id"]: r for r in s.get("profiles", [])}
        cycle = rows.get("overnight_cycle")
        state = (f"overnight cycle keeps {cycle['kept_pct']}%" if cycle and cycle.get("kept_pct") is not None
                 else "waiting for recorded days")
        self._publish_state("diag_lowwrite", state, s or {"days": 0})

    def _sim_notify(self, out):
        month = self._today().strftime("%Y-%m")
        opps = out.get("opportunities") or []
        if opps:
            top = opps[:3]
            lines = [f"{o['name']}: about £{o['saving_month']:.0f} a month less" + (f" ({o['notes']})" if o.get("notes")
                                                                                    else "")
                     for o in top]
            wins = (getattr(self, "_sim_store", None).summary or {}).get("windows", {})
            long = wins.get("year") or {}
            days = (long if long.get("days", 0) >= 90 else wins.get("30") or {}).get("days")
            self._notify("simulator", (f"sim:{top[0]['id']}:{month}", "PowerEngine: a tariff worth a look",
                                       f"Over your last {days} days, with the battery run the same way: "
                                       + "; ".join(lines) + ". See the Simulator tab."))
        new = out.get("new_products") or []
        if new:
            names = ", ".join(sorted({p["name"] for p in new}))[:300]
            self._notify("simulator", ("sim:new:" + ",".join(sorted(p["code"] for p in new))[:120],
                                       "PowerEngine: new tariffs", f"New tariffs published: {names}. They're in "
                                       "tonight's Simulator comparison."))

    def _daily_summary(self, kwargs):
        if self.costbook is None:
            return
        today = self._today()
        sp = self._scenario_params() if self.cfg is not None else None
        s = self.costbook.summary(today - timedelta(days=1), today, scenario_params=sp)
        if s:
            steps = waterfall([s], "yesterday")["steps"] if s.get("scenarios") and s.get("complete") else None
            self._notify("daily", daily_message(s, steps))

    def _health(self):
        if self.costbook is None or self._get_publisher() is None:
            return
        if self.cfg is None:                            # nothing to check yet (no config, or one that didn't load)
            if not self.cfg_error:
                self._publish_state("diag_health", NOT_SET_UP, {"findings": []})
            return
        try:
            h = self.costbook.health(self._today(), getattr(self, "_checks", None))
            h["slots"] = self.slots.summary(datetime.now(timezone.utc), tz=self.tz,
                                                max_kw=float(self.cfg.safety.get("ev_charger_kw", 7.4)))
            h["smart_requests"] = self.smart.summary(datetime.now(timezone.utc), tz=self.tz)
            f = clock.finding(getattr(self, "_clock_drift", None))
            if f:
                h["findings"].append(f)
            from pe_core.health import finding_key
            for x in h["findings"]:
                x["key"] = finding_key(x)
            gone = self._dismissed()
            h["all_findings"] = list(h["findings"])
            h["findings"] = [x for x in h["findings"] if x["key"] not in gone]
            h["dismissed"] = [{"title": v.get("title"), "at": v.get("at")}
                              for v in sorted(gone.values(), key=lambda v: v.get("at", ""), reverse=True)[:5]]
            h["state"] = overall(h["findings"])
            self._health_last = h
            self._publish_state("diag_health", h["state"], {k: v for k, v in h.items() if k != "all_findings"})
            self._notify("health", health_message(h))
        except Exception as err:
            self.log(f"Could not evaluate health: {err!r}", level="WARNING")

    def _sync_solar_entities(self):
        """Discover a power sensor for each enabled solar plant, and retire any no longer enabled (plants
        removed or disabled since the last time this ran). HA's own state, not a file PowerEngine keeps, is
        the record of what's currently published (the same lookup RAM remote control uses for its entities)."""
        if self.cfg is None or self._get_publisher() is None:
            return
        wanted = {p.id: p.name for p in self.cfg.solar_plants if p.enabled}
        try:
            ids = list((self.get_state() or {}).keys())
        except Exception:
            ids = []
        published = {pid for pid in (solar_plant_id_from_entity(e) for e in ids) if pid}
        for pid, name in wanted.items():
            ent = solar_plant_entity(pid, name)
            self._get_publisher().discover(ent, __version__)
        for pid in published - set(wanted):
            self._get_publisher().retire(solar_plant_entity(pid, ""))

    def _sync_device_entities(self):
        """Discover a sensor for each input a read-only device has mapped, and retire any no longer wanted (same
        approach as the solar plants: HA's own state is the record of what is published)."""
        if self.cfg is None or self._get_publisher() is None:
            return
        input_field = {"battery_soc": "soc", "battery_power": "battery_power", "solar_power": "solar_power"}
        wanted = {(d.id, input_field[k]): d.name for d in self.cfg.devices for k in d.inputs}
        try:
            ids = list((self.get_state() or {}).keys())
        except Exception:
            ids = []
        published = {ref for ref in (device_ref_from_entity(e) for e in ids) if ref}
        for (did, fname), name in wanted.items():
            self._get_publisher().discover(device_entity(did, name, fname), __version__)
        for did, fname in published - set(wanted):
            self._get_publisher().retire(device_entity(did, "", fname))

    def _energy_flow_card(self):
        plants = self.cfg.solar_plants if self.cfg else ()
        if self.cfg is not None and getattr(self, "_last_readings", None) is not None:
            capacity_wh = round(self._control_params(self._last_readings).capacity_kwh * 1000 / 100) * 100
        else:
            capacity_wh = 18000
        reserve_soc = self.cfg.safety.get("min_reserve_soc", 12) if self.cfg else 12
        has_ev = bool(self._role_entity("ev_charge_power")) if self.cfg else False
        return energy_flow_card(plants, capacity_wh, reserve_soc, has_ev, self._inverter().card_model)

    def _sync_dashboard(self):
        targets = [os.path.join(os.path.dirname(self._save_path()), "dashboard.yaml")]
        if self._demo and not self._real_config_exists():           # nobody's set up yet: the registered dashboard
            targets.append(os.path.join(self._real_dir(), "dashboard.yaml"))    # shows the demo
        for target in targets:
            try:
                if sync_dashboard(target, card=self._energy_flow_card(), names=self._names()):
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
            new = coerce_flags(new)          # true/false that arrived as text or 0/1 (the demo's settings save)
            old_devices = self.cfg.raw.get("devices") if self.cfg else None
            if old_devices is not None and "devices" not in new:     # a card that doesn't know devices keeps them
                new = {**new, "devices": old_devices}
            new, switched = self._site_guard(new)
            _, backup = save_config(self._save_path(), new)
        except (ConfigError, OSError) as err:
            self.log(f"Config save by {user} rejected: {err}", level="WARNING")
            self.fire_event(RESULT_EVENT, ok=False, message=str(err))
            return
        changed = self._changes(self.cfg.raw if self.cfg else {}, new)
        self.log(f"Config saved by {user} ({changed}); backup: {backup or 'none (first save)'}")
        self._logbook(f"configuration saved by {user}: {changed}")
        if switched:
            self._set_retest(True)
            self.log(f"Site: the inverter or its firmware was changed by {user}; PowerEngine is Passive and the "
                     f"supervised tests need running again", level="WARNING")
            self._logbook(f"site changed by {user}: the inverter or its firmware; Passive until the tests are re-run")
        self._reload()
        if switched:
            self._notify("health", (f"site:{new['site'].get('inverter')}:{new['site'].get('inverter_firmware')}",
                                    "PowerEngine: inverter changed",
                                    "The inverter or its firmware was changed in the site settings. PowerEngine is "
                                    "Passive (nothing is controlled) until you run the supervised tests again."))
        self.fire_event(RESULT_EVENT, ok=True, message=f"Saved. {changed}."
                        + (" The inverter changed, so PowerEngine is Passive until the tests are re-run."
                           if switched else ""))

    def _check_update(self, kwargs):
        """HACS replaces the app's files but AppDaemon keeps the old modules loaded. When the version on disk differs
        from the one running, fire pe_update_installed once; the handover package's automation restarts AppDaemon.
        (PowerEngine can't restart AppDaemon itself: that needs admin rights it deliberately doesn't have.)"""
        installed = installed_version(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pe_core",
                                                   "__init__.py"))
        if installed is None or installed == __version__ or getattr(self, "_update_seen", None) == installed:
            return
        self._update_seen = installed
        self.log(f"Version {installed} installed (running {__version__}); asking Home Assistant to restart AppDaemon")
        self.fire_event("pe_update_installed", running=__version__, installed=installed)

    def _listen_for_commands(self):
        """Direct mode only: HA has no integration behind our entities, so hear its service calls. (With MQTT the
        switches are real and the app just watches their state, so nothing is registered.)"""
        pub = self._get_publisher()
        if pub is not None and not pub.retains:
            self.listen_event(self._on_call_service, "call_service")
            self.listen_event(self._on_pe_command, "pe_command")

    def _on_call_service(self, event_name, data, kwargs):
        """Direct mode: a switch, select or number of ours was operated in HA (see pe_core/commands.py)."""
        data = data or {}
        for ent, value in from_service(str(data.get("domain")), str(data.get("service")),
                                       data.get("service_data") or {}, self.get_state):
            self._on_command(ent.key, value)

    def _on_pe_command(self, event_name, data, kwargs):
        """Direct mode: the card's own command event, for when HA refuses a service call for a missing platform."""
        for ent, value in from_pe_command(data, self.get_state):
            self._on_command(ent.key, value)

    def _on_command(self, key, value):
        """One place a command from HA lands: set the entity, and the state listeners (pause and guards, history)
        react to the change exactly as they do when MQTT delivers it."""
        pub = self._get_publisher()
        if pub is not None:
            self.log(f"Command from Home Assistant: {key} = {value}")
            pub.preset(key, value)

    def _on_set_control(self, event_name, data, kwargs):
        """The Battery controller switch: Active when handed to PowerEngine, Passive when handed to Predbat.
        Saved like any config change (with a backup), so it survives restarts."""
        mode = (data or {}).get("operation")
        try:
            if self.cfg is None:
                raise ConfigError(self.cfg_error or "no configuration yet")
            if self.cfg.raw.get("operation", {}).get("mode") == mode:
                self.log(f"Controller switch: already {mode}")
                self._reload()                             # re-evaluate (pause may just have changed)
                return
            new = with_operation(self.cfg.raw, mode)
            _, backup = save_config(self._save_path(), new)
        except (ConfigError, OSError) as err:
            self.log(f"Controller switch to {mode!r} refused: {err}", level="WARNING")
            self._notify("health", ("control:switch", "PowerEngine: switch failed", str(err)))
            return
        self.log(f"Controller switch: operation set to {mode}; backup: {backup or 'none'}")
        self._logbook(f"operation set to {mode} by the battery controller switch")
        self._reload()

    def _reload(self):
        """Re-read config.yaml in place and republish status (no app restart)."""
        self._temps_at = None                              # cold settings may have changed: recompute soon
        p_before = self._params(conversion=False) if self.cfg is not None else None
        self.cfg_error = None
        try:
            self.cfg, self.cfg_path = load_config(self.paths)
        except ConfigError as err:
            self.cfg, self.cfg_error = None, str(err)
        self._watch_controls()
        self._watch_ready_by()
        if self.cfg is not None:
            self._publish_names()                          # the site's adapters may have changed
        if self.cfg is not None and self.costbook is not None:
            fid = flow_id(self.cfg)
            if fid != self.costbook.flow_id:              # inputs that shape the flows changed: rebuild from history
                self.costbook.flow_id = fid
                self.log("Cost inputs changed; recent days will be rebuilt from HA history")
                self.run_in(self._backfill, 30)
            else:
                p_after = self._params(conversion=False)
                if p_before is not None and (abs(p_after.capacity_kwh - p_before.capacity_kwh) > 0.05
                                             or abs(p_after.efficiency - p_before.efficiency) > 0.001):
                    n = self.costbook.revalue(**self._cost_params())      # e.g. 'use measured' toggled
                    self.log(f"Battery now {p_after.capacity_kwh:.2f} kWh, {p_after.efficiency ** 2 * 100:.1f}% "
                             f"round trip; costs re-valued ({n} half-hours)")
                    self._refresh_months()
            self._measure(revalue=False)                   # republish which capacity is in use
        self._last_checks = None
        self._evaluate()
        self._sync_solar_entities()
        self._sync_device_entities()
        self._sync_dashboard()
        self._cycle({})

    # --- diagnostics export ------------------------------------------------------------------------

    def log(self, msg, *args, **kwargs):
        """AppDaemon's log, also kept in a small in-memory ring for the diagnostics export."""
        ring = self.__dict__.get("_log_ring")
        if ring is None:
            ring = self.__dict__["_log_ring"] = diagnostics.LogRing()
        ring.add(datetime.now(timezone.utc), kwargs.get("level", "INFO"), msg)
        return super().log(msg, *args, **kwargs)

    def _diag_bundle(self, now):
        s = diagnostics.safe
        journal = getattr(self, "journal", None)
        if journal is None:
            journal = s(lambda: WriteJournal(os.path.join(os.path.dirname(self._save_path()), "write_journal.json")))
        mode = getattr(self, "mode", None)
        run = getattr(self, "_test", None)
        return {
            "app": {"version": __version__, "min_card_version": MIN_CARD_VERSION,
                    "generated": now.isoformat(timespec="seconds"),
                    "timezone": str(self.tz) if self.tz else None},
            "mode": s(lambda: {"configured": mode.configured, "effective": mode.effective, "reason": mode.reason,
                               "halted": bool(getattr(self, "_halted", False)),
                               "cap_base": getattr(self, "_cap_base", 0)}) if mode else None,
            "config": s(lambda: self.cfg.raw) if self.cfg else {"error": getattr(self, "cfg_error", "no config")},
            "writes": s(lambda: self.writes.summary(self._today())),
            "journal": s(lambda: diagnostics.recent_journal(journal.entries, now)),
            "plan": s(lambda: plan_snapshot(self.plan, now - timedelta(hours=1), now + timedelta(hours=36)))
            if getattr(self, "plan", None) is not None else None,
            "test": run.as_dict() | {"status": run.status} if run is not None else None,
            "smart_requests": s(lambda: self.smart.attempts[-40:]) if getattr(self, "smart", None) else None,
            "smart_slots": s(lambda: self.slots.summary(now, tz=self.tz)) if getattr(self, "slots", None) else None,
            "bms": s(self._bms_bundle),
            "early_target": s(lambda: {**self._early.summary(), "records": self._early.records[-60:]}),
            "attribute_sizes": s(lambda: diagnostics.largest_attrs(self.__dict__.get("_attr_sizes", {}))),
            "log": list(getattr(self.__dict__.get("_log_ring"), "lines", [])),
        }

    def _bms_bundle(self):
        """The BMS limit sensors (charge, discharge) now, and the recent command / expected / actual rows, so
        the first cold spell can be read back: command, battery power and the limits side by side."""
        roles = ("battery_bms_charge_limit", "battery_bms_discharge_limit")
        now_raw = {k: {"entity": self._role_entity(k),
                       "state": self.get_state(self._role_entity(k)) if self._role_entity(k) else None} for k in roles}
        ring = self.__dict__.get("_bms_ring")
        return {"mapped": {k: bool(v["entity"]) for k, v in now_raw.items()}, "sensors": now_raw,
                "recent": ring.as_list() if ring is not None else [],
                "note": f"limits are in amps; watts use the nominal battery voltage ({BATTERY_VOLTS:g} V)"}

    def _on_diag_request(self, event_name, data, kwargs):
        now = datetime.now(timezone.utc)
        rid = str(data.get("id", ""))[:40]
        bundle = self._diag_bundle(now)
        stamp = (now.astimezone(self.tz) if self.tz else now).strftime("%Y%m%d-%H%M%S")
        saved = diagnostics.safe(diagnostics.save_copy,
                                 os.path.join(os.path.dirname(self._save_path()), "diagnostics"), stamp, bundle)
        self.log(f"Diagnostics exported for {self._user_name(data)} ({saved if isinstance(saved, str) else saved})")
        self.fire_event(diagnostics.BUNDLE_EVENT, id=rid, bundle=json.loads(json.dumps(bundle, default=str)),
                        saved=saved if isinstance(saved, str) else None)

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
            labels = [fill(ROLE_BY_KEY[k].label) if k in ROLE_BY_KEY else k for k in changed]
            more = "…" if len(labels) > 6 else ""
            parts.append(f"{len(changed)} input(s) changed: " + ", ".join(labels[:6]) + more)
        if other:
            parts.append("also " + ", ".join(sorted(other)))
        return "; ".join(parts) or "no changes"

    # --- helpers -------------------------------------------------------------------

    def _retire_old_entities(self):
        """Remove entities an earlier release published (entities.RETIRED_ENTITIES). MQTT: clear their retained
        topics (harmless to repeat). Direct: only those HA still has, so a fresh install gets no stray entity."""
        pub = self._get_publisher()
        for ent in RETIRED_ENTITIES:
            try:
                if pub.retains or self.get_state(ent.entity_id) is not None:
                    pub.retire(ent)
            except Exception as err:
                self.log(f"Could not retire {ent.entity_id}: {err!r}", level="WARNING")

    def _ui_defaults(self):
        """Set dashboard preferences to their defaults once (HA and the broker keep them after that)."""
        path = os.path.join(os.path.dirname(self._save_path()), "ui.json")
        try:
            with open(path, encoding="utf-8") as fh:
                done = json.load(fh)
        except (OSError, ValueError):
            done = {}
        pub = self._get_publisher()
        if not pub.retains:                       # direct: nothing survives a start, so set them again
            done = {}
        changed = False
        if not done.get("right_align"):
            pub.preset("ui_right_align", "ON")               # right-aligned numbers by default
            done["right_align"] = changed = True
        if changed and pub.retains:
            try:
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(done, fh)
            except OSError as err:
                self.log(f"Could not save dashboard defaults: {err}", level="WARNING")

    # --- demo mode (demo plan C2): a simulated home and battery, and nothing written to the real system -----------

    def _demo_setup(self, day):
        """`demo: <day>` in apps.yaml. The settings are a fresh copy of demo/config.template in a separate folder, so
        the real configuration, costs, journal and learned data are never touched; the app's get_state, call_service,
        fire_event, set_state and get_history become the demo gate's (pe_core/demo/gate.py); publishing is direct."""
        pack = load_demo_pack()
        if day not in pack["days"]:
            first = next(iter(pack["days"]))
            self.log(f"Demo: no day called {day!r} (the pack has {', '.join(pack['days'])}); using {first}",
                     level="WARNING")
            day = first
        self._demo = day
        self.cfg_path = None
        demo_dir = os.path.join(self._real_dir(), "demo")
        shutil.rmtree(demo_dir, ignore_errors=True)                  # a fresh demo each start
        os.makedirs(demo_dir, exist_ok=True)
        shutil.copyfile(os.path.join(os.path.dirname(__file__), "demo", "config.template"),
                        os.path.join(demo_dir, "config.yaml"))
        self.paths = [os.path.join(demo_dir, "config.yaml")]
        real_set = getattr(self, "set_state", None)
        gate = self._demo_gate = DemoGate(
            self._demo_world, getattr(self, "get_state", None),
            self._lossless_set_state(real_set) if real_set is not None else None,
            getattr(self, "fire_event", None), self.log,
            allowed_events=(RESULT_EVENT, "pe_test_result", "pe_sim_result", diagnostics.BUNDLE_EVENT,
                            DEMO_RESULT_EVENT))
        self.get_state, self.get_history, self.call_service = gate.get_state, gate.get_history, gate.call_service
        self.set_state, self.fire_event = gate.set_state, gate.fire_event
        self.log(f"Demo mode: the {day} day, simulated home and battery; nothing outside {demo_dir} is changed")

    def _real_dir(self):
        """The folder the real config lives in (or would): where demo.json, the demo folder and, before the app is set
        up, the dashboard go. Not the demo folder, even while the demo runs."""
        paths = self.__dict__.get("_real_paths") or list(DEFAULT_PATHS)
        for path in paths:
            if os.path.isdir(os.path.dirname(os.path.dirname(path))):
                return os.path.dirname(path)
        return os.path.dirname(paths[0])

    def _real_config_exists(self):
        return any(os.path.isfile(p) for p in (self.__dict__.get("_real_paths") or list(DEFAULT_PATHS)))

    def _demo_file(self):
        return os.path.join(self._real_dir(), "demo.json")

    def _saved_demo_day(self):
        """The day a dashboard start chose (demo.json), honoured only while there is no real config."""
        path = self._demo_file()
        if not os.path.isfile(path):
            return None
        if self._real_config_exists():
            self.log(f"Ignoring {path}: a real config exists, so no demo starts by itself", level="WARNING")
            return None
        try:
            with open(path, encoding="utf-8") as fh:
                return str(json.load(fh).get("day") or "") or None
        except (OSError, ValueError, AttributeError):
            return None

    def _demo_reply(self, ok, message):
        self.log(f"Demo: {message}", level="INFO" if ok else "WARNING")
        self.fire_event(DEMO_RESULT_EVENT, ok=ok, message=message)

    def _on_demo(self, event_name, data, kwargs):
        """The card's demo buttons (pe_demo: start / day / exit). Only the event named pe_demo is handled."""
        if event_name != DEMO_EVENT:
            return
        data = data if isinstance(data, dict) else {}
        action, day = data.get("action"), data.get("day")
        if self.__dict__.get("_touched") is None:           # started configured: never re-initialised in place
            return self._demo_reply(False, "The demo can only start on a PowerEngine that isn't set up yet, so it "
                                           "never takes over a real system.")
        pack = load_demo_pack()
        if action in ("start", "day"):
            if action == "start" and not self._demo and self._real_config_exists():
                return self._demo_reply(False, "The demo can only start on a PowerEngine that isn't set up yet, "
                                               "so it never takes over a real system.")
            if not isinstance(day, str) or day not in pack["days"]:
                return self._demo_reply(False, f"There's no demo day called {day!r}. Choose one of: "
                                               f"{', '.join(pack['days'])}.")
            if action == "day" and not self._demo:
                return self._demo_reply(False, "No demo is running. Start it first.")
            if self.args.get("demo"):
                return self._demo_reply(False, f"The demo is set in apps.yaml (demo: {self.args['demo']}); change "
                                               "that line and restart AppDaemon to pick another day.")
            try:
                os.makedirs(self._real_dir(), exist_ok=True)
                tmp = self._demo_file() + ".tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump({"day": day}, fh)
                os.replace(tmp, self._demo_file())
            except OSError as err:
                return self._demo_reply(False, f"Couldn't save the demo choice ({err}).")
            title = fill(pack["days"][day].get("title", day), self._names())
            self._demo_reply(True, f"Starting the demo: {title}." if action == "start" else f"Switched to: {title}.")
        elif action == "exit":
            if self.args.get("demo"):
                return self._demo_reply(False, f"The demo is set in apps.yaml (demo: {self.args['demo']}); remove "
                                               "that line and restart AppDaemon to leave it.")
            try:
                os.remove(self._demo_file())
            except FileNotFoundError:
                pass
            except OSError as err:
                return self._demo_reply(False, f"Couldn't end the demo ({err}).")
            self._demo_reply(True, "The demo has ended.")
        else:
            return self._demo_reply(False, f"I don't know the demo action {action!r}.")
        self.initialize()                                            # again, cleanly: see _wipe

    def _demo_world(self):
        world = self.__dict__.get("_world")
        if world is None:
            pack = load_demo_pack()
            # The recorded days are UK wall-clock days (offsets from local midnight in the pack's time zone), so the
            # demo always runs on that zone, not on whatever AppDaemon's `time_zone` happens to be: a fresh install
            # is often set to UTC, which shifted the whole day (the sunny day's solar, the events, the forecast) by
            # an hour against the clock the owner sees.
            world = self._world = DemoWorld(pack, self._demo, ZoneInfo(pack["tz"]), lambda: datetime.now(timezone.utc))
        return world

    def _demo_info(self):
        """What the demo banner shows (published on the version sensor)."""
        names = self._names()
        pack = self._demo_world().pack["days"]
        return {"day": self._demo, "title": fill(self._demo_world().title(), names),
                "days": [{"key": k, "title": fill(v.get("title", k), names)} for k, v in pack.items()],
                "note": "Recorded data from a real home. Nothing is controlled."}

    def _publisher_choice(self):
        if self._demo:
            return "direct"
        choice = ((getattr(self.cfg, "system", None) or {}).get("publisher") if self.cfg else None) or "auto"
        return choice

    def _get_publisher(self):
        """The one door for every entity the app publishes (MQTT or direct; the core never knows which)."""
        pub = self.__dict__.get("_publisher_obj")
        if pub is None:
            pub = select_publisher(self._publisher_choice(), getattr(self, "mqtt", None), self._quiet_set_state())
            self._publisher_obj = pub
        return pub

    @staticmethod
    def _appdaemon_cleans_attributes():
        """Does this AppDaemon clean the payload of set_state (4.5 and later)? 4.4 sends it as given."""
        try:
            from appdaemon import utils as ad_utils
            return hasattr(ad_utils, "clean_http_kwargs")
        except Exception:
            return False

    def _rest_states_poster(self):
        """A function that writes an entity's state and attributes to Home Assistant exactly as given, or None. Newer
        AppDaemon versions clean the payload of `set_state` on the way (`true` becomes "true"; `false`, `null` and every
        0 are dropped, so a series loses its zeros and every later value shifts, and the config's booleans arrive as
        text). This posts to HA's states endpoint with the plugin's own session instead, the way `set_state` does,
        but with nothing removed. None where its plugin can't be reached."""
        try:
            plugin = self.AD.plugins.get_plugin_object(self.namespace)
            loop, session, base = self.AD.loop, plugin.session, plugin.config.ha_url
            import aiohttp
        except Exception:
            return None

        async def post(entity_id, state, attributes):
            payload = {"state": "" if state is None else str(state), "attributes": attributes or {}}
            async with session.post(base / f"api/states/{entity_id}", json=payload,
                                    timeout=aiohttp.ClientTimeout(total=10)) as resp:
                resp.raise_for_status()

        def send(entity_id, state, attributes):
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is loop:                             # on AppDaemon's own loop: can't wait for it here
                loop.create_task(post(entity_id, state, attributes))
            else:
                asyncio.run_coroutine_threadsafe(post(entity_id, state, attributes), loop).result(15)
        return send

    def _lossless_set_state(self, plain):
        """`plain` (AppDaemon's set_state) for anything it would not alter; the exact REST write for attributes it would
        (see _rest_states_poster). Only our own `pe_` entities are ever written this way."""
        if not self._appdaemon_cleans_attributes():
            return plain
        post = self._rest_states_poster()

        def set_state(entity_id, **kwargs):
            attributes = kwargs.get("attributes")
            if attributes and ad_would_alter(attributes) and re.match(r"^[a-z_]+\.pe_", str(entity_id)):
                if post is not None:
                    try:
                        post(entity_id, kwargs.get("state"), attributes)
                        return None
                    except Exception as err:
                        self._told_once("rest_write", f"Could not write {entity_id} to Home Assistant directly "
                                                      f"({err!r}); using AppDaemon's set_state instead")
                else:
                    self._told_once("rest_altered", "AppDaemon's set_state changes attribute values (true becomes "
                                                    "text, false, null and 0 are dropped) and this AppDaemon can't be "
                                                    "bypassed; some PowerEngine attributes may be wrong", "WARNING")
            return plain(entity_id, **kwargs)
        return set_state

    def _told_once(self, key, message, level="INFO"):
        told = self.__dict__.setdefault("_told", set())
        if key not in told:
            told.add(key)
            self.log(message, level=level)

    def _quiet_set_state(self):
        """set_state for the direct publisher. AppDaemon 4.5 and later warn "Entity ... not found" when set_state
        creates an entity, unless told `check_existence=False`; we create our own entities, so say so. 4.4 has no such
        argument (it would become an attribute), so it is only passed where set_state names it."""
        raw = getattr(self, "set_state", None)
        if raw is None:
            return None
        if not self._demo:                                  # (the demo gate has it underneath: see _demo_setup)
            raw = self._lossless_set_state(raw)
        try:
            named = "check_existence" in inspect.signature(hass.Hass.set_state).parameters
        except (TypeError, ValueError, AttributeError):
            named = False
        if not named:
            return raw

        def set_state(entity_id, **kwargs):
            kwargs.setdefault("check_existence", False)
            return raw(entity_id, **kwargs)
        return set_state

    def _plugin_configured(self, name):
        """Is this plugin in AppDaemon's configuration? get_plugin_api logs a WARNING ("Unknown Plugin Configuration")
        when it isn't, so ask the plugin list first. Where the list can't be read, say yes and let the call decide."""
        try:
            plugins = self.AD.plugins
            for attr in ("config", "plugins"):
                found = getattr(plugins, attr, None)
                if isinstance(found, dict):
                    return name in found
        except Exception:
            pass
        return True

    def _mqtt_api(self, quiet=False):
        try:
            api = self.get_plugin_api("MQTT") if self._plugin_configured("MQTT") else None
        except Exception as err:  # plugin not configured
            api = None
            if not quiet:
                self.log(f"MQTT plugin error: {err}", level="WARNING")
        if api is None and quiet:
            self.log("No MQTT plugin in AppDaemon: publishing entities directly.")
        elif api is None:
            self.log("MQTT plugin not configured in appdaemon.yaml; entities can't be published. "
                     "See docs/INSTALL.md, Step 3.", level="WARNING")
        return api

    def _publish_state(self, key, state, attributes=None):
        pub = self._get_publisher()
        if pub is None:
            return
        if attributes is None:
            pub.publish(key, state)
        else:
            payload = attributes if isinstance(attributes, str) else json.dumps(attributes, default=str)
            try:                                  # measured as HA's recorder stores it: compact JSON, UTF-8
                compact = json.dumps(json.loads(payload), separators=(",", ":"), ensure_ascii=False)
            except ValueError:
                compact = payload
            size = len(compact.encode("utf-8"))
            sizes = self.__dict__.setdefault("_attr_sizes", {})
            if diagnostics.track_attr_size(sizes, key, size):
                self.log(f"sensor.pe_{key}: attributes are {size} bytes, near Home Assistant's "
                         f"{diagnostics.ATTR_LIMIT}-byte limit (over it, HA stops recording this sensor's history)",
                         level="WARNING")
            pub.publish(key, state, payload)

    def _publish_status(self):
        """The Monitoring page's Mode tile: one word, coloured by the dashboard. Stopped (writes couldn't be confirmed)
        and Blocked (a config with inputs not ready, or Active refused) are both shown red. Before there is a config the
        word is "Not set up yet", shown neutral."""
        mode = getattr(self, "mode", None)
        if mode is None:
            return
        if getattr(self, "_halted", False):
            word, why = "Stopped", "Inverter writes couldn't be confirmed; control stopped until AppDaemon restarts."
        elif mode.effective == "active":
            word, why = "Active", mode.reason
        elif mode.effective == "paused":
            word, why = "Paused", mode.reason
        elif mode.effective == "passive" and mode.configured != "active":
            word, why = "Passive", mode.reason
        elif self.cfg is None and not self.cfg_error:
            word, why = NOT_SET_UP, mode.reason
        else:
            word, why = "Blocked", mode.reason
        self._publish_state("state_status", word, {"reason": why, "mode": mode.effective})

    def _beat(self, kwargs):
        self._publish_state("diag_heartbeat", datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def _remove_entities(self):
        self._get_publisher().retire_all()
        self.log("remove_entities is set: removed all PowerEngine entities. The app is now idle; "
                 "remove it in HACS or set remove_entities: false to bring them back.", level="WARNING")
