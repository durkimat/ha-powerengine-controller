"""The data passed between engine v2's layers: the contract every v2 module and the app build against.

Units, everywhere in engine v2: prices and values in **pence per kWh** (v1 and the readings use GBP/kWh: convert at the
edge, in forecast.py and engine.py), energy in kWh, power in kW (W only in `Decision.power_w`, which layer 6 reads),
battery level in percent (0 to 100), times as timezone-aware datetimes (UTC inside; local only for words).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .. import decide as v1d

# --- modes ------------------------------------------------------------------------------------------------------------
SELF_USE, HOLD, CHARGE, EXPORT, EVENT, FREE, NONE = "self_use", "hold", "charge", "export", "event", "free", "none"
MODES = (SELF_USE, HOLD, CHARGE, EXPORT, EVENT, FREE, NONE)
CHOICE_MODES = (SELF_USE, HOLD, CHARGE, EXPORT)          # what layer 5 chooses between; EVENT and FREE are only forced
DISCHARGE_MODES = (SELF_USE, EXPORT, EVENT)               # modes that may take energy out of the battery
MODE_ACTION = {SELF_USE: v1d.SELF_USE, HOLD: v1d.HOLD, CHARGE: v1d.GRID_CHARGE, EXPORT: v1d.EXPORT,
               EVENT: v1d.FORCE_DISCHARGE, FREE: v1d.GRID_CHARGE, NONE: v1d.NONE}
MODE_LABEL = {SELF_USE: "Self-use", HOLD: "Holding the battery", CHARGE: "Charging from the grid",
              EXPORT: "Selling from the battery", EVENT: "Grid event: exporting", FREE: "Free power: charging",
              NONE: "No decision"}


# --- facts and situation (inputs from the app) -----------------------------------------------------------------------
@dataclass(frozen=True)
class BatteryFacts:
    """The house's battery and supply, as measured or configured (shared with v1; built by the app each tick)."""
    capacity_kwh: float = 18.0
    eta_charge: float = 0.95              # one-way efficiencies (learned when allowed)
    eta_discharge: float = 0.95
    max_charge_kw: float = 4.8            # learned or configured, capped by the RAM limit
    max_discharge_kw: float = 4.8
    taper: tuple = ()                     # ((soc_from, fraction of the charge rate), ...) near full (planner.Params)
    dtaper: tuple = ()                    # ((below soc, fraction of the discharge rate), ...) near empty
    hard_floor_soc: float = 12.0          # the battery's own lowest level (shared battery_floor_soc, or learned)
    export_limit_kw: float = 6.0
    fuse_kw: float = 60 * 0.230 * 0.9     # import limit: 90% of the main fuse
    ev_charger_kw: float = 7.4
    charge_factor: float = 1.0            # cold-battery caution now (fraction of the normal charge rate)
    bms_charge_kw: float | None = None    # live BMS limits, when mapped and usable
    bms_discharge_kw: float | None = None


@dataclass(frozen=True)
class Situation:
    """What isn't a reading but decides what may be done (layer 4)."""
    active: bool = False                  # effective mode Active: decisions are sent (else "would")
    mode_reason: str = ""                 # why not Active, when not
    override: Any = None                  # override.Override in force (Active only), else None
    house_load_includes_ev: bool = True
    control_method: str = "ram_remote"


# --- layer 2: forecast -----------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Spread:
    """A low / middle / high figure and the weights of the three (weights sum to 1)."""
    low: float
    mid: float
    high: float
    w: tuple[float, float, float] = (0.25, 0.5, 0.25)


