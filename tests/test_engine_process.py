"""The engine as its own process (engine/engine_main.py): started for real, it connects to the integration's
end of the protocol, runs the engine on it, and stops when asked."""

import os
import pathlib
import subprocess
import sys

import pytest
from host_support import FakeHome, wait_for

from pe_core import protocol
from pe_core.host_server import HostServer

APP = pathlib.Path(__file__).resolve().parents[1] / "engine"


def start(address, secret, tmp_path, wait="10"):
    env = {**os.environ, "PE_SECRET": secret, "PYTHONPATH": str(APP)}
    return subprocess.Popen([sys.executable, str(APP / "engine_main.py"), "--address", address, "--wait", wait,
                             "--settings", str(tmp_path / "config.yaml")],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, cwd=tmp_path)


def test_an_unconfigured_engine_runs_in_its_own_process_publishes_its_entities_and_stops_when_asked(tmp_path):
    home = FakeHome()
    server = HostServer(home, "s3cret")
    home.server = server
    address = server.start()
    process = start(address, "s3cret", tmp_path)
    try:
        wait_for(lambda: len(home.written) > 80, 30, "the entities to be published through the connection")
        written = {w[0] for w in home.written}
        assert "sensor.pe_diag_version" in written and "sensor.pe_diag_heartbeat" in written
        wait_for(lambda: "pe_config_save" in server.wants()[1], 10, "the engine to listen for its events")
        assert server.engine["engine"].count(".") == 2
        server.ask_to_stop()
        assert process.wait(30) == 0
    finally:
        process.kill()
        process.communicate()
        server.stop()


def test_the_engine_exits_with_its_own_status_when_the_integration_speaks_another_protocol(tmp_path):
    server = HostServer(FakeHome(), "s3cret", proto=protocol.VERSION + 1)
    address = server.start()
    process = start(address, "s3cret", tmp_path)
    try:
        assert process.wait(30) == 10
    finally:
        process.kill()
        process.communicate()
        server.stop()


def test_the_engine_exits_when_the_secret_is_wrong_or_missing_or_nothing_answers(tmp_path):
    server = HostServer(FakeHome(), "right")
    address = server.start()
    try:
        wrong = start(address, "wrong", tmp_path)
        assert wrong.wait(30) == 11
        env = {k: v for k, v in os.environ.items() if k != "PE_SECRET"}
        none = subprocess.run([sys.executable, str(APP / "engine_main.py"), "--address", address], env={
            **env, "PYTHONPATH": str(APP)}, capture_output=True, text=True, cwd=tmp_path, timeout=30)
        assert none.returncode == 11 and "PE_SECRET" in none.stderr
        silent = start("tcp:127.0.0.1:1", "x", tmp_path, wait="0.5")
        assert silent.wait(30) == 12
    finally:
        server.stop()


@pytest.mark.parametrize("bad", ["tcp:10.0.0.5:80", "nonsense"])
def test_the_engine_refuses_an_address_that_is_not_local(tmp_path, bad):
    process = start(bad, "x", tmp_path)
    process.communicate(timeout=30)
    assert process.returncode != 0
