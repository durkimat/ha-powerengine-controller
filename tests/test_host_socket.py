"""The host that runs the engine in its own process (pe_core/host_socket.py, host_server.py): the same answers as the
local host to the same questions, and what is new: a local copy of the states, listeners fed by the integration,
timers of its own, and carrying on through a lost connection."""

import logging
import threading
import time

import pytest
from host_support import FakeHome, Recorder, connected, wait_for

from pe_core import host, protocol
from pe_core.compare.harness import fake_home
from pe_core.host_local import LocalHost
from pe_core.host_socket import HostError, HostUnavailable, SocketHost


def test_the_socket_host_is_a_host_with_every_method_the_local_host_has():
    for name in (*host.METHODS, *host.EXTRAS, "args"):
        assert hasattr(SocketHost, name), name
    assert isinstance(SocketHost("tcp:127.0.0.1:1", "s"), host.Host)


# --- the same questions, the same answers (the local host over the fake Home Assistant is the reference)
@pytest.fixture(params=["local", "socket"])
def world(request):
    """(put, host): `put(entity, state, attributes)` sets Home Assistant's state; `host` is the host under test."""
    if request.param == "local":
        app = fake_home()()
        yield app.set_fake, LocalHost(app)
    else:
        with connected() as (home, _, h):
            yield home.put, h


def test_get_state_answers_the_same_whichever_host(world):
    put, h = world
    put("sensor.a", "12.5", {"unit_of_measurement": "kW", "zero": 0, "off": False})
    assert h.get_state("sensor.a") == "12.5"
    assert h.get_state("sensor.a", attribute="unit_of_measurement") == "kW"
    assert h.get_state("sensor.a", attribute="zero") == 0 and h.get_state("sensor.a", attribute="off") is False
    assert h.get_state("sensor.a", attribute="missing") is None
    assert h.get_state("sensor.a", attribute="missing", default="d") == "d"
    full = h.get_state("sensor.a", attribute="all")
    assert full["state"] == "12.5" and full["attributes"]["zero"] == 0 and full["entity_id"] == "sensor.a"
    assert h.get_state("sensor.nope") is None and h.get_state("sensor.nope", default="d") == "d"
    full["attributes"]["zero"] = 99                                   # what comes back is a copy
    assert h.get_state("sensor.a", attribute="zero") == 0


def test_the_whole_state_machine_is_one_question_whichever_host(world):
    put, h = world
    put("sensor.a", "1")
    put("person.me", "home", {"user_id": "u1"})
    everything = h.get_state()
    assert {"sensor.a", "person.me"} <= set(everything)
    assert everything["person.me"]["attributes"]["user_id"] == "u1"


def test_services_and_events_reach_home_assistant_with_their_keywords():
    with connected() as (home, _, h):
        assert h.call_service("number/set_value", entity_id="number.x", value=3.5) == {"ok": True}
        h.fire_event("pe_test_result", ok=True, message="m")
        h.call_service("logbook/log", name="PowerEngine", message="hi", namespace="default")
    assert home.calls == [("number/set_value", {"entity_id": "number.x", "value": 3.5}),
                          ("logbook/log", {"name": "PowerEngine", "message": "hi"})]
    assert home.fired == [("pe_test_result", {"ok": True, "message": "m"})]


def test_what_home_assistant_refuses_comes_back_as_an_error_not_silence():
    with connected() as (home, _, h):
        home.fail_calls = True
        with pytest.raises(HostError, match="Home Assistant said no"):
            h.call_service("number/set_value", entity_id="number.x", value=1)


def test_history_templates_and_state_writes_go_through():
    from datetime import datetime, timezone
    with connected() as (home, _, h):
        start = datetime(2026, 3, 1, tzinfo=timezone.utc)
        rows = h.get_history(entity_id="sensor.a", start_time=start, end_time=start, minimal_response=True)
        assert rows == [[{"entity_id": "sensor.a", "state": "1", "last_changed": start.isoformat()}]]
        assert h.render_template("{{ x }}") == "{{ X }}"
        h.state_writer(lambda *a: None)("sensor.pe_x", state="on", attributes={"a": 0})
    assert home.written == [("sensor.pe_x", "on", {"a": 0})]


