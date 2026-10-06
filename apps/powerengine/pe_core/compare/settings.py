"""The settings a replay runs with: the owner's own, with the inputs pointed at the demo world's entities.

`replay_config` takes the owner's config.yaml (as a mapping) and the demo's template, and gives the config text the
replayed app copies in place of the template. Every setting, feature and the `engine_v2` block of the owner's stay; what
changes is only what the replay needs: the inputs are the world's entities (fixed values such as the battery's size stay
the owner's), Active on RAM remote control with direct publishing, the engine chosen, the house load as the world
reports it (the car included), and the internet-facing features off (smart-charge requests, the tariff simulator,
weather) along with this comparison itself.
"""

from __future__ import annotations

import copy

import yaml

# features the replay turns off: they talk to the supplier or the internet, or are this comparison
FEATURES_OFF = ("smart_charge_optimisation", "tariff_simulator", "cold_caution", "cold_learning", "engine_compare")
# top-level sections the replay takes from the template, not from the owner's config
FROM_TEMPLATE = ("solar_plants", "notifications")
# sections of the owner's config that name real equipment and are left out
DROPPED = ("site", "devices", "remove_entities")


def replay_config(owner: dict | None, template: dict, engine: str) -> str:
    """The config.yaml text for a replay on `engine` ("v1" or "v2"). With no owner config it is the template's own."""
    tmpl = copy.deepcopy(template)
    cfg = copy.deepcopy(owner) if owner else copy.deepcopy(tmpl)
    inputs = copy.deepcopy(tmpl.get("inputs") or {})
    for role, spec in ((owner or {}).get("inputs") or {}).items():
        if isinstance(spec, dict) and "value" in spec:             # a fixed figure (battery size, power limits...)
            inputs[role] = copy.deepcopy(spec)
    cfg["inputs"] = inputs
    for key in FROM_TEMPLATE:
        if key in tmpl:
            cfg[key] = tmpl[key]
    for key in DROPPED:
        cfg.pop(key, None)
    features = dict(cfg.get("features") or {})
    for key in FEATURES_OFF:
        features[key] = False
    cfg["features"] = features
    cfg["operation"] = {**(cfg.get("operation") or {}), "mode": "active"}
    cfg["system"] = {**(cfg.get("system") or {}), "control_method": "ram_remote", "publisher": "direct",
                     "engine": engine, "house_load_includes_ev": True}      # (the world's house load includes the car)
    if engine == "v1":                                      # the v2 preview under v1 changes nothing v1 does: skip it
        cfg["engine_v2"] = {**(cfg.get("engine_v2") or {}), "preview_when_v1": False}
    return yaml.safe_dump(cfg, sort_keys=False)


def battery_of(config_text: str) -> dict:
    """The world's battery from the config the replay runs with: the owner's size, one-way efficiency, power limits
    (capped by the remote-control ceiling) and the battery's own floor. DemoWorld keywords."""
    from ..config import parse_config
    from ..planner import params_from
    cfg = parse_config(yaml.safe_load(config_text))
    p = params_from(cfg)
    return {"capacity_kwh": p.capacity_kwh, "efficiency": p.efficiency, "charge_limit_w": p.max_charge_kw * 1000,
            "discharge_limit_w": p.max_discharge_kw * 1000, "floor_pct": float(cfg.safety.get("battery_floor_soc", 12))}
