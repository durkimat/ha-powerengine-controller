"""The setup wizard's candidate-entities export: the shape check, the scrub check and the summary tool."""

import importlib.util
import json
import pathlib

from pe_core import candidates

spec = importlib.util.spec_from_file_location(
    "candidates_summary", pathlib.Path(__file__).parent.parent / "tools" / "candidates_summary.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def export(**over):
    data = {
        "format": "powerengine-candidates", "version": 1, "generated": "2026-10-03T10:00:00Z", "card_version": "0.9.88",
        "app_version": "0.9.88", "ha_version": "2026.10.0", "chosen": {"inverter": "unlisted", "ev_charger": "none"},
        "note": "Brand X hybrid",
        "devices": [{"key": "d1", "integration": "solax_modbus", "manufacturer": "BrandX", "model": "HX-5",
                     "sw_version": "1.2.3"}],
        "entities": [
            {"id": "sensor.brandx_battery_soc", "domain": "sensor", "platform": "solax_modbus", "device": "d1",
             "unit": "%", "device_class": "battery", "state": "54", "attributes": ["unit_of_measurement"]},
            {"id": "select.brandx_remote_mode", "domain": "select", "platform": "solax_modbus", "device": "d1",
             "state": "Off", "options": ["Off", "Charge", "Discharge"]},
            {"id": "number.brandx_charge_power", "domain": "number", "platform": "solax_modbus", "device": "d1",
             "unit": "W", "state": "0", "min": 0, "max": 5000, "step": 100},
            {"id": "sensor.house_meter_power", "domain": "sensor", "platform": "other", "device": None, "unit": "W",
             "state": "812"},
        ],
    }
    data.update(over)
    return data


def test_a_good_export_has_no_problems_and_nothing_unscrubbed():
    assert candidates.problems(export()) == [] and candidates.unscrubbed(export()) == []


def test_shape_problems_are_named():
    assert candidates.problems([]) == ["not a powerengine-candidates file"]
    assert "version 2, expected 1" in candidates.problems(export(version=2))
    bad = export()
    bad["entities"].append({"id": "Not An Id", "domain": "sensor"})
    bad["entities"].append({"id": "sensor.x", "domain": "sensor", "device": "d9"})
    text = " ".join(candidates.problems(bad))
    assert "bad id" in text and "device 'd9' is not in 'devices'" in text
    assert "unknown part 'boiler'" in " ".join(candidates.problems(export(chosen={"boiler": "x"})))


def test_numbers_emails_and_postcodes_are_flagged_as_unscrubbed():
    data = export(note="mail me at sam@example.com from SW1A 1AA")
    data["devices"][0]["sw_version"] = "420044"                 # firmware keeps its digits
    data["devices"][0]["model"] = "HX-5 sam@example.com"
    data["entities"][0]["id"] = "sensor.edf_energy_electricity_1234567890_rate"
    data["entities"][1]["state"] = "x" * 80
    text = " ".join(candidates.unscrubbed(data))
    assert "a long number" in text and "an email address" in text and "a postcode" in text and "longer than 60" in text
    assert "device d1 sw_version" not in text and "device d1 model has an email address" in text


def test_the_summary_groups_entities_under_their_device():
    text = candidates.summary(export())
    assert "BrandX HX-5  [solax_modbus]  firmware 1.2.3  (3 entities)" in text
    assert "select.brandx_remote_mode = Off  (options: Off, Charge, Discharge)" in text
    assert "number.brandx_charge_power = 0  (W; range 0..5000)" in text
    assert "Other entities, matched by name (1)" in text and "Chosen: inverter=unlisted, ev_charger=none" in text


def test_the_tool_prints_a_summary_and_refuses_unscrubbed_or_broken_files(tmp_path, capsys):
    good = tmp_path / "good.json"
    good.write_text(json.dumps(export()))
    assert tool.main(["x", str(good)]) == 0 and "BrandX HX-5" in capsys.readouterr().out
    leaky = tmp_path / "leaky.json"
    leaky.write_text(json.dumps(export(note="sam@example.com")))
    assert tool.main(["x", str(leaky)]) == 1 and "NOT SCRUBBED" in capsys.readouterr().out
    broken = tmp_path / "broken.json"
    broken.write_text("{nope")
    assert tool.main(["x", str(broken)]) == 2 and "Could not read" in capsys.readouterr().out
    wrong = tmp_path / "wrong.json"
    wrong.write_text("[]")
    assert tool.main(["x", str(wrong)]) == 1 and "Problems with the file" in capsys.readouterr().out
