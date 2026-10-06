"""PowerEngine keeps its Home Assistant package files in step (0.9.109, pe_core/hapackage.py)."""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from replay_harness import import_powerengine

from pe_core import hapackage as hp
from pe_core.config import parse_config

ROOT = Path(__file__).resolve().parents[1]
SHIPPED = ROOT / "apps" / "powerengine" / "ha_packages"
DOCS = ROOT / "docs" / "ha"
MAIN_TEXT = hp.MARKER + "\nscript: {}\n"
PREDBAT_TEXT = hp.MARKER + "\ninput_select: {}\n"
LOAD = "Load PowerEngine's Home Assistant changes"
SHIP = {hp.MAIN: MAIN_TEXT, hp.PREDBAT: PREDBAT_TEXT}


def acts(files, controller="none"):
    return {a.name: a.action for a in hp.plan("/p", controller, SHIP, lambda path: files.get(os.path.basename(path)))}


# --- decisions ---------------------------------------------------------------------------------------

def test_fresh_install_writes_main_only_unless_predbat():
    assert acts({}) == {hp.MAIN: "write"}
    assert acts({}, "predbat") == {hp.MAIN: "write", hp.PREDBAT: "write"}
    assert acts({}, "other") == {hp.MAIN: "write"} and acts({}, "unset") == {hp.MAIN: "write"}


def test_identical_is_kept_and_a_marked_file_that_differs_is_updated():
    assert acts({hp.MAIN: MAIN_TEXT}) == {hp.MAIN: "keep"}
    assert acts({hp.MAIN: MAIN_TEXT.replace("script", "scripts")}) == {hp.MAIN: "update"}
    assert acts({hp.MAIN: MAIN_TEXT.replace("\n", "\r\n")}) == {hp.MAIN: "keep"}      # line endings do not count


def test_a_recognised_predbat_file_is_removed_when_not_wanted_and_kept_when_wanted():
    files = {hp.MAIN: MAIN_TEXT, hp.PREDBAT: PREDBAT_TEXT}
    assert acts(files) == {hp.MAIN: "keep", hp.PREDBAT: "remove"}
    assert acts(files, "predbat") == {hp.MAIN: "keep", hp.PREDBAT: "keep"}


def test_older_shipped_versions_and_the_old_combined_file_are_recognised():
    for rel in ("# PowerEngine package: the one-button update, the AppDaemon restarts and the watchdog.\nscript: {}\n",
                "# PowerEngine and Predbat handover (OPTIONAL: only if you run Predbat alongside PowerEngine).\n"):
        assert hp.recognised(rel)
    combined = ("# PowerEngine handover: one switch for which controller drives the battery, Predbat or PowerEngine.\n"
                "script:\n  battery_handover_to_powerengine:\n    alias: x\n")
    assert hp.recognised(combined)
    assert acts({hp.MAIN: combined}) == {hp.MAIN: "update"}             # the owner's case
    assert not hp.recognised("# PowerEngine handover: my own notes\nscript: {}\n")      # no signature of the old file


def test_an_unknown_user_file_is_never_touched():
    mine = "# my own package\nscript: {}\n"
    assert acts({hp.MAIN: mine}) == {hp.MAIN: "skip_unmanaged"}
    assert acts({hp.MAIN: mine, hp.PREDBAT: mine}) == {hp.MAIN: "skip_unmanaged", hp.PREDBAT: "skip_unmanaged"}
    assert hp.plan("/p", "none", SHIP, lambda p: mine)[0].text is None


# --- doing it ----------------------------------------------------------------------------------------

def make_ha(tmp_path, packages=True):
    ha = tmp_path / "ha"
    (ha / "powerengine").mkdir(parents=True)
    if packages:
        (ha / "packages").mkdir()
    return ha


def test_sync_writes_updates_and_backs_up_with_a_name_home_assistant_ignores(tmp_path):
    ha = make_ha(tmp_path)
    r = hp.sync(str(ha), "none", SHIP, "20261006-120000")
    assert [a.action for a in r["actions"]] == ["write"] and (ha / "packages" / hp.MAIN).read_text() == MAIN_TEXT
    (ha / "packages" / hp.PREDBAT).write_text(PREDBAT_TEXT)
    (ha / "packages" / hp.MAIN).write_text(MAIN_TEXT + "# edited\n")
    r = hp.sync(str(ha), "none", SHIP, "20261006-120500")
    assert {a.name: a.action for a in r["actions"]} == {hp.MAIN: "update", hp.PREDBAT: "remove"} and not r["errors"]
    names = sorted(os.listdir(ha / "packages"))
    assert names == [hp.MAIN, hp.MAIN + ".bak-20261006", hp.PREDBAT + ".bak-20261006"]
    assert (ha / "packages" / (hp.MAIN + ".bak-20261006")).read_text().endswith("# edited\n")
    assert [n for n in names if n.endswith(".yaml")] == [hp.MAIN]            # HA loads only *.yaml
    hp.sync(str(ha), "none", SHIP, "20261006-130000")                         # identical now: nothing new
    assert sorted(os.listdir(ha / "packages")) == names
    (ha / "packages" / hp.MAIN).write_text(MAIN_TEXT + "# again\n")
    hp.sync(str(ha), "none", SHIP, "20261006-140000")
    assert hp.MAIN + ".bak-20261006-2" in os.listdir(ha / "packages")         # a second backup the same day


