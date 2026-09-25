"""Plain-English status and the values behind the dashboard's state entities.

0.1 describes what is happening; later releases add what PowerEngine decided and why.
"""

from __future__ import annotations

from datetime import datetime, tzinfo
from typing import Any

from .decide import Decision
from .modes import ModeDecision
from .readings import Readings

IDLE_W = 50          # below this a flow counts as idle
MAX_STATE_LEN = 255  # HA's limit for a state string


def kw(watts: float) -> str:
    return f"{abs(watts) / 1000:.1f} kW"


def pence(gbp: float) -> str:
    p = gbp * 100
    return f"{p:.2f}".rstrip("0").rstrip(".") + "p"


def hhmm(t: datetime, tz: tzinfo | None) -> str:
    return (t.astimezone(tz) if tz else t).strftime("%H:%M")


def _battery(r: Readings) -> str | None:
    if r.battery_soc is None:
        return None
    s = f"Battery {r.battery_soc:.0f}%"
    if r.battery_power is None or abs(r.battery_power) < IDLE_W:
        return s + ", idle"
    return s + (f", discharging {kw(r.battery_power)}" if r.battery_power > 0 else f", charging {kw(r.battery_power)}")


def _flows(r: Readings) -> str | None:
    parts = []
    if r.solar_power is not None:
        parts.append(f"solar {kw(r.solar_power)}")
    if r.house_power is not None:
        parts.append(f"house {kw(r.house_power)}")
    if r.ev_state() == "charging" and r.ev_power:
        parts.append(f"car charging {kw(r.ev_power)}")
    if r.grid_power is not None:
        if abs(r.grid_power) < IDLE_W:
            parts.append("grid idle")
        else:
            parts.append(f"importing {kw(r.grid_power)}" if r.grid_power > 0 else f"exporting {kw(r.grid_power)}")
    if not parts:
        return None
    text = ", ".join(parts)
    return text[0].upper() + text[1:]


def _price(r: Readings, tz: tzinfo | None) -> str | None:
    if r.import_rate is None:
        return None
    s = f"Import {pence(r.import_rate)}"
    slot = r.current_dispatch()
    if slot:
        s += f" (smart slot until {hhmm(slot.end, tz)})"
    nxt = r.next_rate_change()
    if nxt and nxt.value is not None:
        s += f", {pence(nxt.value)} from {hhmm(nxt.start, tz)}"
    if r.export_rate is not None:
        s += f"; export {pence(r.export_rate)}"
    return s


def _events(r: Readings, tz: tzinfo | None) -> list[str]:
    out = []
    if r.axle_state() == "active":
        out.append("Axle event in progress" + (f" until {hhmm(r.axle_end, tz)}" if r.axle_end else ""))
    elif r.axle_state() == "scheduled":
        end = f"–{hhmm(r.axle_end, tz)}" if r.axle_end else ""
        out.append(f"Axle event at {hhmm(r.axle_start, tz)}{end}")
    if r.free_state() == "active":
        out.append("Free power now" + (f" until {hhmm(r.free_end, tz)}" if r.free_end else ""))
    elif r.free_state() == "scheduled":
        out.append(f"Free power from {hhmm(r.free_start, tz)}")
    nxt = r.next_dispatch()
    if not r.current_dispatch() and nxt:
        out.append(f"Next smart slot {hhmm(nxt.start, tz)}")
    return out


def summary(r: Readings | None, mode: ModeDecision, tz: tzinfo | None = None,
            decision: Decision | None = None) -> str:
    """One paragraph: mode, the decision and why, then what is happening now."""
    if mode.effective == "unconfigured" or r is None:
        return f"UNCONFIGURED. {mode.reason}"
    prefix = "PASSIVE." if mode.effective == "passive" else "ACTIVE."
    lead = [decision.sentence(passive=mode.effective == "passive").rstrip(".")] if decision else []
    parts = lead + [p for p in (_battery(r), _flows(r), _price(r, tz)) if p] + _events(r, tz)
    if r.problems:
        parts.append("No reading for: " + ", ".join(p.replace("_", " ") for p in r.problems))
    return prefix + " " + ". ".join(parts) + "."


