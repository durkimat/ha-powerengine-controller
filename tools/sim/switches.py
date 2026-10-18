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

# id, the const that stubs it, the stub that switches it off, label, what it does. The executor makes the plan's own
# decision now (one function, `value._choose`), so it has no rules of its own left to switch off: add an entry here for
# a method an experiment adds.
LAYERS: list[dict] = []

# setting -> the value at which it has no effect (ablation 1 and 2). Not offered for a setting not listed.
OFF_VALUES = {
    "comfort_cost_p": 0,
    "top_up_cost_p": 0,
    "prefer_self_use": False,
    "switch_cost_p": 0,
    "wear_house_p": 0,
    "wear_sale_p": 0,
}

PRESETS = [
    {
        "id": "engine_2_1_beta",
        "label": "Engine 2.1 beta (as released, top-up cost out)",
        "help": "The released profile: follows the plan, level curves, sharper bottom in smart slots. Your saved settings win over its values, so the top-up cost is set to 0 here, as in the tests.",
        "settings": {"system.engine": "v2_1", "top_up_cost_p": 0},
        "consts": {},
    },
    {
        "id": "current",
        "label": "Current",
        "help": "The archived config as it is: no overrides.",
        "settings": {},
        "consts": {},
    },
]


def for_code(layers_present: set[str]) -> dict:
    """The registry as the GUI is given it, for a code version that has the methods in `layers_present`."""
    return {
        "layers": [layer for layer in LAYERS if layer["const"].rsplit(".", 1)[-1] in layers_present],
        "off_values": OFF_VALUES,
        "presets": PRESETS,
    }