# --- the local copy and the listeners
def test_a_read_entity_is_kept_up_to_date_without_asking_again():
    with connected() as (home, server, h):
        home.put("sensor.a", "1")
        assert h.get_state("sensor.a") == "1"
        assert "sensor.a" in server.wants()[0]
        home.put("sensor.a", "2", {"k": 1})
        wait_for(lambda: h.get_state("sensor.a") == "2", what="the copy to change")
        assert h.get_state("sensor.a", attribute="k") == 1
        home.remove("sensor.a")
        wait_for(lambda: h.get_state("sensor.a") is None, what="the copy to lose the entity")


def test_an_entity_that_appears_later_is_seen_by_a_host_that_looked_for_it_earlier():
    with connected() as (home, _, h):
        assert h.get_state("sensor.late") is None
        home.put("sensor.late", "5")
        wait_for(lambda: h.get_state("sensor.late") == "5", what="the entity to appear")


def test_a_state_listener_is_called_with_the_hosts_arguments_when_the_state_changes():
    with connected() as (home, _, h):
        home.put("switch.pause", "off")
        got = Recorder()
        h.on_state(got, "switch.pause")
        home.put("switch.pause", "off", {"only": "an attribute"})     # same state: not a change
        home.put("switch.pause", "on")
        assert got.wait() == [("switch.pause", "state", "off", "on", {})]
        assert h.get_state("switch.pause") == "on"                     # the copy changed before the callback ran


def test_a_state_listener_can_follow_one_attribute_or_everything():
    with connected() as (home, _, h):
        home.put("sensor.a", "1", {"mode": "x"})
        by_attribute, everything = Recorder(), Recorder()
        h.on_state(by_attribute, "sensor.a", attribute="mode")
        h.on_state(everything, "sensor.a", attribute="all")
        home.put("sensor.a", "2", {"mode": "x"})                       # the state moved, the attribute did not
        home.put("sensor.a", "2", {"mode": "y"})
        assert by_attribute.wait() == [("sensor.a", "mode", "x", "y", {})]
        assert len(everything.wait(2)) == 2


def test_cancelling_a_listener_stops_its_calls():
    with connected() as (home, _, h):
        home.put("sensor.a", "1")
        got, kept = Recorder(), Recorder()
        handle = h.on_state(got, "sensor.a")
        h.on_state(kept, "sensor.a")
        assert h.off_state(handle) is True and h.off_state(handle) is False
        home.put("sensor.a", "2")
        kept.wait()
        assert got.calls == []


def test_an_event_listener_hears_only_the_events_it_asked_for():
    with connected() as (home, server, h):
        got = Recorder()
        handle = h.on_event(got, "pe_config_save")
        server.event_fired("something_else", {"x": 1})
        server.event_fired("pe_config_save", {"settings": {"a": [1, 2]}})
        assert got.wait() == [("pe_config_save", {"settings": {"a": [1, 2]}}, {})]
        h.off_event(handle)
        wait_for(lambda: "pe_config_save" not in server.wants()[1], what="the event to be dropped")
        server.event_fired("pe_config_save", {})
        time.sleep(0.05)
        assert len(got.calls) == 1


def test_two_listeners_for_one_event_both_hear_it_and_it_is_asked_for_once():
    with connected() as (home, server, h):
        a, b = Recorder(), Recorder()
        first = h.on_event(a, "pe_x")
        h.on_event(b, "pe_x")
        server.event_fired("pe_x", {"n": 1})
        a.wait()
        b.wait()
        h.off_event(first)
        assert "pe_x" in server.wants()[1]                                 # still wanted by the second


def test_callbacks_run_one_at_a_time_on_one_thread():
    with connected() as (home, server, h):
        threads, inside, overlaps = set(), [0], [0]

        def slow(event, data, kwargs):
            inside[0] += 1
            overlaps[0] += inside[0] > 1
            threads.add(threading.get_ident())
            time.sleep(0.02)
            inside[0] -= 1
        h.on_event(slow, "pe_x")
        h.call_later(lambda kw: slow(None, None, None), 0.01)
        for n in range(4):
            server.event_fired("pe_x", {"n": n})
        wait_for(lambda: not inside[0] and len(threads) and h._dispatcher.idle(1), what="the callbacks")
        assert overlaps[0] == 0 and len(threads) == 1


