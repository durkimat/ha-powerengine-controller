import yaml

from pe_core.dashboard import SOURCE, sync_dashboard
from pe_core.entities import ENTITIES


def test_dashboard_is_valid_and_uses_only_pe_entities():
    text = open(SOURCE).read()
    yaml.safe_load(text)
    known = {e.entity_id for e in ENTITIES}
    import re
    used = set(re.findall(r"\b(?:sensor|binary_sensor|switch|select)\.pe_[a-z0-9_]+", text))
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
    # AppDaemon takes files ending ".yaml" (app_management: file[-5:] == ".yaml"); the inverter definitions are
    # ".yml" so it leaves them alone, and they may only live in the definitions folder.
    assert [y for y in yamls if y.endswith(".yaml")] == ["powerengine.yaml"], yamls
    assert [y for y in yamls if not y.endswith(".yaml")] == ["pe_core/adapters/devices/solis.yml"], yamls


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


def test_every_page_says_what_it_is():
    """On a phone only the tab icons show, so each page starts with its title, left-aligned, the same way."""
    d = yaml.safe_load(open(SOURCE).read())
    for view in d["views"]:
        assert view.get("type") == "sections" and view.get("max_columns") == 3, view["title"]
        header = view.get("header") or {}
        assert header.get("layout") == "start", view["title"]
        assert header.get("card", {}).get("content", "").startswith("## "), view["title"]


def test_monitoring_mode_tile_is_coloured_by_state():
    d = yaml.safe_load(open(SOURCE).read())
    top = d["views"][0]["sections"][0]["cards"]
    modes = [c for c in top if c.get("entity") == "sensor.pe_state_status"]
    assert sorted(c.get("color", "none") for c in modes) == ["blue", "green", "none", "orange", "red"]
    assert all(c.get("visibility") for c in modes)
    neutral = next(c for c in modes if "color" not in c)                  # before setup: not red, not a colour at all
    assert neutral["visibility"][0]["state"] == "Not set up yet"
    text = yaml.safe_dump(d["views"][0])
    assert "history-graph" not in text                     # history lives on its own tab



def _plant(id, name, enabled=True):
    from pe_core.config import SolarPlant
    return SolarPlant(id=id, name=name, power={}, energy_today={}, enabled=enabled)


def test_energy_flow_card_matches_shipped_default():
    """One plant, 18000 Wh, 12% reserve, an EV, solis: byte-identical to the card shipped in dashboard.lovelace."""
    from pe_core.dashboard import SOURCE, energy_flow_card
    text = open(SOURCE).read()
    lines = text.split("\n")
    begin = next(i for i, ln in enumerate(lines) if ln.strip().startswith("# BEGIN energy-flow"))
    end = next(i for i, ln in enumerate(lines) if ln.strip() == "# END energy-flow")
    shipped = "\n".join(lines[begin + 1:end])
    card = energy_flow_card([_plant("main", "Main")], 18000, 12, True, "solis").rstrip("\n")
    assert card == shipped


def test_energy_flow_card_no_solar_plants():
    from pe_core.dashboard import energy_flow_card
    card = energy_flow_card([], 18000, 12, False, "solis")
    assert "show_solar: false" in card
    assert "mppts" not in card
    assert "additional_loads: 0" in card
    assert "load1_name" not in card
    assert "essential_load1" not in card


def test_energy_flow_card_two_plants_with_ev():
    from pe_core.dashboard import energy_flow_card
    plants = [_plant("roof", "Roof"), _plant("shed", "Shed")]
    card = energy_flow_card(plants, 10000, 15, True, "solis")
    assert "mppts: 2" in card
    assert 'pv1_name: "Roof"' in card
    assert 'pv2_name: "Shed"' in card
    assert "pv1_power_186: sensor.pe_state_solar_roof_power" in card
    assert "pv2_power_187: sensor.pe_state_solar_shed_power" in card
    assert "sensor.pe_state_solar_power" not in card
    assert "load1_name: Car" in card
    assert "essential_load1: sensor.pe_state_ev_power" in card


def test_energy_flow_card_caps_panels_at_five():
    from pe_core.dashboard import energy_flow_card
    plants = [_plant(f"p{i}", f"Plant {i}") for i in range(6)]
    card = energy_flow_card(plants, 18000, 12, False, "solis")
    assert "mppts: 5" in card
    assert "1 more plant feeds the total only" in card
    assert "pv5_power_113: sensor.pe_state_solar_p4_power" in card
    assert "p5_power" not in card


def test_energy_flow_card_disabled_plants_excluded():
    from pe_core.dashboard import energy_flow_card
    plants = [_plant("main", "Main"), _plant("off", "Off", enabled=False)]
    card = energy_flow_card(plants, 18000, 12, False, "solis")
    assert "mppts: 1" in card
    assert "pv1_power_186: sensor.pe_state_solar_power" in card
    assert "off" not in card


def test_sync_dashboard_splices_card_and_writes_only_on_change(tmp_path):
    from pe_core.dashboard import energy_flow_card, sync_dashboard
    target = tmp_path / "powerengine" / "dashboard.yaml"
    card = energy_flow_card([_plant("main", "Main")], 18000, 12, True, "solis")
    assert sync_dashboard(str(target), card=card) is True
    first = target.read_text()
    assert sync_dashboard(str(target), card=card) is False
    assert target.read_text() == first

    card2 = energy_flow_card([_plant("roof", "Roof"), _plant("shed", "Shed")], 10000, 15, False, "solis")
    assert sync_dashboard(str(target), card=card2) is True
    updated = target.read_text()
    assert updated != first
    assert "pv2_power_187: sensor.pe_state_solar_shed_power" in updated
    assert "# BEGIN energy-flow" in updated and "# END energy-flow" in updated


def test_costs_tab_templates_read_the_new_waterfall_steps():
    """The waterfall's headline renders against the real step labels (no old steps)."""
    import jinja2

    from pe_core.costs import waterfall
    d = yaml.safe_load(open(SOURCE))
    cards = [c for v in d["views"] for s in v.get("sections", []) for c in s.get("cards", [])]
    texts = [c["content"] for c in cards if c.get("type") == "markdown"]
    headline = next(t for t in texts if "macro headline" in t)
    for old in ("Day-to-day cost", "Battery carry-over", "Battery on self-use", "self-use"):
        assert old not in headline, old

    days = [{"date": f"2026-09-{n:02d}", "complete": True, "scenarios": {
        "none": 10.0, "solar": 8.0, "tariff": 7.0, "self_use_adj": 6.0, "actual_adj": 4.0, "carry": 0.5,
        "events_metered": 0.0, "axle_income": 0.0}} for n in range(1, 11)]
    attrs = {"periods": {p: waterfall(days, p) for p in ("yesterday", "week", "month", "days30")}}
    env = jinja2.Environment()
    env.globals["state_attr"] = lambda ent, attr: attrs.get(attr) if ent == "sensor.pe_cost_waterfall" else None
    ctx = lambda t: " ".join(env.from_string(t.replace("<<tariff>>", "EDF tariff").replace(  # noqa: E731
        "<<event>>", "Axle").replace("<<supplier>>", "EDF")).render().split())
    text = ctx(headline)
    # week: 7 days x (10, 4.5 paid): none 70, solar+tariff 49, saved 3.5 x 7 = 24.5 from the battery and PowerEngine
    assert "Last 7 days: you paid £31.50" in text and "£49.00" in text and "saved £17.50" in text
