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
    "arbitrage": ("bool", False, None, None, "", "Sell from the battery",
                  "Export stored energy outside grid events when a kWh sold is worth more than keeping it."),
    "events": ("bool", True, None, None, "", "Grid events",
               "Act on grid events (<<event>>): export at full power while one runs."),
    "event_plus_export": ("bool", True, None, None, "", "Export rate paid on top of a grid event",
                          "The supplier's export rate is paid on top of the grid event's own rate."),
    "free_power": ("bool", True, None, None, "", "Free-power sessions", "Charge to 100% during a free-power session."),
    "charge_ceiling_soc": ("number", 100, 10, 100, "%", "Highest grid charge",
                           "A charge from the grid never goes above this. Sun can still fill the battery."),
    # --- floors ------------------------------------------------------------------------------------------------
    "reserve_soc": ("number", 12, 0, 100, "%", "Your reserve",
                    "Kept back in normal running. Grid events may go below it (never below the battery's hard floor)."),
    "hard_floor_margin_pct": ("number", 1, 0, 10, "points", "Grid event margin above the hard floor",
                              "A grid event stops this far above the battery's hard floor, so the battery's "
                              "own cut-off never ends it."),
    # --- value and costs ---------------------------------------------------------------------------------------
    "wear_house_p": ("number", 0.0, 0, 20, "p/kWh", "Wear: battery supplying the house",
                     "Cost counted per kWh the battery supplies to the house. 0 treats the battery as a sunk cost."),
    "wear_sale_p": ("number", 0.0, 0, 20, "p/kWh", "Wear: sales",
                    "Cost counted per kWh the battery sells (exports and grid events)."),
    "event_value_p": ("number", 100.0, 0, 500, "p/kWh", "Grid event pays",
                      "What a grid event pays per kWh exported, before any export rate on top."),
    "terminal_value": ("choice", "refill", ("refill", "fixed"), None, "", "Energy left at the end of the look-ahead",
                       "Refill: worth the cheapest import price expected near the end, after losses. Fixed: the figure "
                       "below."),
    "terminal_value_p": ("number", 10.0, 0, 100, "p/kWh", "Fixed value of energy left",
                         "Used when the energy left at the end is set to Fixed."),
    # --- comfort band ------------------------------------------------------------------------------------------
    "comfort_low_soc": ("number", 20, 0, 100, "%", "Comfort band: lower edge",
                        "Below this the battery pays the comfort cost for each hour it stays there."),
    "comfort_high_soc": ("number", 90, 0, 100, "%", "Comfort band: upper edge",
                         "Above this the battery pays the comfort cost for each hour it stays there."),
    "comfort_cost_p": ("number", 0.3, 0, 10, "p per kWh per hour", "Comfort cost",
                       "The price of each kWh held outside the band for an hour: a soft guide inside the plan, not a "
                       "limit. 0 switches the band off."),
    # --- forecast caution and learning -------------------------------------------------------------------------
    "solar_low_pct": ("number", 25, 0, 100, "%", "Solar: weight of the low forecast", W0),
    "solar_mid_pct": ("number", 50, 0, 100, "%", "Solar: weight of the middle forecast", W0),
    "solar_high_pct": ("number", 25, 0, 100, "%", "Solar: weight of the high forecast", W0),
    "load_low_pct": ("number", 25, 0, 100, "%", "House load: weight of the low figure", W0),
    "load_mid_pct": ("number", 50, 0, 100, "%", "House load: weight of the middle figure", W0),
    "load_high_pct": ("number", 25, 0, 100, "%", "House load: weight of the high figure", W0),
    "learn_scenario_weights": ("bool", True, None, None, "", "Learn the weights from experience",
                               "Move the weights towards how often the sun and the house came in low, middle or high."),
    "scenario_half_life_days": ("number", 14, 1, 60, "days", "Learning: half-life",
                                "How quickly old days stop counting."),
    "scenario_prior_days": ("number", 7, 0, 60, "days", "Learning: weight of the starting values",
                            "The starting weights count as this many days of experience."),
    "learn_solar_bias": ("bool", True, None, None, "", "Learn the solar forecast's bias",
                         "Scale the solar forecast by how it has compared with the real sun over the last 14 days."),
    "learn_soc_offset": ("bool", True, None, None, "", "Learn the battery reading's offset",
                         "Learn how far the battery reading is off while charging, holding and discharging."),
    # --- responsiveness ----------------------------------------------------------------------------------------
    "price_band_p": ("number", 0.5, 0, 5, "p/kWh", "Price band",
                     "A mode starts only when better by this much, and stops only when worse by this much."),
    "level_band_pct": ("number", 1.0, 0, 5, "points", "Level band",
                       "A charge that reached its target restarts only this far below it (the same for a sale)."),
    "min_dwell_s": ("int", 120, 0, 3600, "s", "Shortest time in a mode",
                    "No change of mode sooner than this after the last one, except for safety events."),
    "deadline_grace_min": ("number", 10, 1, 60, "min", "Deadline grace",
                           "How long a charge or sale may run past its expected end before the engine looks again."),
    "debounce_s": ("int", 20, 0, 300, "s", "Reading debounce", "A changed reading must last this long to count."),
    "car_start_debounce_s": ("int", 60, 0, 600, "s", "Car start debounce",
                             "The car must report charging this long before it counts (car wake-ups are 15 to 70 s)."),
    "car_stop_debounce_s": ("int", 30, 0, 600, "s", "Car stop debounce",
                            "The car must report not charging this long before it counts as stopped."),
    "stale_after_s": ("int", 180, 30, 1800, "s", "Missing reading grace",
                      "A required reading missing for this long counts as missing data."),
    "soc_filter_gain": ("number", 0.05, 0.001, 1, "", "Battery level filter gain",
                        "How quickly the filtered battery level is pulled to the reading at each sample."),
    "drift_kwh": ("number", 0.75, 0.1, 5, "kWh", "Forecast drift",
                  "Work the values out again when what happened differs from the forecast by this much."),
    "band_exit_min": ("number", 10, 1, 60, "min", "Outside the expected range",
                      "Work the values out again when the battery has been outside its expected range this long."),
    "forecast_change_pct": ("number", 10, 1, 100, "%", "Solar forecast change",
                            "Work the values out again when a forecast update moves the rest of the day by this much."),
    "revalue_coalesce_s": ("int", 10, 0, 120, "s", "Batch window",
                           "Reasons to work the values out arriving this close together run once."),
    "max_value_age_min": ("int", 120, 10, 720, "min", "Backstop",
                          "Work the values out at least this often, in case a trigger was missed."),
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
     ("arbitrage", "events", "event_plus_export", "free_power", "charge_ceiling_soc")),
    ("floors", "Floors", ("reserve_soc", "hard_floor_margin_pct")),
    ("comfort", "Comfort band", ("comfort_low_soc", "comfort_high_soc", "comfort_cost_p")),
    ("value", "Value and costs",
     ("wear_house_p", "wear_sale_p", "event_value_p", "terminal_value", "terminal_value_p")),
    ("forecast", "Forecast caution and learning",
     ("solar_low_pct", "solar_mid_pct", "solar_high_pct", "load_low_pct", "load_mid_pct", "load_high_pct",
      "learn_scenario_weights", "scenario_half_life_days", "scenario_prior_days", "learn_solar_bias",
      "learn_soc_offset")),
    ("response", "Responsiveness",
     ("price_band_p", "level_band_pct", "min_dwell_s", "deadline_grace_min", "debounce_s", "car_start_debounce_s",
      "car_stop_debounce_s", "stale_after_s", "soc_filter_gain", "drift_kwh", "band_exit_min", "forecast_change_pct",
      "revalue_coalesce_s", "max_value_age_min", "sample_s")),
    ("model", "Model", ("level_step_kwh", "max_segment_min")),
)