# --- timers
def test_timers_run_on_the_host_with_their_keywords_and_can_be_cancelled():
    with connected() as (_, _, h):
        got, never = Recorder(), Recorder()
        h.call_later(got, 0.01, role="x")
        h.cancel_call(h.call_later(never, 0.01))
        every = Recorder()
        h.call_every(every, "now", 0.02)
        assert got.wait() == [({"role": "x"},)]
        every.wait(3)
        assert never.calls == []


def test_the_timers_use_homes_time_zone():
    with connected() as (_, _, h):
        assert str(h._scheduler.tz) == "Europe/London"
        handle = h.call_daily(lambda kw: None, "03:00:00")
        assert h._scheduler.timer_running(handle)
        assert h._scheduler.next_due().astimezone(h._scheduler.tz).strftime("%H:%M") == "03:00"


# --- the demo's needs
def test_tracking_cancels_exactly_what_was_registered_since_it_started():
    with connected() as (home, server, h):
        home.put("sensor.a", "1")
        before = h.call_later(lambda kw: None, 3600)
        h.start_tracking()
        timer = h.call_every(lambda kw: None, "now+3600", 3600)
        state = h.on_state(lambda *a: None, "sensor.a")
        event = h.on_event(lambda *a: None, "pe_x")
        h.cancel_all()
        assert not h._scheduler.timer_running(timer) and h._scheduler.timer_running(before)
        assert not h._state_listeners and not h._event_listeners
        assert h.handles == [] and (state, event)


def test_the_demo_can_replace_a_call_and_put_it_back():
    with connected() as (home, _, h):
        home.put("sensor.a", "real")
        h.override(get_state=lambda *a, **k: "demo")
        assert h.method("get_state")("sensor.a") == "demo"
        h.clear_overrides()
        assert h.method("get_state")("sensor.a") == "real"


# --- a lost connection
def test_calls_say_so_when_the_connection_is_down_and_reads_use_the_copy():
    with connected() as (home, server, h):
        home.put("sensor.a", "1")
        assert h.get_state("sensor.a") == "1"
        server.stop()
        wait_for(lambda: not h.connected.is_set(), what="the host to notice")
        assert h.get_state("sensor.a") == "1"                          # from its copy
        assert h.get_state("sensor.never_read", default="d") == "d"
        with pytest.raises(HostUnavailable):
            h.call_service("number/set_value", entity_id="number.x", value=1)
        assert h.get_state() is None


def test_after_a_reconnection_the_copy_is_refreshed_and_listeners_hear_what_changed_meanwhile():
    home = FakeHome()
    with connected(home) as (_, server, h):
        home.put("sensor.a", "1")
        home.put("sensor.b", "1")
        h.get_state("sensor.b")
        got, events = Recorder(), Recorder()
        h.on_state(got, "sensor.a")
        h.on_event(events, "pe_x")
        address = server.address
        server.stop()
        wait_for(lambda: not h.connected.is_set(), what="the drop")
        home.states_by_id["sensor.a"]["state"] = "2"                    # changes while the engine is cut off
        from pe_core.host_server import HostServer
        again = HostServer(home, "s3cret", address=address)
        home.server = again
        again.start()
        try:
            wait_for(h.connected.is_set, 10, "the reconnection")
            assert got.wait() == [("sensor.a", "state", "1", "2", {})]
            assert h.get_state("sensor.a") == "2" and h.get_state("sensor.b") == "1"
            wait_for(lambda: "pe_x" in again.wants()[1], what="the event to be asked for again")
            again.event_fired("pe_x", {"n": 1})
            assert events.wait() == [("pe_x", {"n": 1}, {})]
        finally:
            again.stop()


def test_a_new_engine_takes_over_from_the_old_one():
    home = FakeHome()
    with connected(home) as (_, server, first):
        second = SocketHost(server.address, "s3cret", engine_version="9.9.10", retry=(0.05,))
        second.start(timeout=5)
        try:
            wait_for(lambda: server.engine and server.engine["engine"] == "9.9.10", what="the new engine")
            assert second.call_service("a/b") == {"ok": True}
        finally:
            second.stop()


def test_the_integration_can_ask_the_engine_to_stop():
    with connected() as (_, server, h):
        assert not h.stop_requested.is_set()
        server.ask_to_stop()
        assert h.stop_requested.wait(5)


