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

# key: (kind, default, min, max, unit, label, help). kind: "number", "int", "bool" or "choice" (min = the options).
# Since 0.9.157 the settings say what you want, not how the engine gets there: each maps onto the engine's own tuning
# (the properties of V2Settings below). Everything here acts in the plan and in the live decision alike.
HOLD_USE = ("car and reserve", "cheap times", "any time")
FIRMNESS = ("off", "gentle", "firm", "strict")
CAUTION = ("cautious", "balanced", "optimistic")
SETTINGS: dict[str, tuple] = {
    # --- what the battery may do -------------------------------------------------------------------------------
    "arbitrage": ('bool', False, None, None, '', 'Sell from the battery',
                 'Sell stored energy outside grid events when that pays more than keeping it.'),
    "events": ("bool", True, None, None, "", "Grid events",
               "Act on grid events (<<event>>): export at full power while one runs."),
    "event_value_p": ("number", 100.0, 0, 500, "p/kWh", "Grid event pays",
                      "What a grid event pays per kWh, before any export rate on top."),
    "event_plus_export": ("bool", True, None, None, "", "Export rate paid on top of a grid event",
                          "The export rate is paid on top of the event's rate."),
    "free_power": ("bool", True, None, None, "", "Free-power sessions", "Charge to 100% during a free-power session."),
    "free_arbitrage": ("bool", False, None, None, "", "Free power: charge or sell",
                       "A free-power session may also sell what is stored."),
    "hold_use": ("choice", "cheap times", HOLD_USE, None, "", "When the battery may hold",
                 "Hold lets the grid run the house while the battery keeps its charge. Car and reserve: only then. "
                 "Cheap times: also at a cheap rate. Any time: whenever that pays."),
    "charge_ceiling_soc": ('number', 100, 10, 100, '%', 'Highest grid charge',
                 'A grid charge never goes above this. Sun can still fill the battery.'),
    "charge_max_import_p": ('number', 0.0, 0, 100, 'p/kWh', 'Highest price to charge at',
                 'No grid charge above this import price (a smart slot may bring less). 0: off.'),
    # --- the battery's range -----------------------------------------------------------------------------------
    "reserve_soc": ('number', 12, 0, 100, '%', 'Your reserve',
                 'Kept back in normal running; grid events may go below it.'),
    "range_low_soc": ("number", 20, 0, 100, "%", "Everyday range: bottom",
                      "Below this, keeping energy in the battery gets dearer by the hour, so the plan stays above it "
                      "where it can."),
    "range_high_soc": ("number", 90, 50, 100, "%", "Everyday range: top",
                       "Above this, holding energy gets dearer by the hour: the battery fills past it only when that "
                       "pays (a last fill before a dear stretch)."),
    "range_firmness": ("choice", "gentle", FIRMNESS, None, "", "How firmly the range holds",
                       "Off: no range. Gentle: a soft guide. Firm: the battery rarely leaves it. Strict: only to "
                       "avoid a real loss."),
    "min_run_pct": ("number", 0, 0, 100, "points", "Shortest charge or sale",
                    "A charge or sale runs at least this far (or to the edge of the range) before it stops or turns "
                    "round. Fewer, longer cycles. 0: off."),
    "slot_floor_soc": ("number", 0, 0, 100, "%", "Smart slots: keep above",
                       "While the plan counts on a smart slot to refill the battery, it keeps the level above this, "
                       "in case the slot is withdrawn. 0: the everyday range."),
    "slot_odds_pct": ("number", 60, 0, 100, "%", "Smart slot odds",
                      "How likely a smart slot outside the overnight window is to happen. The plan prices it at "
                      "these odds."),
    # --- trading -----------------------------------------------------------------------------------------------
    "switch_cost_p": ('number', 2.0, 0, 20, 'p per change', 'Cost of a mode change',
                 'What each change of mode counts as costing. Higher: fewer changes, less profit.'),
    "wear_p": ("number", 0.0, 0, 20, "p/kWh", "Battery wear",
               "Cost counted for each kWh the battery gives out (to the house or sold). 0: a sunk cost."),
    # --- the forecast ------------------------------------------------------------------------------------------
    "forecast_caution": ("choice", "balanced", CAUTION, None, "", "Forecast caution",
                         "What the battery keeps for the house before it sells while energy is dear, until energy "
                         "is certainly cheap again (a smart slot that may not come does not count). Optimistic: "
                         "nothing kept. Balanced: what the forecast says the house needs. Cautious: enough for a "
                         "busy, dull day."),
    "learn": ("bool", True, None, None, "", "Learn from experience",
              "Learn how often sun and house come in low or high, and the solar forecast's bias."),
    # --- advanced ----------------------------------------------------------------------------------------------
    "hard_floor_margin_pct": ('number', 1, 0, 10, 'points', 'Grid event margin above the hard floor',
                 "A grid event stops this far above the battery's hard floor."),
    "learn_soc_offset": ("bool", True, None, None, "", "Learn the battery reading's offset",
                         "Learn how far the battery reading is off when charging, holding and discharging."),
    "scenario_half_life_days": ("number", 14, 1, 60, "days", "Learning: half-life",
                                "How quickly old days stop counting."),
    "scenario_prior_days": ('number', 7, 0, 60, 'days', 'Learning: weight of the starting values',
                 'The starting weights count as this many days of experience.'),
    "car_start_debounce_s": ('int', 60, 0, 600, 's', 'Car start debounce',
                 'The car must report charging this long before it counts.'),
    "car_stop_debounce_s": ("int", 30, 0, 600, "s", "Car stop debounce",
                            "The car must report not charging this long before it counts as stopped."),
    "stale_after_s": ("int", 180, 30, 1800, "s", "Missing reading grace",
                      "A required reading missing for this long counts as missing data."),
    "soc_filter_gain": ('number', 0.05, 0.001, 1, '', 'Battery level filter gain',
                 'How fast the filtered battery level follows the reading.'),
    "price_churn_hold_s": ('int', 60, 0, 300, 's', 'Price churn hold',
                 'A smart-slot change and a price change wait this long for each other, so one re-plan sees both.'),
    "sample_s": ("int", 10, 5, 60, "s", "Sample interval",
                 "How often the readings are checked against the conditions."),
    "preview_when_v1": ('bool', True, None, None, '', 'Preview while engine v1 is in control',
                 'Show what engine v2 would do while v1 is in control. Nothing is sent.'),
}