# v2 key -> (v1 config part, v1 key): where a missing v2 setting takes its first value from
SEED_FROM = {
    "arbitrage": ("features", "arbitrage"),
    "events": ("features", "axle"),
    "event_plus_export": ("features", "axle_plus_export"),
    "free_power": ("features", "free_power_days"),
    "reserve_soc": ("safety", "min_reserve_soc"),
}


class SettingsError(ValueError):
    pass


@dataclass(frozen=True)
class V2Settings:
    arbitrage: bool = False
    events: bool = True
    event_plus_export: bool = True
    free_power: bool = True
    charge_ceiling_soc: float = 100
    reserve_soc: float = 12
    hard_floor_margin_pct: float = 1
    wear_house_p: float = 0.0
    wear_sale_p: float = 0.0
    event_value_p: float = 100.0
    terminal_value: str = "refill"
    terminal_value_p: float = 10.0
    comfort_low_soc: float = 20
    comfort_high_soc: float = 90
    comfort_cost_p: float = 0.3
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
    price_band_p: float = 0.5
    level_band_pct: float = 1.0
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


def parse_v2(raw: Any, features: dict | None = None, safety: dict | None = None) -> V2Settings:
    """The `engine_v2:` block, validated. Missing keys come from v1's equivalent (`SEED_FROM`), else the default.
    Raises SettingsError for an unknown key, a wrong type, a value out of range, or settings that contradict."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise SettingsError("'engine_v2' must be a mapping")
    unknown = sorted(set(raw) - set(SETTINGS))
    if unknown:
        raise SettingsError(f"unknown engine v2 setting(s): {', '.join(unknown)}")
    v1 = {"features": features or {}, "safety": safety or {}}
    values: dict[str, Any] = {}
    for key, spec in SETTINGS.items():
        if key in raw:
            values[key] = _check(key, raw[key])
        elif key in SEED_FROM and SEED_FROM[key][1] in v1[SEED_FROM[key][0]]:
            values[key] = _check(key, v1[SEED_FROM[key][0]][SEED_FROM[key][1]])
        else:
            values[key] = spec[1]
    if values["comfort_low_soc"] >= values["comfort_high_soc"]:
        raise SettingsError("the comfort band's lower edge must be below its upper edge")
    for part in ("solar", "load"):
        if values[f"{part}_low_pct"] + values[f"{part}_mid_pct"] + values[f"{part}_high_pct"] <= 0:
            raise SettingsError(f"the {part} weights can't all be 0")
    return V2Settings(**values)


def catalogue(current: V2Settings | None = None) -> dict:
    """The schema for the config page (published on sensor.pe_diag_v2_settings): every setting with its default,
    range, label and help, the sections, and the values in use now."""
    rows = []
    for key, (kind, default, lo, hi, unit, label, help_) in SETTINGS.items():
        row = {"key": key, "kind": kind, "default": default, "unit": unit, "label": fill(label), "help": fill(help_)}
        if kind == "choice":
            row["options"] = list(lo)
        elif kind != "bool":
            row["min"], row["max"] = lo, hi
        rows.append(row)
    return {"settings": rows,
            "sections": [{"key": k, "label": fill(label), "keys": list(keys)} for k, label, keys in SECTIONS],
            "values": (current or V2Settings()).as_dict()}