def test_sync_never_creates_the_packages_folder(tmp_path):
    ha = make_ha(tmp_path, packages=False)
    r = hp.sync(str(ha), "predbat", SHIP, "20261006-120000")
    assert r["dir_exists"] is False and not os.path.exists(ha / "packages")
    assert hp.report(r, lambda e: False)[0] == "no_packages_dir"


def test_an_unwritable_folder_is_an_error_not_a_crash(tmp_path, monkeypatch):
    ha = make_ha(tmp_path)

    def boom(*a, **k):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(hp.os, "replace", boom)
    r = hp.sync(str(ha), "none", SHIP, "20261006-120000")
    state, attrs = hp.report(r, lambda e: False)
    assert state == "error" and "Permission denied" in attrs["message"] and attrs["reload_needed"] is False
    assert hp.changes(r) == []


# --- the sensor ----------------------------------------------------------------------------------------

def result(controller, actions):
    return {"dir_exists": True, "controller": controller, "errors": [], "actions": actions}


def fa(name, action, managed=True):
    return hp.FileAction(name, action, managed)


def test_reload_needed_comes_from_entity_presence():
    main_only = result("none", [fa(hp.MAIN, "keep")])
    assert hp.reload_needed("none", main_only, lambda e: e == hp.MAIN_ENTITY) is False
    assert hp.reload_needed("none", main_only, lambda e: False) is True                       # script missing
    both = result("predbat", [fa(hp.MAIN, "keep"), fa(hp.PREDBAT, "write")])
    assert hp.reload_needed("predbat", both, lambda e: e == hp.MAIN_ENTITY) is True           # selector missing
    assert hp.reload_needed("predbat", both, lambda e: True) is False
    gone = result("none", [fa(hp.MAIN, "keep"), fa(hp.PREDBAT, "remove")])
    assert hp.reload_needed("none", gone, lambda e: True) is True                             # selector still loaded
    assert hp.reload_needed("none", gone, lambda e: e == hp.MAIN_ENTITY) is False
    nothing = result("none", [fa(hp.MAIN, "keep")])
    assert hp.reload_needed("none", nothing, lambda e: e == hp.MAIN_ENTITY) is False


def test_reload_is_not_claimed_for_a_file_that_is_not_ours():
    mine = result("none", [fa(hp.MAIN, "skip_unmanaged", False)])
    assert hp.reload_needed("none", mine, lambda e: False) is False
    state, attrs = hp.report(mine, lambda e: False)
    assert state == "unmanaged" and attrs["files"] == [{"name": hp.MAIN, "action": "skip_unmanaged", "managed": False}]
    user_selector = result("none", [fa(hp.MAIN, "keep"), fa(hp.PREDBAT, "skip_unmanaged", False)])
    assert hp.reload_needed("none", user_selector, lambda e: True) is False
    assert hp.report(user_selector, lambda e: True)[0] == "ok"     # not wanted: not ours


def test_report_states_and_wording():
    state, attrs = hp.report(result("none", [fa(hp.MAIN, "update")]), lambda e: False)
    assert state == "reload_needed" and attrs["reload_needed"] is True and LOAD in attrs["message"]
    assert "redbat" not in attrs["message"]
    state, attrs = hp.report(result("none", [fa(hp.MAIN, "keep")]), lambda e: e == hp.MAIN_ENTITY)
    assert state == "ok" and attrs["reload_needed"] is False and set(attrs) == {"files", "reload_needed", "message"}
    state, attrs = hp.report(result("predbat", [fa(hp.MAIN, "keep"), fa(hp.PREDBAT, "skip_unmanaged", False)]),
                             lambda e: True)
    assert state == "unmanaged" and hp.PREDBAT in attrs["message"]


# --- the app -------------------------------------------------------------------------------------------

