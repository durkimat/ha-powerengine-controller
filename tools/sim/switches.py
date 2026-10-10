# ruff: noqa: E501  (prose help strings)
"""What the GUI can switch off, and the named sets of overrides it starts from.

These are simulator-side: nothing here is read by the app. A *layer* is a rule that is code, not a setting; the simulator
switches it off by replacing the method with a neutral stub (see worker.STUBS). An *off value* is the value of a setting at
which it has the same effect as the rule being absent (found by ablation: `docs/plans/engine-v2-ablation-1.md`); a setting
without one has no honest "off".

A new control feature should ship with an engine setting (it then appears in the GUI by itself) or, for an experiment, with an
entry here. Entries naming a method the code under test does not have are not offered.
"""

from __future__ import annotations

EXECUTOR = "engine_v2.execute.Executor"

# id, the const that stubs it, the stub that switches it off, label, what it does
LAYERS = [
    {
        "id": "leg_going",
        "const": f"{EXECUTOR}._leg_going",
        "stub": "@false",
        "label": "Keep a running charge or sale going",
        "help": "A charge or sale already running goes on to the end of its plan step. Off: a revaluation can turn it round part-way.",
    },
    {
        "id": "programme_choice",
        "const": f"{EXECUTOR}._with_the_programmes_choice",
        "stub": "@pass",
        "label": "Ask the plan before a charge or sale starts",
        "help": "A charge or sale starts only when the plan itself (value.choice_now) would start it. Off: the price lines alone decide.",
    },
    {
        "id": "worth_the_change",
        "const": f"{EXECUTOR}._worth_the_change",
        "stub": "@true",
        "label": "A change must pay for itself",
        "help": "A change the plan did not ask for must earn more than a second price on the change. Off: any change that looks better goes ahead.",
    },
]

# setting -> the value at which it has no effect (ablation 1 and 2). Not offered for a setting not listed.
OFF_VALUES = {
    "price_band_p": 0,
    "level_band_pct": 0,
    "min_dwell_s": 0,
    "comfort_cost_p": 0,
    "top_up_cost_p": 0,
    "prefer_self_use": False,
    "late_events": False,
    "switch_cost_p": 0,
    "wear_house_p": 0,
    "wear_sale_p": 0,
}

_ALL_LAYERS_OFF = {layer["const"]: layer["stub"] for layer in LAYERS}
_BARE = {k: v for k, v in OFF_VALUES.items() if k not in ("switch_cost_p", "wear_house_p", "wear_sale_p")}

PRESETS = [
    {
        "id": "current",
        "label": "Current",
        "help": "The archived config as it is: no overrides.",
        "settings": {},
        "consts": {},
    },
    {
        "id": "mvp",
        "label": "MVP: the price model and nothing else",
        "help": "Every executor layer, band, cost and extra switched off; the plan's switch cost, the hard limits and the reading "
        "filter stay. It runs, and it chatters (ablation 2: 1,743 mode changes in 3 days). Redefine when the policy executor exists.",
        "settings": dict(_BARE),
        "consts": dict(_ALL_LAYERS_OFF),
    },
    {
        "id": "mvp_programme_choice",
        "label": "MVP + ask the plan before a charge or sale",
        "help": "The MVP with the one layer that did most to stop the chatter put back (ablation 2: 127 mode changes).",
        "settings": dict(_BARE),
        "consts": {k: v for k, v in _ALL_LAYERS_OFF.items() if not k.endswith("_with_the_programmes_choice")},
    },
]


def for_code(layers_present: set[str]) -> dict:
    """The registry as the GUI is given it, for a code version that has the methods in `layers_present`."""
    return {
        "layers": [layer for layer in LAYERS if layer["const"].rsplit(".", 1)[-1] in layers_present],
        "off_values": OFF_VALUES,
        "presets": PRESETS,
    }
