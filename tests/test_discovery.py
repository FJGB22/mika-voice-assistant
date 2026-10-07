import asyncio
import socket

import pytest
from zeroconf import NonUniqueNameException

from mika import discovery

LAN_IP = "192.168.1.20"


# --- local_ip: which address to announce, tested without a network ---


class FakeSocket:
    """Stands in for socket.socket: records how it was used."""

    def __init__(self, family, kind, fail=False):
        self.family = family
        self.kind = kind
        self.fail = fail
        self.connected_to = None
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True

    def connect(self, address):
        if self.fail:
            raise OSError("Network is unreachable")
        self.connected_to = address

    def getsockname(self):
        return (LAN_IP, 54321)


@pytest.fixture
def fake_socket(monkeypatch):
    def install(fail=False):
        made = []

        def factory(family, kind):
            sock = FakeSocket(family, kind, fail)
            made.append(sock)
            return sock

        monkeypatch.setattr(discovery.socket, "socket", factory)
        return made

    return install


def test_local_ip_is_the_address_of_the_outgoing_interface(fake_socket):
    made = fake_socket()

    assert discovery.local_ip() == LAN_IP

    [sock] = made
    assert sock.family == socket.AF_INET
    # UDP: connect() only picks a route. TCP would really try to reach the address.
    assert sock.kind == socket.SOCK_DGRAM
    assert sock.connected_to == discovery.ROUTE_PROBE
    assert sock.closed


def test_local_ip_without_network_is_none(fake_socket):
    made = fake_socket(fail=True)

    assert discovery.local_ip() is None
    assert made[0].closed


# --- make_service_info: what is announced, a real ServiceInfo, no network ---


def test_service_info_points_mika_local_at_the_server():
    info = discovery.make_service_info(LAN_IP, 8765)

    assert info.type == "_mika._tcp.local."
    assert info.name == "Mika._mika._tcp.local."
    assert info.server == "mika.local."
    assert info.parsed_addresses() == [LAN_IP]
    assert info.port == 8765


def test_service_info_uses_the_given_port():
    assert discovery.make_service_info(LAN_IP, 9000).port == 9000


# --- announce: the order of register and cleanup, with a fake zeroconf ---


class Sent:
    """Stands in for the announcement task zeroconf returns: notes when it is awaited."""

    def __init__(self, events, label):
        self.events = events
        self.label = label

    def __await__(self):
        self.events.append(self.label)
        yield from asyncio.sleep(0).__await__()


class FakeZeroconf:
    def __init__(self, events, interfaces, conflict=False):
        self.events = events
        self.interfaces = interfaces
        self.conflict = conflict
        self.info = None

    async def async_register_service(self, info):
        self.events.append("register")
        if self.conflict:
            raise NonUniqueNameException
        self.info = info
        return Sent(self.events, "announced")

    async def async_unregister_service(self, info):
        self.events.append("unregister")
        return Sent(self.events, "goodbye sent")

    async def async_close(self):
        self.events.append("close")

    # Also usable as `async with AsyncZeroconf(...) as zeroconf:`, which closes on exit.
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.async_close()


@pytest.fixture
def fake_zeroconf(monkeypatch):
    def install(ip=LAN_IP, conflict=False):
        events = []
        made = []

        def factory(interfaces):
            zeroconf = FakeZeroconf(events, interfaces, conflict)
            made.append(zeroconf)
            return zeroconf

        monkeypatch.setattr(discovery, "local_ip", lambda: ip)
        monkeypatch.setattr(discovery, "AsyncZeroconf", factory)
        return events, made

    return install


def run_server(events, crash=False):
    async def scenario():
        async with discovery.announce(8765):
            events.append("serving")
            if crash:
                raise RuntimeError("server crashed")

    asyncio.run(scenario())


def test_announce_registers_then_says_goodbye(fake_zeroconf):
    events, made = fake_zeroconf()

    run_server(events)

    # Each step is awaited before the next: goodbye packets must be out before close.
    assert events == ["register", "announced", "serving", "unregister", "goodbye sent", "close"]
    [zeroconf] = made
    assert zeroconf.interfaces == [LAN_IP]  # only the interface that faces the network
    assert zeroconf.info.port == 8765


def test_announce_says_goodbye_even_when_the_server_crashes(fake_zeroconf):
    events, _ = fake_zeroconf()

    with pytest.raises(RuntimeError):
        run_server(events, crash=True)

    assert events[-3:] == ["unregister", "goodbye sent", "close"]


def test_without_network_the_server_still_runs(fake_zeroconf, capsys):
    events, made = fake_zeroconf(ip=None)

    run_server(events)

    assert events == ["serving"]
    assert made == []  # nothing to announce, so no zeroconf at all
    assert "mDNS" in capsys.readouterr().out


def test_name_taken_by_another_machine_keeps_the_server_running(fake_zeroconf, capsys):
    events, _ = fake_zeroconf(conflict=True)

    run_server(events)

    # Nothing was registered, so nothing to unregister. Closed exactly once, and
    # before serving: an unused zeroconf must not stay open while the server runs.
    assert events == ["register", "close", "serving"]
    assert "mDNS" in capsys.readouterr().out


def test_crash_after_name_conflict_is_not_blamed_on_the_conflict(fake_zeroconf):
    events, _ = fake_zeroconf(conflict=True)

    with pytest.raises(RuntimeError) as crash:
        run_server(events, crash=True)

    # Serving inside the except block would chain the crash to NonUniqueNameException,
    # and the traceback would point at the wrong cause.
    assert crash.value.__context__ is None
