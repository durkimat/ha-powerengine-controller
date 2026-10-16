"""Engine v2's own settings: the `engine_v2:` block of config.yaml (docs/plans/engine-v2.md, section 11).

v1 and v2 keep separate settings, even where the defaults match, so each engine can be tuned without moving the other.
Facts about the house (battery size, fuse, the battery's hard floor, control method, ...) stay shared in the v1
sections of config.yaml; only engine choices live here.

A key missing from the saved block takes its value from v1's equivalent setting when there is one (`SEED_FROM`), else
the catalogue default. So the first time v2 is chosen it starts from what the owner already set for v1; once the
config page saves the block, the two are independent.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any

from ..names import fill

W0 = "Starting weight; learned from then on."
# key: (kind, default, min, max, unit, label, help). kind: "number", "int", "bool" or "choice" (min = the options).
SETTINGS: dict[str, tuple] = {
    # --- what is allowed ---------------------------------------------------------------------------------------
    "arbitrage": ('bool', False, None, None, '', 'Sell from the battery',
                 'Sell stored energy outside grid events when that pays more than keeping it.'),
    "events": ("bool", True, None, None, "", "Grid events",
               "Act on grid events (<<event>>): export at full power while one runs."),
    "event_plus_export": ("bool", True, None, None, "", "Export rate paid on top of a grid event",
                          "The export rate is paid on top of the event's rate."),
    "free_power": ("bool", True, None, None, "", "Free-power sessions", "Charge to 100% during a free-power session."),
    "free_arbitrage": ("bool", False, None, None, "", "Free power: charge or sell",
                       "A free-power session may also sell what is stored."),
    "hold_only_car_reserve": ("bool", False, None, None, "", "Hold only for car and reserve",
                              "Hold only for the car or the reserve; else charge, sell or self-use."),
    "prefer_self_use": ('bool', True, None, None, '', 'Prefer Self-use to Hold by day',
                 'By day the battery runs the house and stores spare sun; Hold is for cheap times, the car and '
                 'the reserve.'),
    "preview_when_v1": ('bool', True, None, None, '', 'Preview while engine v1 is in control',
                 'Show what engine v2 would do while v1 is in control. Nothing is sent.'),
    "charge_ceiling_soc": ('number', 100, 10, 100, '%', 'Highest grid charge',
                 'A grid charge never goes above this. Sun can still fill the battery.'),
    "charge_max_import_p": ('number', 0.0, 0, 100, 'p/kWh', 'Highest price to charge at',
                 'No grid charge above this import price (a smart slot may bring less). 0: off.'),
    # --- floors ------------------------------------------------------------------------------------------------
    "reserve_soc": ('number', 12, 0, 100, '%', 'Your reserve',
                 'Kept back in normal running; grid events may go below it.'),
    "hard_floor_margin_pct": ('number', 1, 0, 10, 'points', 'Grid event margin above the hard floor',
                 "A grid event stops this far above the battery's hard floor."),
    # --- value and costs ---------------------------------------------------------------------------------------
    "wear_house_p": ('number', 0.0, 0, 20, 'p/kWh', 'Wear: battery supplying the house',
                 'Cost per kWh the battery supplies to the house. 0: sunk cost.'),
    "wear_sale_p": ("number", 0.0, 0, 20, "p/kWh", "Wear: sales",
                    "Cost counted per kWh the battery sells (exports and grid events)."),
    "event_value_p": ("number", 100.0, 0, 500, "p/kWh", "Grid event pays",
                      "What a grid event pays per kWh, before any export rate on top."),
    "terminal_value": ('choice', 'refill', ('refill', 'fixed'), None, '', 'Energy left at the end of the look-ahead',
                 'Refill: the cheapest import price after losses, else what the house will need. Fixed: the figure '
                 'below.'),
    "terminal_value_p": ("number", 10.0, 0, 100, "p/kWh", "Fixed value of energy left",
                         "Used when the energy left at the end is set to Fixed."),
    # --- comfort band ------------------------------------------------------------------------------------------
    "comfort_low_soc": ('number', 20, 0, 100, '%', 'Comfort band: lower edge',
                 'Below this the battery pays the comfort cost for each hour there.'),
    "comfort_high_soc": ('number', 90, 0, 100, '%', 'Comfort band: upper edge',
                 'Above this the battery pays the comfort cost for each hour there.'),
    "comfort_cost_p": ('number', 0.3, 0, 10, 'p per kWh per hour', 'Comfort cost',
                 'The price of each kWh held an hour outside the band: a soft guide. 0: off.'),
    "top_up_cost_p": ('number', 5.0, 0, 20, 'p/kWh', 'Grid charging above the comfort band',
                 "What each kWh charged from the grid above the band's top counts as costing. 0: off."),
    # --- following the plan and the top and bottom of the battery (engine 2.1 beta) -----------------------------
    "executor": ('choice', 'lines', ('lines', 'plan'), None, '', 'How the mode is chosen',
                 "Lines: live prices against a stored kWh's value. Plan: the plan's mode (2.1 beta)."),
    "replan_on_level": ('bool', False, None, None, '', 'Re-plan when a charge or sale ends',
                 'Re-plan as soon as a charge or sale reaches its target.'),
    "level_top_start_soc": ("number", 90, 50, 100, "%", "Top: where the cost starts",
                            "Above this level each stored kWh costs a rate per hour."),
    "level_top_rate_p": ('number', 0.0, 0, 1000, 'p/kWh/h', 'Top: rate at 100%',
                 'The rate at 100%, doubling every few points above the start. 0: off.'),
    "level_top_doubling_pts": ("number", 2.0, 0.5, 20, "points", "Top: points per doubling",
                               "How steeply the top rate climbs."),
    "level_bot_start_soc": ("number", 70, 0, 100, "%", "Bottom: where the cost starts",
                            "Below this level each stored kWh costs a rate per hour."),
    "level_bot_rate_p": ('number', 0.0, 0, 1000, 'p/kWh/h', 'Bottom: rate at your reserve',
                 'The rate at your reserve, doubling every few points below the start. 0: off.'),
    "level_bot_doubling_pts": ("number", 5.0, 0.5, 30, "points", "Bottom: points per doubling",
                               "How steeply the bottom rate climbs."),
    "slot_bot_factor": ('number', 1.0, 1, 1000, 'times', 'Slots: bottom multiplier',
                 'In a smart slot the bottom rate is multiplied by this. 1: no change.'),
    "slot_bot_start_soc": ('number', 0, 0, 100, '%', 'Slots: bottom start',
                 'In a smart slot the bottom curve starts here (0: as usual).'),
    # --- forecast caution and learning -------------------------------------------------------------------------
    "solar_low_pct": ("number", 25, 0, 100, "%", "Solar: weight of the low forecast", W0),
    "solar_mid_pct": ("number", 50, 0, 100, "%", "Solar: weight of the middle forecast", W0),
    "solar_high_pct": ("number", 25, 0, 100, "%", "Solar: weight of the high forecast", W0),
    "load_low_pct": ("number", 25, 0, 100, "%", "House load: weight of the low figure", W0),
    "load_mid_pct": ("number", 50, 0, 100, "%", "House load: weight of the middle figure", W0),
    "load_high_pct": ("number", 25, 0, 100, "%", "House load: weight of the high figure", W0),
    "learn_scenario_weights": ('bool', True, None, None, '', 'Learn the weights from experience',
                 'Move the weights towards how often sun and house came in low, mid or high.'),
    "scenario_half_life_days": ("number", 14, 1, 60, "days", "Learning: half-life",
                                "How quickly old days stop counting."),
    "scenario_prior_days": ('number', 7, 0, 60, 'days', 'Learning: weight of the starting values',
                 'The starting weights count as this many days of experience.'),
    "learn_solar_bias": ('bool', True, None, None, '', "Learn the solar forecast's bias",
                 'Scale the solar forecast by how it compared with the real sun in the last 14 days.'),
    "learn_soc_offset": ("bool", True, None, None, "", "Learn the battery reading's offset",
                         "Learn how far the battery reading is off when charging, holding and discharging."),
    # --- responsiveness ----------------------------------------------------------------------------------------
    "reversal_levy_p": ("number", 0.0, 0, 20, "p/kWh", "Levy to turn a charge into a sale",
                        "Turning a charge into a sale, or back, needs the price this far past."),
    "reentry_levy_p": ("number", 0.0, 0, 20, "p/kWh", "Levy to restart a run that just ended",
                       "A run that ended in the last 10 min restarts only this much further past."),
    "corridor_cost_p": ("number", 0.5, 0, 5, "p", "Corridor width",
                        "A charge or sale stops anywhere that costs within this of the best stop."),
    "price_band_p": ('number', 0.5, 0, 5, 'p/kWh', 'Price band',
                 'A mode starts when better by this much, and stops when worse by this much.'),
    "level_band_pct": ('number', 1.0, 0, 5, 'points', 'Level band',
                 'A charge that reached its target restarts only this far below it; a sale likewise.'),
    "switch_cost_p": ('number', 2.0, 0, 20, 'p per change', 'Cost of a mode change',
                 'What each change of mode counts as costing, in the plan and when deciding.'),
    "min_dwell_s": ("int", 120, 0, 3600, "s", "Shortest time in a mode",
                    "No change of mode sooner than this, except for safety events."),
    "deadline_grace_min": ('number', 10, 1, 60, 'min', 'Deadline grace',
                 'How long a run may pass its expected end before a re-plan.'),
    "debounce_s": ("int", 20, 0, 300, "s", "Reading debounce", "A changed reading must last this long to count."),
    "car_start_debounce_s": ('int', 60, 0, 600, 's', 'Car start debounce',
                 'The car must report charging this long before it counts.'),
    "car_stop_debounce_s": ("int", 30, 0, 600, "s", "Car stop debounce",
                            "The car must report not charging this long before it counts as stopped."),
    "stale_after_s": ("int", 180, 30, 1800, "s", "Missing reading grace",
                      "A required reading missing for this long counts as missing data."),
    "soc_filter_gain": ('number', 0.05, 0.001, 1, '', 'Battery level filter gain',
                 'How fast the filtered battery level follows the reading.'),
    "drift_kwh": ('number', 0.75, 0.1, 5, 'kWh', 'Forecast drift',
                 'Re-plan when what happened differs from the forecast by this much.'),
    "band_exit_min": ('number', 10, 1, 60, 'min', 'Outside the expected range',
                 'Re-plan when the battery is outside its expected range this long.'),
    "forecast_change_pct": ('number', 10, 1, 100, '%', 'Solar forecast change',
                 'Re-plan when a forecast update moves the rest of the day by this much.'),
    "revalue_coalesce_s": ('int', 10, 0, 120, 's', 'Batch window',
                 'Reasons to re-plan arriving this close together run once.'),
    "max_value_age_min": ('int', 120, 10, 720, 'min', 'Backstop',
                 'Work the values out at least this often, in case a trigger was missed.'),
    "sample_s": ("int", 10, 5, 60, "s", "Sample interval",
                 "How often the readings are checked against the conditions."),
    # --- model -------------------------------------------------------------------------------------------------
    "level_step_kwh": ("number", 0.1, 0.05, 0.5, "kWh", "Level resolution",
                       "Step of the battery-level grid the values are worked out on."),
    "max_segment_min": ("int", 30, 5, 60, "min", "Longest step", "No step of the look-ahead is longer than this."),
}

# config-page sections, in display order: (key, label, settings)
SECTIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("allowed", "What engine v2 may do",
     ("arbitrage", "events", "event_plus_export",
      "free_power", "free_arbitrage", "hold_only_car_reserve", "charge_ceiling_soc",
      "charge_max_import_p", "prefer_self_use", "preview_when_v1")),
    ("floors", "Floors", ("reserve_soc", "hard_floor_margin_pct")),
    ("comfort", "Comfort band", ("comfort_low_soc", "comfort_high_soc", "comfort_cost_p", "top_up_cost_p")),
    ("levels", "Plan-following and battery limits",
     ("executor", "replan_on_level", "reversal_levy_p", "reentry_levy_p", "corridor_cost_p",
      "level_top_start_soc", "level_top_rate_p", "level_top_doubling_pts",
      "level_bot_start_soc", "level_bot_rate_p", "level_bot_doubling_pts", "slot_bot_factor", "slot_bot_start_soc")),
    ("value", "Value and costs",
     ("wear_house_p", "wear_sale_p", "event_value_p", "terminal_value", "terminal_value_p")),
    ("forecast", "Forecast caution and learning",
     ("solar_low_pct", "solar_mid_pct", "solar_high_pct", "load_low_pct", "load_mid_pct", "load_high_pct",
      "learn_scenario_weights", "scenario_half_life_days", "scenario_prior_days", "learn_solar_bias",
      "learn_soc_offset")),
    ("response", "Responsiveness",
     ("price_band_p", "level_band_pct", "switch_cost_p", "min_dwell_s", "deadline_grace_min",
      "debounce_s", "car_start_debounce_s", "car_stop_debounce_s", "stale_after_s", "soc_filter_gain", "drift_kwh",
      "band_exit_min", "forecast_change_pct", "revalue_coalesce_s", "max_value_age_min", "sample_s")),
    ("model", "Model", ("level_step_kwh", "max_segment_min")),
)

# v2 key -> (v1 config part, v1 key): where a missing v2 setting takes its first value from
SEED_FROM = {
    "arbitrage": ("features", "arbitrage"),
    "events": ("features", "axle"),
    "event_plus_export": ("features", "axle_plus_export"),
    "free_power": ("features", "free_power_days"),
    "reserve_soc": ("safety", "min_reserve_soc"),
    "comfort_high_soc": ("safety", "arbitrage_max_soc"),
}


class SettingsError(ValueError):
    pass


@dataclass(frozen=True)
class V2Settings:
    arbitrage: bool = False
    events: bool = True
    event_plus_export: bool = True
    free_power: bool = True
    free_arbitrage: bool = False
    hold_only_car_reserve: bool = False
    prefer_self_use: bool = True
    preview_when_v1: bool = True
    charge_ceiling_soc: float = 100
    charge_max_import_p: float = 0.0
    reserve_soc: float = 12
    hard_floor_margin_pct: float = 1
    wear_house_p: float = 0.0
    wear_sale_p: float = 0.0
    event_value_p: float = 100.0
    terminal_value: str = "refill"
    terminal_value_p: float = 10.0
    executor: str = "lines"
    replan_on_level: bool = False
    level_top_start_soc: float = 90
    level_top_rate_p: float = 0.0
    level_top_doubling_pts: float = 2.0
    level_bot_start_soc: float = 70
    level_bot_rate_p: float = 0.0
    level_bot_doubling_pts: float = 5.0
    slot_bot_factor: float = 1.0
    slot_bot_start_soc: float = 0
    comfort_low_soc: float = 20
    comfort_high_soc: float = 90
    comfort_cost_p: float = 0.3
    top_up_cost_p: float = 5.0
    solar_low_pct: float = 25
    solar_mid_pct: float = 50
    solar_high_pct: float = 25
    load_low_pct: float = 25
    load_mid_pct: float = 50
    load_high_pct: float = 25
    learn_scenario_weights: bool = True
    scenario_half_life_days: float = 14
    scenario_prior_days: float = 7
    learn_solar_bias: bool = True
    learn_soc_offset: bool = True
    reversal_levy_p: float = 0.0
    reentry_levy_p: float = 0.0
    corridor_cost_p: float = 0.5
    price_band_p: float = 0.5
    level_band_pct: float = 1.0
    switch_cost_p: float = 2.0
    min_dwell_s: int = 120
    deadline_grace_min: float = 10
    debounce_s: int = 20
    car_start_debounce_s: int = 60
    car_stop_debounce_s: int = 30
    stale_after_s: int = 180
    soc_filter_gain: float = 0.05
    drift_kwh: float = 0.75
    band_exit_min: float = 10
    forecast_change_pct: float = 10
    revalue_coalesce_s: int = 10
    max_value_age_min: int = 120
    sample_s: int = 10
    level_step_kwh: float = 0.1
    max_segment_min: int = 30

    def as_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @property
    def solar_weights(self) -> tuple[float, float, float]:
        """Starting weights of the low, middle and high solar scenarios, as fractions summing to 1."""
        return _norm(self.solar_low_pct, self.solar_mid_pct, self.solar_high_pct)

    @property
    def load_weights(self) -> tuple[float, float, float]:
        return _norm(self.load_low_pct, self.load_mid_pct, self.load_high_pct)


def _norm(a: float, b: float, c: float) -> tuple[float, float, float]:
    t = a + b + c
    return (a / t, b / t, c / t) if t > 0 else (0.25, 0.5, 0.25)


def _check(key: str, value: Any) -> Any:
    kind, _, lo, hi = SETTINGS[key][:4]
    if kind == "bool":
        if not isinstance(value, bool):
            raise SettingsError(f"engine v2 setting '{key}' must be true or false")
        return value
    if kind == "choice":
        if value not in lo:
            raise SettingsError(f"engine v2 setting '{key}' must be one of {', '.join(lo)}")
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SettingsError(f"engine v2 setting '{key}' must be a number")
    if not lo <= value <= hi:
        raise SettingsError(f"engine v2 setting '{key}' must be between {lo} and {hi}")
    return int(round(value)) if kind == "int" else value


# The engines the system setting offers: v1 and v2. "v2_1" is no longer an engine of its own: an old config that says so
# means engine v2 with the "2.1 beta" profile (ENGINE_ALIAS), and is still accepted.
ENGINES = ("v1", "v2")
ENGINE_ALIAS = {"v2_1": "v2"}

# Profiles are named sets of engine v2's tuning. The profile in use gives the values wherever the `engine_v2:` block
# (the changes you made on top of it) does not set one; loading a profile on the config page clears those changes, so
# nothing left over from an earlier profile can win. A profile holds tuning only: the facts and permissions of the
# house (SITE_KEYS) stay as they are when a profile is loaded.
DEFAULT_PROFILE = "defaults"
BETA_PROFILE = "2.1 beta"
BETA22_PROFILE = "2.2 beta"
BUILTIN_PROFILES: dict[str, dict[str, Any]] = {
    DEFAULT_PROFILE: {},
    BETA_PROFILE: {
        "executor": "plan", "replan_on_level": True,
        "price_band_p": 0.0, "level_band_pct": 0.0, "min_dwell_s": 120, "comfort_cost_p": 0.0, "top_up_cost_p": 0.0,
        "prefer_self_use": False,
        "level_top_start_soc": 90, "level_top_rate_p": 200.0, "level_top_doubling_pts": 3.0,
        "level_bot_start_soc": 70, "level_bot_rate_p": 10.0, "level_bot_doubling_pts": 5.0,
        "slot_bot_factor": 30.0, "slot_bot_start_soc": 80,
    },
}
BUILTIN_PROFILES[BETA22_PROFILE] = {**BUILTIN_PROFILES[BETA_PROFILE], "hold_only_car_reserve": True,
                                    "free_arbitrage": True, "reversal_levy_p": 4.0, "reentry_levy_p": 4.0,
                                    "charge_max_import_p": 15.0}
BUILTIN_NOTES = {
    DEFAULT_PROFILE: "Engine v2's own defaults.",
    BETA_PROFILE: "Engine v2 following its own plan, with a smooth cost keeping the battery off the very top and "
                  "bottom. Beta.",
    BETA22_PROFILE: "2.1 beta, and Hold only for the car and the reserve, free power that may also sell, levies "
                    "against turning a run round or restarting one that just ended, and no grid charge above "
                    "15p. Beta.",
}
# Settings that belong to the house, not to a way of tuning the engine: a profile never holds or changes them.
SITE_KEYS = frozenset({
    "arbitrage", "events", "event_plus_export", "free_power", "preview_when_v1", "charge_ceiling_soc",
    "reserve_soc", "hard_floor_margin_pct", "comfort_low_soc", "comfort_high_soc",
    "wear_house_p", "wear_sale_p", "event_value_p",
})
PROFILE_KEYS = tuple(k for k in SETTINGS if k not in SITE_KEYS)
MAX_USER_PROFILES = 10
MAX_PROFILE_NAME = 30


def profile_name_problem(name: Any) -> str | None:
    """Why a profile name can't be used, else None."""
    if not isinstance(name, str) or not name.strip():
        return "a profile needs a name"
    if name != name.strip() or len(name) > MAX_PROFILE_NAME:
        return f"a profile name has no spaces at its ends and is at most {MAX_PROFILE_NAME} characters"
    if name.lower() in BUILTIN_PROFILES:
        return f"'{name}' is the name of a built-in profile"
    return None