# config-page sections, in display order: (key, label, settings)
SECTIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("allowed", "What the battery may do",
     ("arbitrage", "events", "event_value_p", "event_plus_export", "free_power", "free_arbitrage", "hold_use",
      "charge_ceiling_soc", "charge_max_import_p")),
    ("range", "The battery's range",
     ("reserve_soc", "range_low_soc", "range_high_soc", "range_firmness", "min_run_pct", "slot_floor_soc",
      "slot_odds_pct")),
    ("trading", "Trading", ("switch_cost_p", "wear_p")),
    ("forecast", "Forecast", ("forecast_caution", "learn")),
    ("advanced", "Advanced",
     ("hard_floor_margin_pct", "learn_soc_offset", "scenario_half_life_days", "scenario_prior_days",
      "car_start_debounce_s", "car_stop_debounce_s", "stale_after_s", "soc_filter_gain", "price_churn_hold_s",
      "sample_s", "preview_when_v1")),
)
# v2 key -> (v1 config part, v1 key): where a missing v2 setting takes its first value from
SEED_FROM = {
    "arbitrage": ("features", "arbitrage"),
    "events": ("features", "axle"),
    "event_plus_export": ("features", "axle_plus_export"),
    "free_power": ("features", "free_power_days"),
    "reserve_soc": ("safety", "min_reserve_soc"),
    "slot_odds_pct": ("safety", "smart_slot_certainty_pct"),
}

# What each choice means to the engine's own tuning
# firmness -> (top rate at 100% p/kWh/h, top points per doubling, bottom rate at the reserve, its points per doubling)
FIRMNESS_CURVES = {"off": (0.0, 2.0, 0.0, 5.0), "gentle": (50.0, 3.0, 3.0, 5.0), "firm": (200.0, 3.0, 10.0, 5.0),
                   "strict": (500.0, 2.0, 50.0, 4.0)}
