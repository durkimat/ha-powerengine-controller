"""Keep the managed dashboard file in HA's config folder up to date.

The Energy flow card (a custom:sunsynk-power-flow-card) is generated from the configured solar plant(s) rather
than shipped hard-coded: `energy_flow_card` builds its YAML, and `sync_dashboard` splices it into the shipped
dashboard between two marker lines before comparing and writing.
"""

from __future__ import annotations

import json
import os

from .names import fill

# Shipped as .lovelace, not .yaml: AppDaemon would try to load a .yaml here as app config.
SOURCE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dashboard", "dashboard.lovelace")

BEGIN_MARKER = "# BEGIN energy-flow (generated from your configuration; edits here are replaced)"
END_MARKER = "# END energy-flow"

# Sunsynk Power Flow Card entity keys
PV_KEYS = ("pv1_power_186", "pv2_power_187", "pv3_power_111", "pv4_power_112", "pv5_power_113")
MAX_PANELS = len(PV_KEYS)


def _num(value) -> str:
    """A number for YAML: as a plain int when it's a whole number, else as given."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(f)) if f == int(f) else str(value)


def energy_flow_card(plants, capacity_wh, reserve_soc, has_ev, inverter_model, indent: str = "          ") -> str:
    """The Energy flow card's YAML (a custom:sunsynk-power-flow-card), at `indent`, from the configured plant(s).

    `plants` is `cfg.solar_plants` (only the enabled ones are used); `capacity_wh` and `reserve_soc` are the
    battery's usable capacity (Wh) and reserve SoC (%); `has_ev` is whether an EV charger role is mapped;
    `inverter_model` is the card's inverter brand key (e.g. "solis"). Output is byte-identical to the shipped
    default for today's config (one plant, 18000 Wh, 12%, an EV, solis).
    """
    enabled = [p for p in plants if p.enabled]
    n = min(len(enabled), MAX_PANELS)
    i2, i4 = indent + "  ", indent + "    "
    lines = [
        f"{indent}- type: custom:sunsynk-power-flow-card",
        f"{i2}grid_options: {{columns: full}}",
        f"{i2}cardstyle: lite",
        f"{i2}show_solar: {'true' if enabled else 'false'}",
        f"{i2}inverter:",
        f"{i4}model: {inverter_model}",
        f"{i4}modern: false",
        f'{i4}autarky: "no"',
        f"{i2}battery:",
        f"{i4}energy: {_num(capacity_wh)}",
        f"{i4}shutdown_soc: {_num(reserve_soc)}",
        f"{i4}# PowerEngine's battery power is + discharging; this card animates it the other way unless inverted",
        f"{i4}invert_power: true",
    ]
    if enabled:
        lines.append(f"{i2}solar:")
        lines.append(f"{i4}mppts: {n}")
        if n > 1:
            for idx, plant in enumerate(enabled[:n], start=1):
                lines.append(f"{i4}pv{idx}_name: {json.dumps(plant.name)}")
        if len(enabled) > MAX_PANELS:
            extra = len(enabled) - MAX_PANELS
            verb = "feed" if extra != 1 else "feeds"
            lines.append(f"{i4}# {extra} more plant{'s' if extra != 1 else ''} {verb} the total only "
                         f"(the card shows at most {MAX_PANELS} panels)")
    lines.append(f"{i2}load:")
    lines.append(f"{i4}show_daily: false")
    if has_ev:
        lines.append(f"{i4}additional_loads: 1")
        lines.append(f"{i4}load1_name: Car")
        lines.append(f"{i4}load1_icon: mdi:car-electric")
    else:
        lines.append(f"{i4}additional_loads: 0")
    lines.append(f"{i2}grid:")
    lines.append(f"{i4}show_nonessential: false")
    lines.append(f"{i2}entities:")
    lines.append(f"{i4}battery_power_190: sensor.pe_state_battery_power")
    lines.append(f"{i4}battery_soc_184: sensor.pe_state_battery_soc")
    lines.append(f"{i4}grid_ct_power_172: sensor.pe_state_grid_power")
    if has_ev:
        lines.append(f"{i4}# the load circle is the total (house + car); the car is shown as one of the loads "
                     "within it")
    lines.append(f"{i4}essential_power: sensor.pe_state_load_power")
    if has_ev:
        lines.append(f"{i4}essential_load1: sensor.pe_state_ev_power")
    if len(enabled) == 1:
        lines.append(f"{i4}{PV_KEYS[0]}: sensor.pe_state_solar_power")
    elif len(enabled) >= 2:
        for idx, plant in enumerate(enabled[:n], start=1):
            lines.append(f"{i4}{PV_KEYS[idx - 1]}: sensor.pe_state_solar_{plant.id}_power")
    return "\n".join(lines) + "\n"


def _splice(text: str, card: str) -> str:
    """Replace the lines between the BEGIN/END markers (kept as-is) with `card`'s lines."""
    lines = text.split("\n")
    begin_i = next(i for i, ln in enumerate(lines) if ln.strip() == BEGIN_MARKER)
    end_i = next(i for i, ln in enumerate(lines) if ln.strip() == END_MARKER)
    card_lines = card.rstrip("\n").split("\n")
    return "\n".join(lines[:begin_i + 1] + card_lines + lines[end_i:])


def render(text: str, card: str | None = None, names: dict[str, str] | None = None) -> str:
    """The shipped dashboard as written to HA: the Energy flow card spliced in, then every `<<term>>` placeholder
    replaced by the user's supplier or device name (an unknown placeholder raises, so it can't ship)."""
    if card is not None:
        text = _splice(text, card)
    return fill(text, names)


def sync_dashboard(target: str, source: str = SOURCE, card: str | None = None,
                   names: dict[str, str] | None = None) -> bool:
    """Copy the shipped dashboard to `target` if it differs. With `card` (the Energy flow card's YAML, from
    `energy_flow_card`), the text between the markers is replaced with it first; `<<term>>` placeholders are filled
    from `names` (the current names map by default). Returns True if written."""
    with open(source, encoding="utf-8") as fh:
        wanted = render(fh.read(), card, names)
    try:
        with open(target, encoding="utf-8") as fh:
            if fh.read() == wanted:
                return False
    except FileNotFoundError:
        pass
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(wanted)
    os.replace(tmp, target)
    return True