def parse_profiles(raw: Any) -> dict[str, dict[str, Any]]:
    """The `v2_profiles:` block: your saved profiles, name -> the values that differ from engine v2's defaults."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise SettingsError("'v2_profiles' must be a mapping")
    if len(raw) > MAX_USER_PROFILES:
        raise SettingsError(f"at most {MAX_USER_PROFILES} profiles can be saved")
    out: dict[str, dict[str, Any]] = {}
    for name, values in raw.items():
        problem = profile_name_problem(name)
        if problem:
            raise SettingsError(problem)
        if not isinstance(values, dict):
            raise SettingsError(f"profile '{name}' must be a mapping")
        values = {k: v for k, v in values.items() if k not in RETIRED}
        unknown = sorted(set(values) - set(PROFILE_KEYS))
        if unknown:
            raise SettingsError(f"profile '{name}' has setting(s) a profile can't hold: {', '.join(unknown)}")
        out[name] = {k: _check(k, v) for k, v in values.items()}
    return out


def profile_values(name: str | None, user: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """The values a profile gives (only those that differ from the defaults)."""
    name = name or DEFAULT_PROFILE
    if name in BUILTIN_PROFILES:
        return dict(BUILTIN_PROFILES[name])
    if user and name in user:
        return dict(user[name])
    raise SettingsError(f"unknown engine v2 profile '{name}'")


def profile_of(engine: str | None) -> str | None:
    """The profile an old `system.engine` value implies: the 2.1 beta profile for "v2_1", else None."""
    return BETA_PROFILE if engine == "v2_1" else None


# Settings an earlier release had and a saved config may still hold: dropped quietly, never an error.
RETIRED = frozenset({"reversal_cost_p",     # 0.9.129: one cost for every change (switch_cost_p)
                     "late_events", "late_events_per_week", "late_event_hours"})   # 0.9.148: unannounced events


def parse_v2(raw: Any, features: dict | None = None, safety: dict | None = None,
             profile: dict | None = None) -> V2Settings:
    """The `engine_v2:` block, validated. Missing keys come from the profile in use (`profile`: its values, see
    `profile_values`), else v1's equivalent (`SEED_FROM`), else the default. Raises SettingsError for an unknown key, a
    wrong type, a value out of range, or settings that contradict."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise SettingsError("'engine_v2' must be a mapping")
    raw = {k: v for k, v in raw.items() if k not in RETIRED}      # a setting an earlier release had: ignored
    unknown = sorted(set(raw) - set(SETTINGS))
    if unknown:
        raise SettingsError(f"unknown engine v2 setting(s): {', '.join(unknown)}")
    v1 = {"features": features or {}, "safety": safety or {}}
    values: dict[str, Any] = {}
    for key, spec in SETTINGS.items():
        if key in raw:
            values[key] = _check(key, raw[key])
        elif profile and key in profile:
            values[key] = _check(key, profile[key])
        elif key in SEED_FROM and SEED_FROM[key][1] in v1[SEED_FROM[key][0]]:
            values[key] = _check(key, v1[SEED_FROM[key][0]][SEED_FROM[key][1]])
        else:
            values[key] = spec[1]
    if values["comfort_low_soc"] >= values["comfort_high_soc"]:
        raise SettingsError("the comfort band's lower edge must be below its upper edge")
    if values["level_top_rate_p"] > 0 and values["level_top_start_soc"] >= 100:
        raise SettingsError("the top of the battery's cost must start below 100%")
    if values["level_bot_rate_p"] > 0 and values["level_bot_start_soc"] <= values["reserve_soc"]:
        raise SettingsError("the bottom of the battery's cost must start above your reserve")
    for part in ("solar", "load"):
        if values[f"{part}_low_pct"] + values[f"{part}_mid_pct"] + values[f"{part}_high_pct"] <= 0:
            raise SettingsError(f"the {part} weights can't all be 0")
    return V2Settings(**values)


