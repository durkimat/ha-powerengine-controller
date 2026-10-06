"""The catalogue of inputs ("roles") PowerEngine reads, and the outputs it may control.

Single source of truth: the config card reads this catalogue from the app, so
descriptions, units, sign conventions and suggestions live here only.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace

from .adapters.definition import role_suggestions
from .names import fill

# kind -> what the value is; drives unit checks and how the card shows it
KINDS = ("power", "energy", "percent", "rate", "money", "text", "binary", "timestamp", "list", "static", "control", "temperature")

UNITS = {
    "power": ("W", "kW"),
    "energy": ("kWh", "Wh"),
    "percent": ("%",),
    "rate": ("GBP/kWh", "£/kWh", "p/kWh"),
    "money": ("GBP", "GBP/day", "£"),
    "temperature": ("°C",),
}

# Entities PowerEngine must never write to, whatever the config says.
FORBIDDEN_CONTROL_WORDS = ("bump", "boost")


@dataclass(frozen=True)
class Role:
    key: str
    group: str
    label: str
    description: str
    kind: str
    required: str = "yes"          # "yes" | "no" | feature name ("axle", "free_power")
    domains: tuple[str, ...] = ("sensor",)
    signed: bool = False
    sign_note: str = ""            # what + and - mean to PowerEngine
    attribute: str = ""            # read this attribute instead of the state
    static_ok: bool = False        # may be a fixed value instead of an entity
    static_unit: str = ""
    suggest: tuple[str, ...] = ()  # regex patterns for a pre-filled suggestion
    suggest_not: tuple[str, ...] = ()
    suggest_static: float | None = None
    unknown_ok: bool = False       # 'unknown' is a normal idle state (e.g. no event scheduled)
    measurable: bool = False       # PowerEngine measures it; the config can choose the measured figure instead

    def as_dict(self) -> dict:
        d = asdict(self)
        d["label"], d["description"] = fill(self.label), fill(self.description)     # names from the adapters
        d.pop("group")                           # the card places roles by its own topic list; not needed
        if self.domains == ("sensor",):          # the card's default; left out to keep the catalogue small
            d.pop("domains")
        if self.required == "yes":               # likewise: the card treats a missing "required" as "yes"
            d.pop("required")
        return {k: v for k, v in d.items() if v not in ("", (), None, False) or k in ("key", "required")}


GROUPS = (
    ("battery", "Battery and inverter"),
    ("grid", "Grid and house"),
    ("solar", "Solar forecast"),
    ("tariff", "Tariff"),
    ("ev", "EV charger"),
    ("smart", "<<smart_charge>>"),
    ("axle", "Grid events"),
    ("free", "Free-power sessions"),
    ("controls", "Control outputs (Active mode only)"),
    ("handover", "Handover guards (Active mode only)"),
)

_GUARD = ("switch", "input_boolean", "automation", "binary_sensor")
_E = r"^sensor\.edf_energy_electricity_"

_BASE_ROLES: tuple[Role, ...] = (
    # --- battery ---
    Role("battery_soc", "battery", "Battery state of charge", "How full the battery is. Used by every decision.",
         "percent"),
    Role("battery_power", "battery", "Battery power", "Live charge/discharge power (unless the pair below is mapped).",
         "power", signed=True, sign_note="+ discharging, - charging"),
    Role("battery_charge_power", "battery", "Battery charging power", "If Battery power has no sign: power in.",
         "power", required="no"),
    Role("battery_discharge_power", "battery", "Battery discharging power", "If Battery power has no sign: power out.",
         "power", required="no"),
    Role("battery_capacity", "battery", "Usable battery capacity", "Energy the battery can actually deliver.",
         "static", static_ok=True, static_unit="kWh", suggest_static=18.0, measurable=True),
    Role("battery_round_trip", "battery", "Battery round-trip efficiency", "Energy out ÷ energy in, all losses included.",
         "static", required="no", static_ok=True, static_unit="%", suggest_static=90.0, measurable=True),
    Role("battery_max_charge_power", "battery", "Max charge power", "Fastest safe charge rate.",
         "static", static_ok=True, static_unit="W", suggest_static=4800, measurable=True),
    Role("battery_max_discharge_power", "battery", "Max discharge power", "Fastest safe discharge rate.",
         "static", static_ok=True, static_unit="W", suggest_static=4800, measurable=True),
    Role("battery_charge_today", "battery", "Battery charged today", "Energy into the battery today, for losses.",
         "energy"),
    Role("battery_discharge_today", "battery", "Battery discharged today", "Energy out of the battery today, for losses.",
         "energy"),
    Role("battery_soh", "battery", "Battery health", "State of health, shown on the Health view.",
         "percent", required="no"),
    Role("outside_temperature", "battery", "Outside temperature", "Local sensor, used now instead of the forecast.",
         "temperature", required="no"),
    Role("battery_temperature", "battery", "Battery temperature", "The battery's own sensor, instead of the estimate.",
         "temperature", required="no"),
    Role("battery_bms_charge_limit", "battery", "BMS charge limit", "The battery's own charge limit (A); commands stay under it.",
         "text", required="no"),
    Role("battery_bms_discharge_limit", "battery", "BMS discharge limit", "The battery's own discharge limit (A).",
         "text", required="no"),
    Role("inverter_clock", "battery", "Inverter clock", "The inverter's own time, to check for drift.",
         "text", required="no"),
    Role("inverter_min_soc", "battery", "Inverter minimum SoC", "The inverter's own floor, as a safety cross-check.",
         "percent", required="no", domains=("number", "sensor")),
    # --- grid and house ---
    Role("grid_power", "grid", "Grid power", "Live import/export at the meter.",
         "power", signed=True, sign_note="+ importing, - exporting"),
    Role("grid_power_reference", "grid", "Check meter", "A second grid meter (e.g. <<ev_charger>> CT).", "power",
         required="no", signed=True, sign_note="+ in, - out", suggest=(r"^sensor\.myenergi_.*_power_grid$",)),
    Role("grid_import_today", "grid", "Grid import today", "Energy imported today, for costs and losses.",
         "energy"),
    Role("grid_export_today", "grid", "Grid export today", "Energy exported today, for costs and losses.",
         "energy"),
    Role("grid_import_today_check", "grid", "Check meter import today", "Preferred over the inverter's figure.",
         "energy", required="no", suggest=(r"^sensor\.myenergi_.*_grid_import_today$",)),
    Role("grid_export_today_check", "grid", "Check meter export today", "Preferred over the inverter's figure.",
         "energy", required="no", suggest=(r"^sensor\.myenergi_.*_grid_export_today$",)),
    Role("house_load_power", "grid", "House load", "Household consumption (the car is subtracted if included).",
         "power"),
    Role("house_load_today", "grid", "House load today", "Household energy used today, for costs and losses.",
         "energy"),
    # --- solar forecast ---
    Role("solar_forecast_today", "solar", "Solar forecast today", "Half-hourly solar forecast for today.",
         "list", attribute="detailedForecast", suggest=(r"^sensor\.solcast_pv_forecast_forecast_today$",)),
    Role("solar_forecast_tomorrow", "solar", "Solar forecast tomorrow", "Half-hourly solar forecast for tomorrow.",
         "list", attribute="detailedForecast", suggest=(r"^sensor\.solcast_pv_forecast_forecast_tomorrow$",)),
    Role("solar_forecast_day3", "solar", "Solar forecast day 3", "Extends planning beyond tomorrow.",
         "list", required="no", attribute="detailedForecast", suggest=(r"^sensor\.solcast_pv_forecast_forecast_day_3$",)),
    # --- tariff ---
    Role("import_rate_now", "tariff", "Import rate now", "The rate you're paying right now.",
         "rate", suggest=(_E + r".*_current_rate$",), suggest_not=(r"export",)),
    Role("import_rates_today", "tariff", "Import rates today", "Today's half-hourly import rates (slots included).",
         "list", domains=("event", "sensor"), attribute="rates",
         suggest=(r"^event\.edf_energy_electricity_.*_current_day_rates$",), suggest_not=(r"export",)),
    Role("import_rates_tomorrow", "tariff", "Import rates tomorrow", "Tomorrow's half-hourly import rates.",
         "list", domains=("event", "sensor"), attribute="rates",
         suggest=(r"^event\.edf_energy_electricity_.*_next_day_rates$",), suggest_not=(r"export",)),
    Role("export_rate", "tariff", "Export rate", "What you're paid per kWh exported.",
         "rate", static_ok=True, static_unit="GBP/kWh", suggest=(_E + r".*_export_current_rate$",)),
    Role("standing_charge", "tariff", "Standing charge", "Daily fixed charge, for costs.",
         "money", static_ok=True, static_unit="GBP/day",
         suggest=(_E + r".*_current_standing_charge$",), suggest_not=(r"export",)),
    Role("offpeak_now", "tariff", "Off-peak now", "On in the normal off-peak window.",
         "binary", domains=("binary_sensor",),
         suggest=(r"^binary_sensor\.edf_energy_electricity_.*_off_peak$",), suggest_not=(r"export",)),
    # --- EV ---
    Role("ev_plug_status", "ev", "Car plug status", "'Charging' while the car draws power.",
         "text", suggest=(r"^sensor\.myenergi_zappi_.*_plug_status$",)),
    Role("ev_charger_status", "ev", "Charger status", "What the charger is doing, for the status view.",
         "text", suggest=(r"^sensor\.myenergi_zappi_[0-9]+_status$",)),
    Role("ev_charge_power", "ev", "Car charging power", "Live charging power, to separate car from house load.",
         "power", suggest=(r"^sensor\.myenergi_.*_power_charging$",)),
    Role("ev_energy_today", "ev", "Car energy today", "Energy into the car today, for the car's share of costs.",
         "energy", suggest=(r"^sensor\.myenergi_zappi_.*_energy_used_today$",)),
    Role("ev_charge_mode", "ev", "Charger mode", "Charger mode (e.g. Eco+), for the status view.",
         "text", required="no", domains=("select", "sensor"), suggest=(r"^select\.myenergi_zappi_.*_charge_mode$",)),
    Role("ev_session_energy", "ev", "Charge this session", "Energy added this session, for the status tile.",
         "energy", required="no", suggest=(r"^sensor\.myenergi_zappi_.*_charge_added_session$",)),
    # --- EDF smart charge ---
    Role("smart_dispatches", "smart", "Smart-charge dispatches", "Planned and completed smart-charge slots.",
         "list", domains=("binary_sensor",), attribute="planned_dispatches",
         suggest=(r"^binary_sensor\.edf_energy_.*_intelligent_dispatching$",)),
    Role("smart_state", "smart", "Smart-charge state", "<<supplier>>'s smart-charging state, for the status view.",
         "text", required="no", suggest=(r"^sensor\.edf_energy_.*_intelligent_state$",)),
    # --- grid events (run by <<event>>) ---
    Role("axle_event_active", "axle", "Grid event active", "On during a grid event run by <<event>> (triggers force discharge).",
         "binary", required="axle", domains=("sensor", "binary_sensor"),
         suggest=(r"^sensor\.axle_vpp_axle_event_in_progress$",)),
    Role("axle_event_start", "axle", "Grid event start", "Start of the next grid event, to hold charge for it.",
         "timestamp", required="axle", suggest=(r"^sensor\.axle_vpp_axle_start_time_friendly$",)),
    Role("axle_event_end", "axle", "Grid event end", "End of the next grid event.",
         "timestamp", required="axle", suggest=(r"^sensor\.axle_vpp_axle_end_time_friendly$",)),
    Role("axle_direction", "axle", "Grid event direction", "Import or export event (only export is acted on).",
         "text", required="no", unknown_ok=True, suggest=(r"^sensor\.axle_vpp_axle_import_export$",)),
    # --- free power ---
    Role("free_power_active", "free", "Free power now", "On during a free-electricity session.",
         "binary", required="free_power", domains=("binary_sensor",),
         suggest=(r"^binary_sensor\.edf_energy_.*_free_electricity_now$",)),
    Role("free_power_next_start", "free", "Next free session start", "Start of the next free session, for planning.",
         "timestamp", required="free_power", suggest=(r"^sensor\.edf_energy_.*_next_free_electricity_session_start$",)),
    Role("free_power_next_end", "free", "Next free session end", "End of the next free session.",
         "timestamp", required="free_power", suggest=(r"^sensor\.edf_energy_.*_next_free_electricity_session_end$",)),
    # --- control outputs (never written in Passive mode) ---
    # Solis timed charge/discharge slots (pre-FB00 firmware: values apply when the update button is pressed)
    Role("timed_charge_start_hour", "controls", "Charge window start (hour)", "Timed charge window start hour.",
         "control", required="no", domains=("number",)),
    Role("timed_charge_start_minute", "controls", "Charge window start (minute)", "Timed charge window start minute.",
         "control", required="no", domains=("number",)),
    Role("timed_charge_end_hour", "controls", "Charge window end (hour)", "Timed charge window end hour.",
         "control", required="no", domains=("number",)),
    Role("timed_charge_end_minute", "controls", "Charge window end (minute)", "Timed charge window end minute.",
         "control", required="no", domains=("number",)),
    Role("timed_charge_current", "controls", "Charge current", "Timed charge current (A).",
         "control", required="no", domains=("number",)),
    Role("timed_discharge_start_hour", "controls", "Discharge window start (hour)", "Timed discharge window start hour.",
         "control", required="no", domains=("number",)),
    Role("timed_discharge_start_minute", "controls", "Discharge window start (minute)", "Timed discharge start minute.",
         "control", required="no", domains=("number",)),
    Role("timed_discharge_end_hour", "controls", "Discharge window end (hour)", "Timed discharge window end hour.",
         "control", required="no", domains=("number",)),
    Role("timed_discharge_end_minute", "controls", "Discharge window end (minute)", "Timed discharge end minute.",
         "control", required="no", domains=("number",)),
    Role("timed_discharge_current", "controls", "Discharge current", "Timed discharge current (A).",
         "control", required="no", domains=("number",)),
    Role("timed_update_button", "controls", "Apply timed windows", "Button that sends the window times to the inverter.",
         "control", required="no", domains=("button",)),
    Role("storage_mode", "controls", "Storage mode", "Energy storage control switch (Self-Use etc.).",
         "control", required="no", domains=("select",)),
    Role("inverter_clock_sync", "controls", "Sync inverter clock", "Button that sets the inverter's clock to HA's.",
         "control", required="no", domains=("button",)),
    Role("inverter_export_limit", "controls", "Inverter export limit (entity)", "Export (backflow) power limit.",
         "control", required="no", domains=("number",)),
    Role("smart_target_soc", "controls", "Smart-charge target", "Car charge target sent to <<supplier>>.",
         "control", required="no", domains=("number",),
         suggest=(r"^number\.edf_energy_.*_intelligent_charge_target$",)),
    Role("smart_target_time", "controls", "Smart-charge ready-by time", "Ready-by time sent to <<supplier>>.",
         "control", required="no", domains=("select", "time"),
         suggest=(r"^select\.edf_energy_.*_intelligent_target_time$",)),
    # --- handover guards: Active is refused unless every mapped guard is in its safe state ---
    Role("guard_read_only", "handover", "Other controller read-only", "Must be on (e.g. a controller's read-only switch).",
         "binary", required="no", domains=_GUARD, suggest=(r"^switch\.predbat_set_read_only$",)),
    Role("guard_off_1", "handover", "Other control off (1)", "Must be off (another control, such as an automation).",
         "binary", required="no", domains=_GUARD),
    Role("guard_off_2", "handover", "Other control off (2)", "Must be off.",
         "binary", required="no", domains=_GUARD),
)

DEFAULT_INVERTER = "solis"


def roles_for(inverter: str = DEFAULT_INVERTER, firmware: str | None = None) -> tuple[Role, ...]:
    """The catalogue with the entity suggestions that the inverter's definition file adds (its `roles:` section:
    brand-specific `suggest` / `suggest_not` regexes). Suggestions that belong to no brand (the tariff, the car
    charger, the forecast) stay on the roles above."""
    extra = role_suggestions(inverter, firmware)
    unknown = sorted(set(extra) - {r.key for r in _BASE_ROLES})
    if unknown:
        raise ValueError(f"the {inverter} definition suggests entities for unknown role(s): {', '.join(unknown)}")
    out = []
    for role in _BASE_ROLES:
        add = extra.get(role.key)
        if add:
            role = replace(role, suggest=role.suggest + add["suggest"], suggest_not=role.suggest_not + add["suggest_not"])
        out.append(role)
    return tuple(out)


ROLES: tuple[Role, ...] = roles_for()
ROLE_BY_KEY = {r.key: r for r in ROLES}

# Inputs from earlier versions that no longer exist; dropped from a config.yaml on load (not an error).
RETIRED_ROLES = frozenset({"inverter_override", "inverter_override_charge_power", "inverter_override_discharge_power",
                           "inverter_force_charge_soc"})


def is_forbidden_control(entity_id: str) -> bool:
    """True for entities PowerEngine must never write to (bump/boost charging)."""
    eid = entity_id.lower()
    return any(word in eid for word in FORBIDDEN_CONTROL_WORDS)


def catalogue() -> dict:
    """Compact, JSON-safe catalogue for the config card (published as sensor attributes: HA's recorder skips any
    over 16 KiB, so role groups are left out; the card places roles by its own topics)."""
    return {"roles": [r.as_dict() for r in ROLES]}
