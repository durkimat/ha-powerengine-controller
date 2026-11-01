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
    assert [y for y in yamls if not y.endswith(".yaml")] == [
        "ha_packages/powerengine_handover.yml", "ha_packages/powerengine_predbat_handover.yml",
        "pe_core/adapters/devices/solis.yml"], yamls


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
    assert len(desk) == len(phone) == 4
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



def _all_cards(d):
    """Every card of every view, including those inside a section card's own `cards`."""
    def walk(cards):
        for c in cards:
            yield c
            yield from walk(c.get("cards") or [])
    return list(walk(c for v in d["views"] for s in v.get("sections", []) for c in s.get("cards", [])))


def _costs_view():
    text = open(SOURCE, encoding="utf-8").read()
    start = text.index("  - title: Costs\n")
    return text[start:text.index("  - title: Health\n")]



def test_last_bar_is_the_waterfalls_you_paid_with_the_events_in():
    """Day by day, the last bar is what the waterfall ends on: PowerEngine plus the event half-hours less the
    event payment (costs._steps_for's r6), so the bars and the waterfall always agree."""
    from pe_core.costs import waterfall
    day = {"date": "2026-10-03", "complete": True, "scenarios": {
        "none": 6.40, "solar": 5.0, "tariff": 3.0, "actual_adj": 2.5, "carry": 0.0,
        "events_metered": 0.5, "axle_income": 4.2}}
    paid = waterfall([day], "yesterday")["steps"][-1]["value"]
    sc = day["scenarios"]
    assert paid == round(sc["actual_adj"] + sc["carry"] + sc["events_metered"] - sc["axle_income"], 2) == -1.2


def test_costs_page_no_longer_describes_the_old_cost_layers():
    """The waterfall is Solar, tariff and PowerEngine, then events. The page's tables and text follow it."""
    view = _costs_view()
    for stale in ("Unexplained", "Carried in battery", "How the layers", "Battery control", "carry-over", "S0",
                  "Ledger correction", "first-in-first-out"):
        assert stale not in view, stale
    assert "**How the numbers are worked out**" in view


def test_plan_actions_table_renders_when_a_window_has_no_optional_key():
    """HA raises on a missing key ('dict object' has no attribute 'manual') and the card then shows nothing, so the
    Actions table must render with windows that lack the optional `manual` key (0.9.102)."""
    import jinja2
    d = yaml.safe_load(open(SOURCE))
    card = next(c for v in d["views"] for s in v.get("sections", []) for c in s.get("cards", [])
                if c.get("type") == "markdown" and "'sensor.pe_plan', 'windows'" in c["content"])
    plain = {"from": "15:30", "to": "16:00", "day": "", "action": "self_use", "target_soc": None, "price": "6.66p",
             "estimated": False, "soc_start": 72, "soc_end": 71, "reason": "the battery covers the house"}
    attrs = {"windows": [plain, {**plain, "manual": True}], "windows_more": 0}
    env = jinja2.Environment(undefined=jinja2.StrictUndefined)
    env.globals["state_attr"] = lambda _e, k: attrs.get(k)
    env.globals["states"] = lambda _e: "3"
    out = env.from_string(card["content"]).render()
    assert "**self-use** |" in out and "**self-use** (manual) |" in out



def test_one_engine_tab_is_written_for_the_engine_in_use():
    from pe_core.dashboard import render
    text = open(SOURCE, encoding="utf-8").read()

    def titles(out):
        return [v["title"] for v in yaml.safe_load(out)["views"]]
    assert {"Engine v1", "Engine v2"} <= set(titles(render(text)))                 # no engine given: both pages
    v2, v1 = titles(render(text, engine="v2")), titles(render(text, engine="v1"))
    assert "Engine v2" in v2 and "Engine v1" not in v2
    assert "Engine v1" in v1 and "Engine v2" not in v1
    other = lambda ts: [t for t in ts if not t.startswith("Engine")]    # noqa: E731
    assert other(v2) == other(v1)                                          # the rest of the pages is the same
    assert "powerengine-v2-plan-card" not in render(text, engine="v1")


def test_costs_page_follows_one_shared_period():
    """The picker sets the period; the waterfall, day-by-day, engines and events cards follow it (shared: true), and the
    old fixed 7-day pieces (headline text, chart, day table, step breakdown) are gone."""
    d = yaml.safe_load(open(SOURCE))
    costs = next(v for v in d["views"] if v["title"] == "Costs")
    cards = _all_cards({"views": [costs]})
    types = {c.get("type"): c for c in cards}
    assert "custom:powerengine-costs-period-card" in types
    for t in ("custom:powerengine-waterfall-card", "custom:powerengine-engine-compare-card",
              "custom:powerengine-events-card"):
        assert types[t].get("shared") is True and "period" not in types[t], t
    assert "custom:powerengine-costs-days-card" in types
    charts = {c.get("card", {}).get("type") for c in cards if c.get("type") == "conditional"}
    assert "custom:apexcharts-card" not in charts
    view = _costs_view()
    for gone in ("Today so far", "How yesterday's", "macro headline"):
        assert gone not in view, gone
