from datetime import datetime

import pytest
import yaml

from pe_core.config import ConfigError, load_config
from pe_core.store import KEEP_BACKUPS, save_config

GOOD = {"inputs": {"battery_soc": {"entity": "sensor.solis_battery_soc"}}, "operation": {"mode": "passive"}}


def test_first_save_creates_folder(tmp_path):
    path = tmp_path / "powerengine" / "config.yaml"
    cfg, backup = save_config(str(path), GOOD)
    assert backup is None and path.exists()
    assert load_config([str(path)])[0].inputs == GOOD["inputs"]


def test_resave_keeps_backup(tmp_path):
    path = tmp_path / "config.yaml"
    save_config(str(path), GOOD)
    _, backup = save_config(str(path), {**GOOD, "operation": {"mode": "active"}}, now=datetime(2026, 9, 25, 12))
    assert backup.endswith(".bak-20260925-120000")
    assert yaml.safe_load(open(backup))["operation"]["mode"] == "passive"


def test_invalid_config_changes_nothing(tmp_path):
    path = tmp_path / "config.yaml"
    save_config(str(path), GOOD)
    before = path.read_text()
    with pytest.raises(ConfigError):
        save_config(str(path), {"inputs": {"battery_soc": {"entity": "switch.nope"}}})
    assert path.read_text() == before


def test_backups_are_pruned(tmp_path):
    path = tmp_path / "config.yaml"
    for i in range(KEEP_BACKUPS + 5):
        save_config(str(path), GOOD, now=datetime(2026, 9, 25, 12, 0, i))
    assert len(list(tmp_path.glob("config.yaml.bak-*"))) == KEEP_BACKUPS