def test_an_integration_that_starts_refusing_the_engine_after_an_update_stops_it_reconnecting():
    home = FakeHome()
    with connected(home) as (_, server, h):
        address = server.address
        server.stop()
        wait_for(lambda: not h.connected.is_set(), what="the drop")
        from pe_core.host_server import HostServer
        newer = HostServer(home, "s3cret", address=address, proto=protocol.VERSION + 1)
        newer.start()
        try:
            assert h.stop_requested.wait(10)
        finally:
            newer.stop()


def test_the_host_logs_with_the_level_and_message_it_is_given(caplog):
    with connected() as (_, _, h):
        with caplog.at_level(logging.INFO, logger="powerengine"):
            h.log("plain")
            h.log("with %s", "args", level="WARNING")
    assert [(r.levelname, r.getMessage()) for r in caplog.records if r.name == "powerengine"] == [
        ("INFO", "plain"), ("WARNING", "with args")]


def test_an_event_arrives_with_who_caused_it_under_metadata():
    with connected() as (home, server, h):
        got = Recorder()
        h.on_event(got, "pe_config_save")
        server.event_fired("pe_config_save", {"a": 1}, {"user_id": "u1", "id": "c1"})
        assert got.wait() == [("pe_config_save", {"a": 1, "metadata": {"context": {"user_id": "u1", "id": "c1"}}}, {})]


def test_the_server_tells_the_integration_what_the_engine_wants_to_hear_about():
    from pe_core.host_server import HostServer
    home, watched, listened = FakeHome(), [], []
    server = HostServer(home, "s", on_watch=watched.append, on_listen=listened.append)
    home.server = server
    address = server.start()
    host = SocketHost(address, "s")
    try:
        host.start(timeout=5)
        host.get_state("sensor.a")
        host.on_event(lambda *a: None, "pe_x")
        assert watched == ["sensor.a"] and listened == ["pe_x"]
    finally:
        host.stop()
        server.stop()


def test_an_error_that_cannot_be_turned_into_text_still_gets_an_answer():
    class Awkward(Exception):
        def __str__(self):
            raise RuntimeError("only on the event loop")

    class Home(FakeHome):
        def call(self, service, data):
            raise Awkward("service not found")
    with connected(Home()) as (_, _, h):
        with pytest.raises(HostError, match="Awkward"):
            h.call_service("a/b")
        assert h.get_timezone() == "Europe/London"                   # and the connection is still good


def test_native_entities_are_defined_on_the_integration_and_defined_again_after_a_reconnection():
    from pe_core.host_server import HostServer
    home = FakeHome()
    with connected(home) as (_, server, h):
        native = h.native_entities
        native.define("sensor", "state_x", "X", {"icon": "mdi:a"})
        native.online(True)
        assert home.defined == [("sensor", "state_x", "X", {"icon": "mdi:a"})] and home.online == [True]
        address = server.address
        server.stop()
        wait_for(lambda: not h.connected.is_set(), what="the drop")
        again = HostServer(home, "s3cret", address=address)
        home.server = again
        again.start()
        try:
            wait_for(lambda: len(home.defined) == 2 and home.online == [True, True], 10, "the definitions again")
            native.retire("sensor.pe_state_x")
            assert home.retired == ["sensor.pe_state_x"] and "state_x" not in native.defined
        finally:
            again.stop()


def test_the_failsafe_is_handed_to_the_integration_and_handed_again_after_a_reconnection():
    from pe_core.host_server import HostServer
    home = FakeHome()
    calls = [{"service": "number/set_value", "data": {"entity_id": "number.a", "value": 0}}, {"wait_s": 5}]
    with connected(home) as (_, server, h):
        h.native_entities.failsafe(calls, True)
        assert home.failsafes == [(calls, True)] and h.native_entities.failsafe_sent == (calls, True)
        address = server.address
        server.stop()
        wait_for(lambda: not h.connected.is_set(), what="the drop")
        again = HostServer(home, "s3cret", address=address)
        home.server = again
        again.start()
        try:
            wait_for(lambda: len(home.failsafes) == 2, 10, "the failsafe again")
            assert home.failsafes[1] == (calls, True)
        finally:
            again.stop()


def test_an_integration_that_does_not_know_the_failsafe_says_so():
    class Old(FakeHome):
        def set_failsafe(self, calls, armed):
            raise AttributeError("no such thing")

    with connected(Old()) as (_, _, h):
        with pytest.raises(HostError):
            h.native_entities.failsafe([], False)
        assert h.native_entities.failsafe_sent is None
