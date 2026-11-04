"""The messages between the engine and the integration (pe_core/protocol.py) and the handshake."""

import socket

import pytest
from host_support import FakeHome, connected

from pe_core import protocol
from pe_core.host_server import HostServer
from pe_core.host_socket import HostUnavailable, SocketHost


def pair():
    a, b = socket.socketpair()
    return protocol.Wire(a), protocol.Wire(b)


def test_a_message_is_one_line_of_json_and_comes_back_whole():
    left, right = pair()
    left.send({"t": "call", "id": 1, "data": {"text": "line one\nline two", "n": [1, 2.5, None, True]}})
    left.send({"t": "stop"})
    assert right.read() == {"t": "call", "id": 1, "data": {"text": "line one\nline two", "n": [1, 2.5, None, True]}}
    assert right.read() == {"t": "stop"}
    left.close()
    assert right.read() is None


def test_something_that_is_not_the_protocol_is_refused():
    left, right = pair()
    left.sock.sendall(b"not json\n")
    with pytest.raises(protocol.ProtocolError):
        right.read()
    left.sock.sendall(b'["a list"]\n')
    with pytest.raises(protocol.ProtocolError):
        right.read()
    left.sock.sendall(b'{"no": "type"}\n')
    with pytest.raises(protocol.ProtocolError):
        right.read()


def test_the_proof_of_the_secret_depends_on_both_the_secret_and_the_nonce():
    assert protocol.check_mac("a", "n1", protocol.mac("a", "n1"))
    assert not protocol.check_mac("b", "n1", protocol.mac("a", "n1"))
    assert not protocol.check_mac("a", "n2", protocol.mac("a", "n1"))
    assert not protocol.check_mac("a", "n1", None)
    assert protocol.new_secret() != protocol.new_secret()


def test_addresses_are_local_only():
    assert protocol.parse_address("unix:/tmp/pe.sock") == ("unix", "/tmp/pe.sock")
    assert protocol.parse_address("tcp:127.0.0.1:5000") == ("tcp", ("127.0.0.1", 5000))
    for bad in ("tcp:192.168.1.5:5000", "tcp:0.0.0.0:80", "http://x", "unix:", ""):
        with pytest.raises(ValueError):
            protocol.parse_address(bad)


def test_an_engine_with_the_secret_is_welcomed_with_the_time_zone():
    with connected() as (home, server, host):
        assert host.connected.is_set() and host.get_timezone() == "Europe/London"
        assert server.engine == {"engine": "9.9.9", "proto": protocol.VERSION}


def test_a_wrong_secret_is_refused_and_says_so():
    home = FakeHome()
    server = HostServer(home, "right")
    address = server.start()
    host = SocketHost(address, "wrong", retry=(0.01,))
    try:
        with pytest.raises(protocol.Refused) as err:
            host.start(timeout=2)
        assert err.value.reason == "secret"
    finally:
        server.stop()


def test_a_different_protocol_version_is_refused_with_the_reason_and_the_version_the_integration_speaks():
    home = FakeHome()
    server = HostServer(home, "s", proto=protocol.VERSION + 1)
    address = server.start()
    host = SocketHost(address, "s", retry=(0.01,))
    try:
        with pytest.raises(protocol.Refused) as err:
            host.start(timeout=2)
        assert err.value.reason == "protocol" and err.value.proto == protocol.VERSION + 1
    finally:
        server.stop()


def test_nothing_listening_means_the_engine_gives_up_after_its_wait():
    host = SocketHost("tcp:127.0.0.1:1", "s", retry=(0.01,))
    with pytest.raises(HostUnavailable):
        host.start(timeout=0.2)


def test_a_request_the_server_does_not_know_is_answered_with_an_error_and_the_connection_carries_on():
    from pe_core.host_socket import HostError
    with connected() as (home, server, host):
        with pytest.raises(HostError, match="unknown request 'from_the_future'"):
            host.request("from_the_future")
        home.put("sensor.a", "1")
        assert host.get_state("sensor.a") == "1"


def test_every_request_the_server_answers_is_in_the_catalogue():
    import inspect

    from pe_core import host_server
    source = inspect.getsource(host_server.HostServer._answer)
    for kind in protocol.REQUESTS:
        assert f'kind == "{kind}"' in source, kind
        assert kind in protocol.__doc__