def short(text: str) -> str:
    return text if len(text) <= MAX_STATE_LEN else text[: MAX_STATE_LEN - 1] + "…"


def entity_states(r: Readings | None, mode: ModeDecision, tz: tzinfo | None = None,
                  decision: Decision | None = None, since: str | None = None) -> dict[str, tuple[Any, dict]]:
    """key -> (state, attributes) for every state_* entity (except the activity log)."""
    text = summary(r, mode, tz, decision)
    out: dict[str, tuple[Any, dict]] = {"state_summary": (short(text), {"text": text})}
    if decision is not None:
        out["state_decision"] = (decision.action, {
            "sentence": decision.sentence(passive=mode.effective != "active"),
            "rule": decision.rule, "reason": decision.reason,
            "target_soc": decision.target_soc, "power_w": decision.power_w,
            "since": since, "passive": mode.effective != "active",
        })
    if r is None:
        return out

    def num(v, digits=0):
        return "unknown" if v is None else round(v, digits)

    out["state_battery_soc"] = (num(r.battery_soc), {})
    out["state_battery_power"] = (num(r.battery_power), {"convention": "+ discharging, - charging"})
    out["state_grid_power"] = (num(r.grid_power), {"convention": "+ importing, - exporting"})
    out["state_solar_power"] = (num(r.solar_power), {"plants": {k: num(v) for k, v in r.solar_by_plant.items()},
                                                     "forecast_today_kwh": r.forecast_today_kwh,
                                                     "forecast_tomorrow_kwh": r.forecast_tomorrow_kwh})
    out["state_house_power"] = (num(r.house_power), {"including_car": num(r.house_power_raw)})
    out["state_ev_power"] = (num(r.ev_power), {})

    nxt = r.next_rate_change()
    slot = r.current_dispatch()
    out["state_import_rate"] = (num(r.import_rate, 5), {
        "pence": None if r.import_rate is None else round(r.import_rate * 100, 2),
        "until": hhmm(nxt.start, tz) if nxt else None,
        "next_rate_pence": None if not nxt or nxt.value is None else round(nxt.value * 100, 2),
        "in_smart_slot": slot is not None,
        "offpeak_now": r.offpeak_now,
    })
    out["state_export_rate"] = (num(r.export_rate, 5), {
        "pence": None if r.export_rate is None else round(r.export_rate * 100, 2)})

    ev = {"unplugged": "Unplugged", "plugged_in": "Plugged in", "charging": "Charging"}[r.ev_state()]
    out["state_ev"] = (ev, {"plug": r.ev_plug, "charger": r.ev_status, "mode": r.ev_mode,
                            "session_kwh": r.ev_session_kwh, "power_w": num(r.ev_power)})

    nxt_slot = r.next_dispatch()
    if slot:
        smart = f"Slot until {hhmm(slot.end, tz)}"
    elif nxt_slot:
        smart = f"Next slot {hhmm(nxt_slot.start, tz)}"
    else:
        smart = "No slots planned"
    out["state_smart_charge"] = (smart, {"slots": [{"start": w.start.isoformat(), "end": w.end.isoformat(),
                                                    "kwh": w.value} for w in r.dispatches]})

    ax = r.axle_state()
    ax_text = {"idle": "No event", "scheduled": f"Event at {hhmm(r.axle_start, tz)}" if r.axle_start else "Scheduled",
               "active": "Event in progress"}[ax]
    out["state_axle"] = (ax_text, {"status": ax, "start": r.axle_start.isoformat() if r.axle_start else None,
                                   "end": r.axle_end.isoformat() if r.axle_end else None})
    fp = r.free_state()
    fp_text = {"none": "None scheduled", "scheduled": f"From {hhmm(r.free_start, tz)}" if r.free_start else "Scheduled",
               "active": "Free power now"}[fp]
    out["state_free_power"] = (fp_text, {"status": fp, "start": r.free_start.isoformat() if r.free_start else None,
                                         "end": r.free_end.isoformat() if r.free_end else None})
    return out
