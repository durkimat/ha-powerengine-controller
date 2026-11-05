"""tools/shadow_compare.py: the shadow run against the live PowerEngine, from Home Assistant's history."""

import pathlib
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))

import shadow_compare as sc  # noqa: E402

T0 = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)


def row(minute, state, **attributes):
    return {"state": state, "attributes": attributes, "last_changed": (T0 + timedelta(minutes=minute)).isoformat()}


def test_the_shadows_ids_are_the_engines_own_with_the_shadow_prefix():
    assert sc.shadow_id("sensor.pe_v2_mode") == "sensor.pe_shadow_v2_mode"
    assert sc.shadow_id("sensor.solis_x") == "sensor.solis_x"


def test_two_identical_histories_agree_for_every_minute():
    live = [row(0, "charge"), row(30, "export"), row(90, "self_use")]
    decision = sc.Series("decision", "sensor.pe_state_decision")
    result = sc.compare(live, list(live), decision, T0, T0 + timedelta(hours=3))
    assert result["agree"] == result["minutes"] == 180 and result["disagreements"] == [] and result["short"] == 0


def test_a_short_difference_from_the_engines_ticking_at_other_moments_is_not_reported_a_long_one_is():
    live = [row(0, "charge"), row(30, "export"), row(90, "self_use")]
    shadow = [row(0, "charge"), row(31, "export"), row(90, "self_use"), row(120, "hold")]  # 1 min late, then real
    result = sc.compare(live, shadow, sc.Series("decision", "x"), T0, T0 + timedelta(hours=3))
    assert result["short"] == 1 and len(result["disagreements"]) == 1
    d = result["disagreements"][0]
    assert (d["from"], d["minutes"], d["live"], d["shadow"]) == (T0 + timedelta(minutes=120), 60, "self_use", "hold")
    assert result["agree"] == 180 - 1 - 60 and result["longest"] == 60


def test_numbers_within_the_tolerance_text_with_would_send_removed_and_missing_values_are_the_same():
    soc = sc.Series("soc", "x", "target_soc", tolerance=1.0)
    assert sc.same(41.7, 42.3, soc) and not sc.same(41.7, 43.0, soc) and sc.same(None, "unknown", soc)
    command = sc.Series("command", "x", clean=r"\s*\(would send\)")
    assert sc.same("Force discharge at 5000 W", "Force discharge at 5000 W (would send)", command)
    assert not sc.same("Force discharge at 5000 W", "Off (Self-Use) (would send)", command)


def test_an_attribute_series_reads_the_attribute_not_the_state():
    live = [row(0, "export", target_soc=42.0), row(60, "export", target_soc=30.0)]
    shadow = [row(0, "export", target_soc=42.4), row(60, "export", target_soc=42.0)]
    target = sc.Series("target", "x", "target_soc", tolerance=1.0)
    result = sc.compare(live, shadow, target, T0, T0 + timedelta(hours=2))
    assert [(d["minutes"], d["live"], d["shadow"]) for d in result["disagreements"]] == [(60, 30.0, 42.0)]


def test_the_report_skips_the_shadows_first_minutes_and_lists_where_they_differed():
    history = {
        "sensor.pe_shadow_diag_started": [row(0, (T0 + timedelta(minutes=0)).isoformat())],
        "sensor.pe_state_decision": [row(-60, "charge"), row(60, "export")],
        "sensor.pe_shadow_state_decision": [row(0, "unknown"), row(1, "charge"), row(70, "export")],      # 10 min late
        "sensor.pe_state_status": [row(0, "Active")], "sensor.pe_shadow_state_status": [row(0, "Passive")],
    }
    text, differing = sc.run(history, T0 - timedelta(hours=1), T0 + timedelta(hours=3))
    assert differing == 1
    assert "decision: " in text and "10 min: live 'export', shadow 'charge'" in text
    assert "not compared" in text and "sensor.pe_state_status: 'Active' | 'Passive'" in text


def test_fetch_asks_the_history_api_with_the_token_and_groups_rows_by_entity(monkeypatch):
    import io
    import json
    seen = {}

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(request, timeout):
        seen["url"], seen["auth"] = request.full_url, request.get_header("Authorization")
        return Response(json.dumps([[{"entity_id": "sensor.a", "state": "1"}], [], [{"entity_id": "sensor.b",
                                                                                     "state": "2"}]]).encode())
    monkeypatch.setattr(sc.urllib.request, "urlopen", urlopen)
    out = sc.fetch("http://ha/api/", "TOKEN", ["sensor.a", "sensor.b"], T0, T0 + timedelta(hours=1))
    assert set(out) == {"sensor.a", "sensor.b"} and seen["auth"] == "Bearer TOKEN"
    assert "/history/period/" in seen["url"] and "filter_entity_id=sensor.a%2Csensor.b" in seen["url"]
    assert "significant_changes_only=0" in seen["url"]


def test_it_needs_a_token(monkeypatch, capsys):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    assert sc.main([]) == 2 and "SUPERVISOR_TOKEN is not set" in capsys.readouterr().err
