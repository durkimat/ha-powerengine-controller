"""Operation mode: Passive (monitor and simulate) or Active (in control).

The effective mode can be stricter than the configured one: Active is refused
unless every guardrail passes. It is never looser.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Config

UNCONFIGURED, PASSIVE, ACTIVE, PAUSED = "unconfigured", "passive", "active", "paused"

# Handover guards: role -> the state it must be in before PowerEngine may control the inverter.
GUARDS = (("guard_read_only", "on"), ("guard_off_1", "off"), ("guard_off_2", "off"))

# Active is available from 0.5.14. Kept as a switch so a build can be made Passive-only again if needed.
BUILD_SUPPORTS_ACTIVE = True


@dataclass(frozen=True)
class ModeDecision:
    configured: str
    effective: str
    reason: str


def guard_problems(cfg: Config | None, get_state) -> list[str]:
    """Why another controller might still be in charge; empty when every mapped guard is safe.

    At least one guard must be mapped: without any, PowerEngine can't tell Predbat or the legacy
    automations aren't also writing to the inverter."""
    if cfg is None:
        return ["no config"]
    mapped = [(key, want, cfg.inputs[key]["entity"]) for key, want in GUARDS
              if "entity" in (cfg.inputs.get(key) or {})]
    if not mapped:
        return ["no handover guards are mapped"]
    out = []
    for _key, want, eid in mapped:
        state = get_state(eid)
        if str(state) != want:
            out.append(f"{eid} is {state} (must be {want})")
    return out


def effective_mode(cfg: Config | None, config_error: str | None = None,
                   build_supports_active: bool = BUILD_SUPPORTS_ACTIVE,
                   missing_required: list[str] | tuple[str, ...] = (),
                   guards: list[str] | tuple[str, ...] = (), paused: bool = False) -> ModeDecision:
    if config_error:
        return ModeDecision(UNCONFIGURED, UNCONFIGURED, f"Config problem: {config_error}")
    if cfg is None:
        return ModeDecision(UNCONFIGURED, UNCONFIGURED, "No config.yaml yet; set PowerEngine up on the config page.")
    if missing_required:
        shown = ", ".join(list(missing_required)[:4]) + ("…" if len(missing_required) > 4 else "")
        return ModeDecision(cfg.mode, UNCONFIGURED, f"{len(missing_required)} required input(s) not ready: {shown}")
    if cfg.mode == PASSIVE:
        return ModeDecision(PASSIVE, PASSIVE, "Passive: monitoring and simulating only; nothing is controlled.")
    if not build_supports_active:
        return ModeDecision(ACTIVE, PASSIVE, "Active was requested, but this build only supports Passive.")
    if guards:
        return ModeDecision(ACTIVE, PASSIVE, "Active refused: another controller may be in charge ("
                            + "; ".join(guards) + ").")
    if paused:
        return ModeDecision(ACTIVE, PAUSED, "Paused from the dashboard: the inverter is back on Self-Use and "
                            "PowerEngine makes no changes until you resume.")
    return ModeDecision(ACTIVE, ACTIVE, "Active: PowerEngine is in control.")
