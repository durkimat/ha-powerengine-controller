"""Engine v2 state saved by an earlier release still loads (0.9.157: the live comparison lost two fields)."""

from pe_core.engine_v2.execute import Executor
from pe_core.engine_v2.settings import V2Settings


def test_saved_lines_with_fields_an_earlier_release_had_still_load():
    lines = {"value_p": 12.0, "buy_line_p": 7.0, "sell_line_p": 14.25, "use_line_p": 27.4, "store_sun_line_p": 15.8,
             "import_p": 6.66, "export_p": 15.0, "charge_target_soc": 90.0, "sell_floor_soc": None,
             "charge_now": True, "run_target_soc": 88.0}
    ex = Executor(V2Settings(), {"mode": "charge", "since": 1.0, "lines": lines})
    assert ex.mode == "charge" and ex.lines.value_p == 12.0 and ex.lines.charge_target_soc == 90.0
