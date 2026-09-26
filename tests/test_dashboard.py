import yaml

from pe_core.dashboard import SOURCE, sync_dashboard
from pe_core.entities import ENTITIES


def test_dashboard_is_valid_and_uses_only_pe_entities():
    text = open(SOURCE).read()
    yaml.safe_load(text)
    known = {e.entity_id for e in ENTITIES}
    import re
    used = set(re.findall(r"\b(?:sensor|binary_sensor|switch)\.pe_[a-z0-9_]+", text))
    assert used and used <= known, used - known


def test_sync_writes_once(tmp_path):
    target = tmp_path / "powerengine" / "dashboard.yaml"
    assert sync_dashboard(str(target)) is True
    assert sync_dashboard(str(target)) is False
    target.write_text("edited")
    assert sync_dashboard(str(target)) is True


def test_no_stray_yaml_in_app_folder():
    """AppDaemon loads every .yaml under the app folder as app config; only the app definition may be YAML."""
    import pathlib
    app = pathlib.Path(__file__).resolve().parents[1] / "apps" / "powerengine"
    yamls = sorted(p.relative_to(app).as_posix() for p in app.rglob("*.y*ml"))
    assert yamls == ["powerengine.yaml"], yamls


def test_dashboard_templates_parse():
    """Every markdown card's Jinja template must at least parse (HA would show an error otherwise)."""
    import jinja2
    d = yaml.safe_load(open(SOURCE))
    env = jinja2.Environment()
    count = 0
    for view in d["views"]:
        for section in view.get("sections", []):
            for card in section.get("cards", []):
                if card.get("type") == "markdown":
                    env.parse(card["content"])
                    count += 1
    assert count >= 5


def test_charts_have_a_phone_version():
    """Every chart is shown twice: desktop (>= 600 px) and a phone version with one visible axis."""
    d = yaml.safe_load(open(SOURCE))
    pairs = []

    def walk(x):
        if isinstance(x, dict):
            if x.get("type") == "custom:apexcharts-card":
                raise AssertionError("chart outside a screen-size conditional")
            if x.get("type") == "conditional" and x["card"].get("type") == "custom:apexcharts-card":
                pairs.append((x["conditions"][0]["media_query"], x["card"]))
                return
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(d)
    desk = [c for q, c in pairs if "min-width" in q]
    phone = [c for q, c in pairs if "max-width" in q]
    assert len(desk) == len(phone) == 5
    for a, b in zip(desk, phone, strict=True):
        assert a["series"] == b["series"]
        assert sum(y.get("show", True) for y in b["yaxis"]) == 1
        assert all("title" not in (y.get("apex_config") or {}) for y in b["yaxis"])
        assert b["apex_config"]["legend"]["position"] == "top"
    assert (desk[0]["graph_span"], phone[0]["graph_span"]) == ("36h", "18h")