SLOT_BOT_FACTOR = 30.0          # inside a smart slot the bottom curve's rate is this many times the usual
# the starting weights of the low, middle and high sun and house (learning moves them from there)
START_WEIGHTS = (25, 50, 25)


class SettingsError(ValueError):
    pass


@dataclass(frozen=True)
class V2Settings:
    arbitrage: bool = False
    events: bool = True
    event_value_p: float = 100.0
    event_plus_export: bool = True
    free_power: bool = True
    free_arbitrage: bool = False
    hold_use: str = "cheap times"
    charge_ceiling_soc: float = 100
    charge_max_import_p: float = 0.0
    reserve_soc: float = 12
    range_low_soc: float = 20
    range_high_soc: float = 90
    range_firmness: str = "gentle"
    min_run_pct: float = 0
    slot_floor_soc: float = 0
    slot_odds_pct: float = 60
    switch_cost_p: float = 2.0
    wear_p: float = 0.0
    forecast_caution: str = "balanced"
    learn: bool = True
    hard_floor_margin_pct: float = 1
    learn_soc_offset: bool = True
    scenario_half_life_days: float = 14
    scenario_prior_days: float = 7
    car_start_debounce_s: int = 60
    car_stop_debounce_s: int = 30
    stale_after_s: int = 180
    soc_filter_gain: float = 0.05
    price_churn_hold_s: int = 60
    sample_s: int = 10
    preview_when_v1: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    # ---- the engine's own tuning, from what the settings say ---------------------------------------------------
    @property
    def hold_only_car_reserve(self) -> bool:
        return self.hold_use == "car and reserve"

    @property
    def prefer_self_use(self) -> bool:
        """By day Hold only at cheap times (with the car or at the reserve always)."""
        return self.hold_use == "cheap times"

    @property
    def level_top_start_soc(self) -> float:
        return self.range_high_soc

    @property
    def level_top_rate_p(self) -> float:
        return FIRMNESS_CURVES[self.range_firmness][0]

    @property
    def level_top_doubling_pts(self) -> float:
        return FIRMNESS_CURVES[self.range_firmness][1]

    @property
    def level_bot_start_soc(self) -> float:
        return self.range_low_soc

    @property
    def level_bot_rate_p(self) -> float:
        return FIRMNESS_CURVES[self.range_firmness][2]

    @property
    def level_bot_doubling_pts(self) -> float:
        return FIRMNESS_CURVES[self.range_firmness][3]

    @property
    def slot_bot_factor(self) -> float:
        return SLOT_BOT_FACTOR if self.slot_floor_soc > 0 else 1.0

    @property
    def slot_bot_start_soc(self) -> float:
        return self.slot_floor_soc

    @property
    def wear_house_p(self) -> float:
        return self.wear_p

    @property
    def wear_sale_p(self) -> float:
        return self.wear_p

    @property
    def learn_scenario_weights(self) -> bool:
        return self.learn

    @property
    def learn_solar_bias(self) -> bool:
        return self.learn

    @property
    def solar_weights(self) -> tuple[float, float, float]:
        """Starting weights of the low, middle and high solar scenarios, as fractions summing to 1."""
        return _norm(*START_WEIGHTS)

    @property
    def load_weights(self) -> tuple[float, float, float]:
        return _norm(*START_WEIGHTS)



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
        "hold_use": "any time", "range_low_soc": 70, "range_high_soc": 90, "range_firmness": "firm",
        "min_run_pct": 5, "slot_floor_soc": 80,
    },
}
# 2.2 beta's range is gentle, not firm: on 7-16 Oct the replays cost about 1.10 GBP less with 26 fewer mode changes, and
# the same on dull synthetic days (20 Oct). Removing the range cost money.
BUILTIN_PROFILES[BETA22_PROFILE] = {**BUILTIN_PROFILES[BETA_PROFILE], "hold_use": "car and reserve",
                                    "range_firmness": "gentle", "free_arbitrage": True, "charge_max_import_p": 15.0}
BUILTIN_NOTES = {
    DEFAULT_PROFILE: "Engine v2's own defaults.",
    BETA_PROFILE: "A firm range of 70 to 90%, charges and sales of at least 5 points, and the battery kept above 80% "
                  "while a smart slot is counted on. Beta.",
    BETA22_PROFILE: "2.1 beta with a gentle range, and Hold only for the car and the reserve, free power that may also "
                    "sell, and no grid charge above 15p. Beta.",
}

