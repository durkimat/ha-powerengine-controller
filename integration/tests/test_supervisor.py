"""The supervisor against small stand-in engines: restarts after a wait, the exit statuses that must not restart, what
the engine prints, and stopping."""

import asyncio
import sys
import textwrap

from custom_components.powerengine import const
from custom_components.powerengine.supervisor import EngineSupervisor, installed_version


def engine_dir(tmp_path, script, version="1.2.3"):
    (tmp_path / "pe_core").mkdir()
    (tmp_path / "pe_core" / "__init__.py").write_text(f'__version__ = "{version}"\n')
    (tmp_path / "engine_main.py").write_text(textwrap.dedent(script))
    return tmp_path


def supervisor(tmp_path, script, backoff=(0.05, 0.05), **kw):
    seen = {"crashing": [], "changes": 0, "asked": 0}
    def ask_stop():
        seen["asked"] += 1
        return False                                      # nothing is connected
    sup = EngineSupervisor(engine_dir(tmp_path, script), str(tmp_path / "config.yaml"), lambda: "tcp:127.0.0.1:1", "s",
                           running_version=lambda: "1.2.3", ask_stop=ask_stop,
                           on_change=lambda: seen.update(changes=seen["changes"] + 1),
                           on_crashing=seen["crashing"].append, python=sys.executable, backoff=backoff, **kw)
    return sup, seen


async def until(condition, timeout=20.0):
    end = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end:
        if condition():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("timed out")


async def test_an_engine_that_keeps_stopping_is_started_again_after_each_wait_and_a_repair_is_raised(tmp_path):
    sup, seen = supervisor(tmp_path, "import sys; sys.exit(3)")
    sup.start()
    await until(lambda: sup.restarts >= 4)
    assert sup.exit_code == 3 and seen["crashing"] == [True]
    await sup.stop()
    assert sup.status == const.STATUS_STOPPED


async def test_a_protocol_mismatch_is_not_started_again_until_home_assistant_restarts(tmp_path):
    sup, _ = supervisor(tmp_path, "import sys; sys.exit(10)")
    sup.start()
    await until(lambda: sup.status == const.STATUS_NEEDS_RESTART)
    await asyncio.sleep(0.3)
    assert sup.restarts == 0 and sup.exit_code == 10
    await sup.stop()


async def test_an_engine_that_is_refused_its_secret_is_failed_not_looped(tmp_path):
    sup, _ = supervisor(tmp_path, "import sys; sys.exit(11)")
    sup.start()
    await until(lambda: sup.status == const.STATUS_FAILED)
    assert sup.restarts == 0
    await sup.stop()


async def test_what_the_engine_prints_goes_to_the_log_at_its_level_and_is_kept_for_diagnostics(tmp_path, caplog):
    script = '''
        import sys, time
        print("WARNING the battery is slow", flush=True)
        print("a plain line", flush=True)
        time.sleep(30)
    '''
    sup, _ = supervisor(tmp_path, script)
    with caplog.at_level("INFO"):
        sup.start()
        await until(lambda: len(sup.log) >= 2)
    await sup.stop()
    assert list(sup.log) == ["WARNING the battery is slow", "a plain line"]
    records = [(r.levelname, r.getMessage()) for r in caplog.records if r.name.endswith(".engine")]
    assert ("WARNING", "the battery is slow") in records and ("INFO", "a plain line") in records


async def test_the_engine_is_connected_when_the_server_says_so_and_not_before(tmp_path):
    sup, _ = supervisor(tmp_path, "import time; time.sleep(30)")
    sup.start()
    await until(lambda: sup.status == const.STATUS_STARTING)
    assert sup.running is None
    sup.connected()
    assert sup.status == const.STATUS_RUNNING and sup.running == "1.2.3"
    await sup.stop()


async def test_an_engine_not_connected_is_terminated_at_once_when_stopped_and_one_that_is_asked_gets_time(tmp_path):
    sup, seen = supervisor(tmp_path, "import time; time.sleep(60)")
    sup.start()
    await until(lambda: sup._process is not None)
    started = asyncio.get_running_loop().time()
    await sup.stop()
    assert asyncio.get_running_loop().time() - started < 5 and seen["asked"] == 1      # not connected: no 20 s wait


async def test_a_restart_starts_a_new_engine_without_a_wait(tmp_path):
    sup, _ = supervisor(tmp_path, "import time; time.sleep(60)", backoff=(30,))
    sup.start()
    await until(lambda: sup._process is not None)
    first = sup._process.pid
    await sup.restart()
    await until(lambda: sup._process is not None and sup._process.pid != first and sup._process.returncode is None)
    assert sup.restarts == 1
    await sup.stop()


async def test_the_installed_version_is_read_from_the_files_and_a_newer_install_is_seen(tmp_path):
    sup, _ = supervisor(tmp_path, "import time; time.sleep(60)")
    assert installed_version(tmp_path) == "1.2.3" and installed_version(tmp_path / "nowhere") is None
    sup.start()
    await until(lambda: sup.status == const.STATUS_STARTING)
    sup.connected()
    assert not sup.update_needed()
    (tmp_path / "pe_core" / "__init__.py").write_text('__version__ = "1.2.4"\n')
    assert sup.update_needed()
    await sup.stop()
