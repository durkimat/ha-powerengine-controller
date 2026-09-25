"""Operation mode: Passive (monitor and simulate) or Active (in control).

The effective mode can be stricter than the configured one: Active is refused
unless every guardrail passes. It is never looser.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Config

UNCONFIGURED, PASSIVE, ACTIVE = "unconfigured", "passive", "active"

# Control is not implemented yet: every build until control ships is Passive-only.
BUILD_SUPPORTS_ACTIVE = False


@dataclass(frozen=True)
class ModeDecision:
    configured: str
    effective: str
    reason: str


def effective_mode(cfg: Config | None, config_error: str | None = None,
                   build_supports_active: bool = BUILD_SUPPORTS_ACTIVE) -> ModeDecision:
    if config_error:
        return ModeDecision(UNCONFIGURED, UNCONFIGURED, f"Config problem: {config_error}")
    if cfg is None:
        return ModeDecision(UNCONFIGURED, UNCONFIGURED, "No config.yaml yet; set PowerEngine up on the config page.")
    if cfg.mode == PASSIVE:
        return ModeDecision(PASSIVE, PASSIVE, "Passive: monitoring and simulating only; nothing is controlled.")
    if not build_supports_active:
        return ModeDecision(ACTIVE, PASSIVE, "Active was requested, but this build only supports Passive.")
    return ModeDecision(ACTIVE, ACTIVE, "Active: PowerEngine is in control.")