# Settings that belong to the house, not to a way of tuning the engine: a profile never holds or changes them.
SITE_KEYS = frozenset({
    "arbitrage", "events", "event_plus_export", "free_power", "preview_when_v1", "charge_ceiling_soc",
    "reserve_soc", "hard_floor_margin_pct", "wear_p", "event_value_p", "slot_odds_pct",
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
        values = {k: v for k, v in _migrate(values).items() if k not in RETIRED}
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
                     "late_events", "late_events_per_week", "late_event_hours",   # 0.9.148: unannounced events
                     # 0.9.156: settings that changed nothing measurable in the replays of 8 to 15 Oct; the first four
                     # are gone, the rest are constants now (the module that uses each names it)
                     "replan_on_level", "terminal_value", "terminal_value_p", "reentry_levy_p",
                     "corridor_cost_p", "max_segment_min", "forecast_change_pct", "drift_kwh", "band_exit_min",
                     "revalue_coalesce_s", "max_value_age_min", "deadline_grace_min", "debounce_s", "level_step_kwh",
                     # 0.9.157: one decision for the plan and the executor; these only tuned the live comparison
                     "executor", "price_band_p", "level_band_pct", "min_dwell_s", "reversal_levy_p",
                     # 0.9.157: settings that say what you want instead of how (`_migrate` carries the old values over
                     # where a new setting means the same)
                     "comfort_low_soc", "comfort_high_soc", "comfort_cost_p", "top_up_cost_p",
                     "level_top_start_soc", "level_top_rate_p", "level_top_doubling_pts",
                     "level_bot_start_soc", "level_bot_rate_p", "level_bot_doubling_pts",
                     "slot_bot_factor", "slot_bot_start_soc", "prefer_self_use", "hold_only_car_reserve",
                     "wear_house_p", "wear_sale_p", "solar_low_pct", "solar_mid_pct", "solar_high_pct",
                     "load_low_pct", "load_mid_pct", "load_high_pct", "learn_scenario_weights", "learn_solar_bias"})


def _migrate(raw: dict) -> dict:
    """Settings saved before 0.9.157, in the new terms where a new setting means the same thing (the rest of the old
    keys are dropped as RETIRED)."""
    out = dict(raw)
    for old, new in (("level_bot_start_soc", "range_low_soc"), ("level_top_start_soc", "range_high_soc"),
                     ("slot_bot_start_soc", "slot_floor_soc")):
        if old in out and new not in out:
            out[new] = out[old]
    if "hold_use" not in out and ("hold_only_car_reserve" in out or "prefer_self_use" in out):
        out["hold_use"] = ("car and reserve" if out.get("hold_only_car_reserve")
                           else "cheap times" if out.get("prefer_self_use", True) else "any time")
    if "wear_p" not in out and ("wear_house_p" in out or "wear_sale_p" in out):
        out["wear_p"] = max(out.get("wear_house_p") or 0, out.get("wear_sale_p") or 0)
    if "learn" not in out and "learn_scenario_weights" in out:
        out["learn"] = bool(out["learn_scenario_weights"])
    if "range_firmness" not in out and "level_bot_rate_p" in out:
        rate = out["level_bot_rate_p"] or 0
        out["range_firmness"] = "off" if rate <= 0 else min(
            (f for f in FIRMNESS if f != "off"), key=lambda f: abs(FIRMNESS_CURVES[f][2] - rate))
    return out


def parse_v2(raw: Any, features: dict | None = None, safety: dict | None = None,
             profile: dict | None = None) -> V2Settings:
    """The `engine_v2:` block, validated. Missing keys come from the profile in use (`profile`: its values, see
    `profile_values`), else v1's equivalent (`SEED_FROM`), else the default. Raises SettingsError for an unknown key, a
    wrong type, a value out of range, or settings that contradict."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise SettingsError("'engine_v2' must be a mapping")
    raw = {k: v for k, v in _migrate(raw).items() if k not in RETIRED}   # a setting an earlier release had: ignored
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
    if values["range_low_soc"] >= values["range_high_soc"]:
        raise SettingsError("the bottom of the everyday range must be below its top")
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