@dataclass(frozen=True)
class Segment:
    """A stretch of time with one set of prices and events; sun and house load as spreads. Boundaries fall where the
    data changes (a price, a smart slot's real start, an event), and no segment is longer than max_segment_min."""
    start: datetime
    end: datetime
    import_p: float                       # import price if no smart slot happens (p/kWh)
    export_p: float                       # export price (p/kWh)
    solar_kwh: Spread                     # for the whole segment
    load_kwh: Spread                      # house only, no car
    slot_prob: float | None = None        # a smart slot covers this segment with this probability (None: none)
    slot_import_p: float | None = None    # the import price if the slot happens
    event: bool = False                   # grid event: forced export
    event_p: float = 0.0                  # what the event pays per kWh exported (incl. the export rate if paid on top)
    free: bool = False                    # free-power session: forced charge to 100%
    car_kw: float = 0.0                   # car draw expected (only the running segment while the car charges)
    overnight: bool = False               # in the regular overnight cheap window
    price_estimated: bool = False         # beyond the published prices
    charge_factor: float = 1.0            # cold caution
    manual: str | None = None             # an override fixes this segment's mode (a v2 mode key)

    @property
    def hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600


@dataclass(frozen=True)
class Forecast:
    made_at: datetime
    segments: tuple[Segment, ...]
    notes: tuple[str, ...] = ()           # plain-words caveats ("prices after Tue 23:00 are estimated")


# --- layer 4: rules -----------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Limits:
    allowed: frozenset                    # modes layer 5 may choose (subset of CHOICE_MODES), when nothing is forced
    forced: str | None = None             # a mode that must be used (EVENT, FREE, an override's mode)
    floor_soc: float = 12.0               # lowest level a discharge may reach in this situation
    ceiling_soc: float = 100.0            # highest level a grid charge may reach
    charge_cap_kw: float | None = None    # caps on power (fuse, BMS, cold); None: the battery's own limit
    discharge_cap_kw: float | None = None
    rule: str = "plan"                    # short id of the deciding rule (journal, Decision.rule)
    reason: str = ""                      # plain words for any restriction ("car charging: the battery holds")


# --- layer 3: value -----------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Lines:
    """The live comparison, in value terms (p per stored kWh): what a stored kWh is worth at the level now, and the
    real prices turned into lines after losses and wear. Buy while value > buy_line; sell while value < sell_line;
    the battery covers the house while value < use_line; spare sun goes in while value > store_sun_line."""
    value_p: float
    buy_line_p: float                     # import / eta_charge
    sell_line_p: float                    # export * eta_discharge - wear_sale
    use_line_p: float                     # import * eta_discharge - wear_house
    store_sun_line_p: float               # export / eta_charge
    import_p: float
    export_p: float
    charge_target_soc: float | None = None    # where buying stops being worth it now (None: not charging territory)
    sell_floor_soc: float | None = None       # where selling stops being worth it now


@dataclass(frozen=True)
class TimelineItem:
    mode: str
    start: datetime
    end: datetime                         # expected
    level_start: float
    level_end: float
    until: str                            # the condition that ends it, in words ("until 88%", "until 05:30")
    reason: str                           # plain words naming the prices


@dataclass(frozen=True)
class ValueResult:
    made_at: datetime
    because: str                          # the trigger that caused this revalue, in words
    forecast: Forecast
    step_kwh: float                       # level grid step
    lam: tuple[tuple[float, ...], ...]    # lam[k][i]: value (p/kWh) of a stored kWh at segment k start, level i*step
    timeline: tuple[TimelineItem, ...]
    path: dict                            # {"start": iso, "step_min": 15, "mid": [...], "low": [...], "high": [...]}
    cost_expected_p: float                # expected cost of the horizon under the policy (pence)
    cost_selfuse_p: float                 # the same with plain self-use (pence)
    comfort_given_up_p: float | None = None   # cash difference against no comfort cost (None: not worked out)
    calc_s: float = 0.0


