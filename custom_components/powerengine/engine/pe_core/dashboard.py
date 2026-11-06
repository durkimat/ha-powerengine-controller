"""Keep the managed dashboard file in HA's config folder up to date.

The Energy flow card (a `custom:powerengine-flow-card`, part of PowerEngine's own card) is generated from the configured
solar plant(s) rather than shipped hard-coded: `energy_flow_card` builds its YAML, and `sync_dashboard` splices it into
the shipped dashboard between two marker lines before comparing and writing.
"""

from __future__ import annotations

import json
import os

from .names import fill

# The shipped dashboard (a template: the engine fills it in and writes it to the settings folder as dashboard.yaml).
SOURCE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dashboard", "dashboard.yaml")

BEGIN_MARKER = "# BEGIN energy-flow (generated from your configuration; edits here are replaced)"
END_MARKER = "# END energy-flow"
VIEW_MARKS = ("# BEGIN view-engine-", "# END view-engine-")      # round an engine page; never written out


def _num(value) -> str:
    """A number for YAML: as a plain int when it's a whole number, else as given."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(f)) if f == int(f) else str(value)


def energy_flow_card(plants, capacity_wh, reserve_soc, has_ev, indent: str = "          ") -> str:
    """The Energy flow card's YAML (a `custom:powerengine-flow-card`), at `indent`, from the configured plant(s).

    `plants` is `cfg.solar_plants` (only the enabled ones are used); `capacity_wh` and `reserve_soc` are the battery's
    usable capacity (Wh) and reserve SoC (%), for the card's "x h left" and "full in x h"; `has_ev` is whether an EV
    charger role is mapped. With two or more plants the card also breaks the solar figure down by plant. Output is
    byte-identical to the shipped default for today's config (one plant, 18000 Wh, 12%, an EV).
    """
    enabled = [p for p in plants if p.enabled]
    i2, i4 = indent + "  ", indent + "    "
    lines = [f"{indent}- type: custom:powerengine-flow-card", f"{i2}grid_options: {{columns: full}}"]
    if not enabled:
        lines.append(f"{i2}solar: false")
    lines += [f"{i2}capacity_wh: {_num(capacity_wh)}", f"{i2}reserve_soc: {_num(reserve_soc)}"]
    if has_ev:
        lines.append(f"{i2}ev: true")
    if len(enabled) >= 2:
        lines.append(f"{i2}plants:")
        for plant in enabled:
            lines.append(f"{i4}- {{name: {json.dumps(plant.name)}, entity: sensor.pe_state_solar_{plant.id}_power}}")
    return "\n".join(lines) + "\n"


def _splice(text: str, card: str) -> str:
    """Replace the lines between the BEGIN/END markers (kept as-is) with `card`'s lines."""
    lines = text.split("\n")
    begin_i = next(i for i, ln in enumerate(lines) if ln.strip() == BEGIN_MARKER)
    end_i = next(i for i, ln in enumerate(lines) if ln.strip() == END_MARKER)
    card_lines = card.rstrip("\n").split("\n")
    return "\n".join(lines[:begin_i + 1] + card_lines + lines[end_i:])


def _without_view(text: str, engine: str) -> str:
    """The dashboard without the page of engine `engine` ("v1" or "v2"): the lines between its two marker comments."""
    begin, end = f"# BEGIN view-engine-{engine}", f"# END view-engine-{engine}"
    lines = text.split("\n")
    b = next(i for i, ln in enumerate(lines) if ln.strip().startswith(begin))
    e = next(i for i, ln in enumerate(lines) if ln.strip() == end)
    return "\n".join(lines[:b] + lines[e + 1:])


def render(text: str, card: str | None = None, names: dict[str, str] | None = None,
           engine: str | None = None) -> str:
    """The shipped dashboard as written to HA: the Energy flow card spliced in, then every `<<term>>` placeholder
    replaced by the user's supplier or device name (an unknown placeholder raises, so it can't ship). With `engine`
    ("v1" or "v2": the one in use) the other engine's page is left out, so there is one Engine tab."""
    if card is not None:
        text = _splice(text, card)
    if engine in ("v1", "v2"):
        text = _without_view(text, "v2" if engine == "v1" else "v1")
    text = "\n".join(ln for ln in text.split("\n") if not ln.strip().startswith(VIEW_MARKS))
    return fill(text, names)


def sync_dashboard(target: str, source: str = SOURCE, card: str | None = None,
                   names: dict[str, str] | None = None, engine: str | None = None) -> bool:
    """Copy the shipped dashboard to `target` if it differs. With `card` (the Energy flow card's YAML, from
    `energy_flow_card`), the text between the markers is replaced with it first; `<<term>>` placeholders are filled
    from `names` (the current names map by default); with `engine`, only that engine's page is kept. Returns True if
    written."""
    with open(source, encoding="utf-8") as fh:
        wanted = render(fh.read(), card, names, engine)
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