def app(monkeypatch, tmp_path, controller="none", demo=False, packages=True):
    pe = import_powerengine(monkeypatch)
    ha = make_ha(tmp_path, packages)
    cfg = parse_config({"system": {"other_controller": controller}})
    present = set()
    s = SimpleNamespace(cfg=cfg, _demo=demo, published=[], logs=[], notes=[], present=present, _published={},
                        _save_path=lambda: str(ha / "powerengine" / "config.yaml"))
    s.log = lambda msg, level="INFO": s.logs.append((level, msg))
    s._notify = lambda event, msg: s.notes.append(msg)
    s.get_state = lambda eid, **k: "on" if eid in present else None
    s._ha_config_dir = lambda: pe.PowerEngine._ha_config_dir(s)
    s._entity_present = lambda eid: pe.PowerEngine._entity_present(s, eid)
    s._publish_package = lambda: pe.PowerEngine._publish_package(s)
    s._publish_if_changed = lambda key, state, attrs: s.published.append((key, state, attrs))
    return pe, s, ha


def test_the_app_writes_backs_up_logs_notifies_and_publishes(monkeypatch, tmp_path):
    pe, s, ha = app(monkeypatch, tmp_path)
    (ha / "packages" / hp.MAIN).write_text(hp.MARKER + "\nold: 1\n")
    (ha / "packages" / hp.PREDBAT).write_text("# PowerEngine and Predbat handover (OPTIONAL: x)\n")
    pe.PowerEngine._package_sync(s)
    pkgs = ha / "packages"
    assert (pkgs / hp.MAIN).read_text() == hp.shipped_texts()[hp.MAIN] and not (pkgs / hp.PREDBAT).exists()
    assert len([n for n in os.listdir(pkgs) if ".bak-" in n]) == 2
    assert len([m for lvl, m in s.logs if m.startswith("Home Assistant package:")]) == 2
    assert len(s.notes) == 1 and LOAD in s.notes[0][2]
    key, state, attrs = s.published[-1]
    assert key == "diag_package" and state == "reload_needed" and attrs["reload_needed"] is True
    s.present.add(hp.MAIN_ENTITY)
    pe.PowerEngine._publish_package(s)
    assert s.published[-1][1] == "ok"


def test_the_app_writes_nothing_in_a_demo_or_without_a_packages_folder(monkeypatch, tmp_path):
    pe, s, ha = app(monkeypatch, tmp_path, demo=True)
    pe.PowerEngine._package_sync(s)
    assert os.listdir(ha / "packages") == [] and s.published == [] and s.notes == []
    pe, s, ha = app(monkeypatch, tmp_path / "b", packages=False)
    pe.PowerEngine._package_sync(s)
    assert not (ha / "packages").exists() and s.published[-1][1] == "no_packages_dir" and s.notes == []
    assert s.published[-1][2]["reload_needed"] is False


def test_the_app_skips_the_sync_without_a_config(monkeypatch, tmp_path):
    pe, s, ha = app(monkeypatch, tmp_path)
    s.cfg = None
    pe.PowerEngine._package_sync(s)
    assert os.listdir(ha / "packages") == [] and s.published == []


def test_choosing_predbat_writes_its_file(monkeypatch, tmp_path):
    pe, s, ha = app(monkeypatch, tmp_path, controller="predbat")
    pe.PowerEngine._package_sync(s)
    assert sorted(os.listdir(ha / "packages")) == [hp.MAIN, hp.PREDBAT]


# --- the shipped files -----------------------------------------------------------------------------------

@pytest.mark.parametrize("name,shipped", sorted(hp.SHIPPED_FILES.items()))
def test_docs_files_are_identical_to_the_shipped_ones(name, shipped):
    assert (DOCS / name).read_text() == (SHIPPED / shipped).read_text()
    assert (SHIPPED / shipped).read_text().split("\n", 1)[0] == hp.MARKER


def test_shipped_files_are_recognised_as_ours_and_not_yaml_for_appdaemon():
    for name, text in hp.shipped_texts().items():
        assert hp.recognised(text) and name.endswith(".yaml")
    assert not [p for p in (ROOT / "apps" / "powerengine" / "ha_packages").iterdir() if p.suffix == ".yaml"]


def test_unset_controller_leaves_the_predbat_file_alone():
    files = {hp.MAIN: MAIN_TEXT, hp.PREDBAT: PREDBAT_TEXT.replace("{}", "{x: 1}")}
    assert acts(files, "unset") == {hp.MAIN: "keep", hp.PREDBAT: "keep"}       # recognised, even if different
    assert acts({hp.MAIN: MAIN_TEXT}, "unset") == {hp.MAIN: "keep"}              # absent: not written
    assert acts(files, "other")[hp.PREDBAT] == "remove" and acts(files, "none")[hp.PREDBAT] == "remove"
    r = result("unset", [fa(hp.MAIN, "keep"), fa(hp.PREDBAT, "keep")])
    for present in (lambda e: e == hp.MAIN_ENTITY, lambda e: True):
        assert hp.reload_needed("unset", r, present) is False
    assert hp.reload_needed("unset", result("unset", [fa(hp.MAIN, "keep")]), lambda e: e == hp.MAIN_ENTITY) is False