def catalogue(current: V2Settings | None = None, profile: str | None = None,
              user_profiles: dict[str, dict] | None = None) -> dict:
    """The schema for the config page (published on sensor.pe_diag_v2_settings): every setting with its default (the
    profile in use's), range, label and help, the sections, the values in use now, the engines the system setting
    offers. (The profiles are on their own sensor, `profiles_catalogue`: this one is close to HA's 16 KB limit.)"""
    try:
        base = profile_values(profile, user_profiles)
    except SettingsError:
        base = {}
    rows = []
    for key, (kind, default, lo, hi, unit, label, help_) in SETTINGS.items():
        default = base.get(key, default)
        # This sensor is near HA's size limit: a setting with no unit shows none, and one with no kind is a number
        # (the card reads both that way).
        row = {"key": key, "default": default, "label": fill(label), "help": fill(help_)}
        if kind != "number":
            row["kind"] = kind
        if unit:
            row["unit"] = unit
        if kind == "choice":
            row["options"] = list(lo)
        elif kind != "bool":
            row["min"], row["max"] = lo, hi
        rows.append(row)
    return {"settings": rows,
            "sections": [{"key": k, "label": fill(label), "keys": list(keys)} for k, label, keys in SECTIONS],
            "values": (current or V2Settings()).as_dict(), "engines": list(ENGINES)}


def profiles_catalogue(profile: str | None = None, user_profiles: dict[str, dict] | None = None) -> dict:
    """The profiles for the config page (sensor.pe_diag_v2_profiles): the one in use, the built-in ones with their
    values, your saved ones by name (the card has their values from the saved config), and the settings a profile
    never holds."""
    return {"profile": profile or DEFAULT_PROFILE,
            "builtin": [{"name": n, "note": BUILTIN_NOTES[n], "values": dict(v)} for n, v in BUILTIN_PROFILES.items()],
            "saved": sorted(user_profiles or {}), "site_keys": sorted(SITE_KEYS),
            "max_saved": MAX_USER_PROFILES, "max_name": MAX_PROFILE_NAME}