# --- layer 1 and the triggers: events -----------------------------------------------------------------------------
# kind -> (revalue?, urgent?). Urgent events bypass the minimum time in a mode.
EVENT_KINDS: dict[str, tuple[bool, bool]] = {
    "start": (True, True),                # engine started or switched to
    "level": (False, False),              # the filtered level reached an exit level (target, floor)
    "reserve": (False, True),             # the level reached the reserve or the hard floor
    "price": (False, False),              # a price boundary passed (published or estimated)
    "slot_start": (False, False),
    "slot_end": (False, False),
    "slots_changed": (True, False),       # smart slots added, moved, withdrawn, cut short
    "prices_published": (True, False),
    "event_changed": (True, True),        # grid event or free power announced or changed
    "event_start": (True, True),
    "event_end": (True, True),
    "free_start": (True, True),
    "free_end": (True, True),
    "car_start": (True, True),
    "car_stop": (True, True),
    "sun_to_short": (False, False),       # spare sun turned to a shortfall (debounced)
    "short_to_sun": (False, False),
    "drift": (True, False),               # CUSUM of actual against forecast net load passed drift_kwh
    "band_exit": (True, False),           # level outside the expected low-high range for band_exit_min
    "forecast_update": (True, False),
    "deadline": (True, False),
    "override": (True, True),
    "mode_switch": (False, True),         # Active / Passive / Pause changed
    "settings": (True, True),
    "learned": (True, False),
    "bms": (False, True),
    "data_missing": (False, True),
    "data_back": (True, True),
    "backstop": (True, False),
}


@dataclass(frozen=True)
class Event:
    at: datetime
    kind: str                             # a key of EVENT_KINDS
    text: str                             # plain words ("the cheap rate started at 23:30")

    @property
    def revalue(self) -> bool:
        return EVENT_KINDS[self.kind][0]

    @property
    def urgent(self) -> bool:
        return EVENT_KINDS[self.kind][1]


@dataclass(frozen=True)
class Observation:
    now: datetime
    level_reported: float | None          # the inverter's reading (%)
    level_filtered: float | None          # layer 1's estimate (%)
    net_load_kw: float | None             # house + car - solar, + needs energy
    car_charging: bool                    # debounced
    data_ok: bool                         # the required readings are there (or missing for less than stale_after_s)
    missing: tuple[str, ...] = ()
    events: tuple[Event, ...] = ()


# --- layer 5: the mode --------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Exit:
    kind: str                             # "level", "price", "car", "event", "deadline", "change"
    text: str                             # "The battery reaches 88%"
    expected_at: datetime | None = None


@dataclass(frozen=True)
class ModeState:
    mode: str
    since: datetime
    why: str                              # plain words naming prices and value
    rule: str
    chosen_by: str                        # the event kind that led to this mode
    target_soc: float | None = None       # charge: where it stops; export: the sell floor
    power_w: float | None = None
    exits: tuple[Exit, ...] = ()
    deadline: datetime | None = None
    lines: Lines | None = None


# --- the facade ---------------------------------------------------------------------------------------------------
@dataclass
class StepInput:
    """What the app hands the engine each tick (built in the app from what v1's cycle already reads)."""
    now: datetime
    readings: Any                         # readings.Readings (GBP/kWh prices; the engine converts)
    facts: BatteryFacts
    situation: Situation
    tz: Any                               # local zone, for words and the overnight window
    solar_points: list = field(default_factory=list)     # adapters.base.ForecastPoint (with low/high bands)
    load_profile: Any = None              # forecast.LoadProfile (shared with v1)
    slot_certainty: Callable[[datetime, datetime | None], float] | None = None   # Certainty.score(start, first_seen)
    slot_first_seen: dict = field(default_factory=dict)  # dispatch start iso -> first seen iso
    overnight: set = field(default_factory=set)          # half-hours of the day in the overnight window
    settings_changed: bool = False        # the v2 settings were saved since the last tick
    learned_changed: bool = False         # shared learned facts changed (efficiency, limits, profile)
    slots_whole_house: bool = True        # the supplier gives the house the smart-slot rate (v1 `slots_whole_house`)


@dataclass
class StepOutput:
    decision: Any                         # decide.Decision for layer 6 (always present; the current mode's decision)
    mode: ModeState
    value: ValueResult | None             # the latest value result (None until the first revalue finishes)
    observation: Observation
    events: tuple[Event, ...] = ()        # events handled this tick
    revalued: bool = False
    mode_changed: bool = False
    journal: tuple[dict, ...] = ()        # new journal rows (mode changes and revalues)
