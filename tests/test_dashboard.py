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
