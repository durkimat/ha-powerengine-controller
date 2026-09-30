"""tools/diag_summary.py on a small synthetic diagnostics export."""

import importlib.util
import json
import pathlib

spec = importlib.util.spec_from_file_location(
    "diag_summary", pathlib.Path(__file__).parent.parent / "tools" / "diag_summary.py"
)
ds = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ds)


def line(t, level, msg):
    return {"t": f"2026-09-29T{t}+00:00", "level": level, "msg": msg}


def export():
    log = [
        line("06:00:00", "INFO", "PowerEngine 0.9.70 starting"),
        line("06:10:00", "INFO", "Decision: Grid-charge to 80%: cheap rate"),
        line("06:10:30", "INFO", "Decision: Hold the battery: target reached"),
        line("06:11:00", "INFO", "Decision: Grid-charge to 80%: cheap rate"),
        line("06:11:40", "INFO", "Decision: Hold the battery: target reached"),
        line("09:00:00", "INFO", "Decision: Self-use (battery limited to 2.0 kW): solar"),
        line("09:05:00", "INFO", "RAM remote control: Force charge at 3000 W"),
        line("09:05:30", "INFO", "RAM remote control: inverter not following Force charge at 3000 W"),
        line("09:10:00", "INFO", "RAM remote control: Off (Self-Use)"),
        line("09:15:00", "WARNING", "Could not request smart-charge slots: Timeout 12 s"),
        line("09:45:00", "WARNING", "Could not request smart-charge slots: Timeout 30 s"),
        line("10:00:00", "ERROR", "Reading, planning or deciding failed: KeyError('x')"),
        line("10:30:00", "INFO", "Axle event 18:00-19:00 seen"),
        line("11:00:00", "INFO", "PowerEngine 0.9.70 starting"),
    ]
    return {
        "generated": "2026-09-29T11:30:00.000Z",
        "card_version": "0.9.68",
        "browser": "test",
        "app": {
            "app": {
                "version": "0.9.70",
                "min_card_version": "0.9.70",
                "generated": "2026-09-29T11:30:00+00:00",
                "timezone": "Europe/London",
            },
            "mode": {"configured": "active", "effective": "active", "reason": "", "halted": False},
            "config": {"site": {"inverter": "solis", "tariff": "auto"}},
            "writes": {
                "budget": 100000,
                "since": "2026-09-01",
                "ram": 5,
                "staged": 2,
                "observed": {"today": 3, "per_day": 4.5, "days": 10, "total": 50, "years": 60.9},
            },
            "journal": [],
            "plan": {
                "made_at": "2026-09-29T11:25:00+00:00",
                "slots": [
                    {
                        "start": f"2026-09-29T{10 + i // 2:02d}:{30 * (i % 2):02d}:00+00:00",
                        "soc": 50 + i,
                        "action": "self-use",
                        "price_p": 7.0 + i,
                    }
                    for i in range(20)
                ],
            },
            "smart_requests": [
                {
                    "time": "2026-09-29T02:00:00+00:00",
                    "by": "auto",
                    "from": "ready 07:00",
                    "to": "ready 08:00",
                    "why": "car needs more",
                    "result": "accepted",
                },
                {
                    "time": "2026-09-29T09:20:00+00:00",
                    "by": "auto",
                    "from": None,
                    "to": "ready 09:00",
                    "why": "",
                    "result": None,
                },
            ],
            "smart_slots": {
                "slots": 4,
                "used": 3,
                "cancelled": 1,
                "cut_short": 0,
                "upcoming": 1,
                "car_kwh": 12.5,
                "recent": [
                    {"day": "Mon 28 Sep", "time": "02:00-04:30", "status": "done", "planned_kwh": 10, "car_kwh": 9.1}
                ],
            },
            "attribute_sizes": [
                {"sensor": "map_catalogue", "last_bytes": 14500, "peak_bytes": 14600, "limit_bytes": 16384},
                {"sensor": "state_plan", "last_bytes": 3000, "peak_bytes": 3100, "limit_bytes": 16384},
            ],
            "log": log,
        },
    }


def test_summary_finds_what_a_routine_check_wants():
    s = ds.summarise(export())
    assert s["app_version"] == "0.9.70" and s["card_version"] == "0.9.68" and s["card_older_than_minimum"] is True
    assert s["log"]["levels"] == {"INFO": 11, "WARNING": 2, "ERROR": 1}
    # the two timeouts differ only in a number: one line, counted twice, first and last time kept
    w = [x for x in s["warnings"] if x["level"] == "WARNING"]
    assert len(w) == 1 and w[0]["count"] == 2 and w[0]["first"].startswith("2026-09-29T09:15")
    assert s["decisions"]["total"] == 5
    assert s["decisions"]["action_flips"] == 4
    assert s["decisions"]["flip_flops"] == [
        {"pair": "grid-charge <-> hold the battery", "count": 2, "first": "2026-09-29T06:10:00+00:00"}
    ]
    assert s["ram"]["total"] == 2  # "inverter not following" is not a command
    assert [r["time"][11:16] for r in s["smart_requests"]] == ["02:00", "09:20"]
    assert s["smart_requests"][1]["result"] == "pending"
    assert len(s["axle"]) == 1 and len(s["restarts"]) == 2
    assert [x["sensor"] for x in s["big_attributes"]] == ["map_catalogue"]  # 3.1 KB is not flagged
    assert len(s["plan"]["next"]) == 12 and s["plan"]["next"][0]["time"].startswith(
        "2026-09-29T11:30"
    )  # the slot already over is left out
    assert s["writes"]["observed_per_day"] == 4.5
    assert json.loads(json.dumps(s))  # serialisable


def test_since_limits_the_window():
    s = ds.summarise(export(), ds.parse_time("2026-09-29T09:30:00"))
    assert s["log"]["lines"] == 4 and s["decisions"] is not None and s["decisions"]["total"] == 0
    assert [r["time"][11:16] for r in s["smart_requests"]] == []
    assert s["restarts"][0]["time"].startswith("2026-09-29T11:00") and len(s["restarts"]) == 1


def test_older_export_with_sections_missing():
    for old in (
        {},
        {"generated": "2026-09-01T00:00:00Z"},
        {"app": None},
        {"app": {"app": {"version": "0.9.1"}, "log": "x"}},
        {"app": {"plan": {"slots": "no"}, "writes": [], "attribute_sizes": {"a": 1}, "smart_requests": [1, None]}},
    ):
        s = ds.summarise(old)
        text = ds.render(s)
        assert "PowerEngine diagnostics" in text and "Traceback" not in text
    assert ds.summarise({"app": {"app": {"version": "0.9.1"}}})["card_older_than_minimum"] is None


def test_command_line_text_and_json(tmp_path, capsys):
    f = tmp_path / "export.json"
    f.write_text(json.dumps(export()))
    assert ds.main([str(f)]) == 0
    text = capsys.readouterr().out
    assert "OLDER than the app's minimum 0.9.70" in text and "FLIP-FLOP" in text and "x2" in text
    assert len(text.splitlines()) < 60
    assert ds.main([str(f), "--json", "--since", "2026-09-29T09:30:00Z"]) == 0
    assert json.loads(capsys.readouterr().out)["since"].startswith("2026-09-29T09:30")
    assert ds.main([str(tmp_path / "missing.json")]) == 2
