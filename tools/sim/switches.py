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
    "range_firmness": "off",
    "min_run_pct": 0,
    "slot_floor_soc": 0,
    "charge_max_import_p": 0,
    "switch_cost_p": 0,
    "wear_p": 0,
}

PRESETS = [
    {
        "id": "engine_2_1_beta",
        "label": "Engine 2.1 beta",
        "help": "The 2.1 beta profile: a firm range of 70 to 90%, charges and sales that run its span, and 80% kept while a smart slot is counted on. Your saved settings win over its values.",
        "settings": {"system.engine": "v2_1"},
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
