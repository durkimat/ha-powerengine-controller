"""The package file earlier versions wrote is retired once the integration holds the watchdog (pe_core/hapackage.py)."""
import os
from types import SimpleNamespace

from replay_harness import import_powerengine

from pe_core import hapackage as hp

MAIN = hp.MAIN
OURS = hp.MARKER_PREFIX if hasattr(hp, "MARKER_PREFIX") else "# Managed by PowerEngine:"
OURS_TEXT = OURS + " rewritten when PowerEngine updates.\nscript: {}\n"


def make_ha(tmp_path, packages=True):
    ha = tmp_path / "ha"
    (ha / "powerengine").mkdir(parents=True)
    if packages:
        (ha / "packages").mkdir()
    return ha


def test_recognised_means_ours_by_marker_header_or_the_old_combined_file():
    assert hp.recognised(OURS_TEXT)
    assert hp.recognised("# PowerEngine package: the one-button update, the AppDaemon restarts and the watchdog.\n")
    combined = "# PowerEngine handover: one switch for the controller\nbattery_handover_to_powerengine\n"
    assert hp.recognised(combined)
    assert not hp.recognised("# PowerEngine handover: my own notes\nscript: {}\n")
    assert not hp.recognised("# my own package\nscript: {}\n")


def test_retire_removes_ours_with_a_backup_and_leaves_the_rest(tmp_path):
    ha = make_ha(tmp_path)
    pk = ha / "packages"
    (pk / MAIN).write_text(OURS_TEXT)
    (pk / "mine.yaml").write_text("# my own package\n")
    r = hp.retire(str(ha), "20261106-120000")
    assert r["removed"] == [MAIN] and not r["unmanaged"] and not r["errors"]
    assert sorted(os.listdir(pk)) == sorted(["mine.yaml", MAIN + ".bak-20261106"])
    assert (pk / (MAIN + ".bak-20261106")).read_text() == OURS_TEXT
    assert [n for n in os.listdir(pk) if n.endswith(".yaml")] == ["mine.yaml"]          # HA loads only *.yaml
    assert hp.retire(str(ha), "20261106-120100") == {"removed": [], "unmanaged": [], "errors": []}   # nothing left


def test_a_file_that_is_not_ours_is_never_touched(tmp_path):
    ha = make_ha(tmp_path)
    mine = "# PowerEngine handover: my own notes\nscript: {}\n"
    (ha / "packages" / MAIN).write_text(mine)
    r = hp.retire(str(ha), "20261106-120000")
    assert r == {"removed": [], "unmanaged": [MAIN], "errors": []}
    assert (ha / "packages" / MAIN).read_text() == mine and os.listdir(ha / "packages") == [MAIN]


def test_no_packages_folder_means_nothing_to_do_and_nothing_created(tmp_path):
    ha = make_ha(tmp_path, packages=False)
    assert hp.retire(str(ha), "20261106-120000") == {"removed": [], "unmanaged": [], "errors": []}
    assert not os.path.exists(ha / "packages")


def test_only_the_newest_backups_are_kept(tmp_path):
    ha = make_ha(tmp_path)
    pk = ha / "packages"
    for day in range(1, 6):
        (pk / MAIN).write_text(OURS_TEXT)
        hp.retire(str(ha), f"2026110{day}-120000")
    backups = sorted(n for n in os.listdir(pk) if ".bak-" in n)
    assert backups == [f"{MAIN}.bak-2026110{d}" for d in (3, 4, 5)] and len(backups) == hp.KEEP_BACKUPS


def test_an_unwritable_folder_is_an_error_not_a_crash(tmp_path, monkeypatch):
    ha = make_ha(tmp_path)
    (ha / "packages" / MAIN).write_text(OURS_TEXT)

    def boom(*a, **k):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(hp.os, "remove", boom)
    r = hp.retire(str(ha), "20261106-120000")
    assert r["removed"] == [] and "Permission denied" in r["errors"][0][1]


# --- the app ---------------------------------------------------------------------------------------------

def app(monkeypatch, tmp_path):
    pe = import_powerengine(monkeypatch)
    ha = make_ha(tmp_path)
    s = SimpleNamespace(logs=[], calls=[], _save_path=lambda: str(ha / "powerengine" / "config.yaml"))
    s.log = lambda msg, level="INFO": s.logs.append((level, msg))
    s.host = SimpleNamespace(call_service=lambda service, **kw: s.calls.append(service))
    s._ha_config_dir = lambda: pe.PowerEngine._ha_config_dir(s)
    return pe, s, ha


def test_the_app_removes_the_package_once_and_home_assistant_forgets_its_script_and_automation(monkeypatch, tmp_path):
    pe, s, ha = app(monkeypatch, tmp_path)
    (ha / "packages" / MAIN).write_text(OURS_TEXT)
    pe.PowerEngine._retire_package(s)
    assert not (ha / "packages" / MAIN).exists() and s.calls == ["script/reload", "automation/reload"]
    assert any("removed" in m for _lvl, m in s.logs)
    pe.PowerEngine._retire_package(s)                                   # once per run
    assert s.calls == ["script/reload", "automation/reload"]


def test_the_app_says_so_when_a_package_file_is_not_its_own(monkeypatch, tmp_path):
    pe, s, ha = app(monkeypatch, tmp_path)
    (ha / "packages" / MAIN).write_text("# PowerEngine handover: my own notes\n")
    pe.PowerEngine._retire_package(s)
    assert (ha / "packages" / MAIN).exists() and s.calls == []
    assert any(lvl == "WARNING" and "not PowerEngine's" in m for lvl, m in s.logs)
