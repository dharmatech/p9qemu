import socket

import pytest

from p9qemu.errors import P9QemuError
from p9qemu.forwarding import (
    loopback_ipv4_address,
    require_port_forwards_available,
)
from p9qemu.qemu import PortForward


@pytest.mark.parametrize(
    "value",
    (
        "127.0.0.1",
        "127.0.0.20",
        "127.255.255.255",
    ),
)
def test_loopback_ipv4_address_accepts_canonical_loopback_literals(value: str) -> None:
    assert loopback_ipv4_address(value) == value


@pytest.mark.parametrize(
    "value",
    (
        "localhost",
        "0.0.0.0",
        "192.0.2.1",
        "::1",
        "127.0.0.020",
        " 127.0.0.20",
    ),
)
def test_loopback_ipv4_address_rejects_unsafe_or_ambiguous_values(
    value: str,
) -> None:
    with pytest.raises(
        P9QemuError,
        match="canonical IPv4 loopback literal in 127.0.0.0/8",
    ):
        loopback_ipv4_address(value)


class FakeSocket:
    def __init__(
        self,
        events: list[tuple[str, object]],
        *,
        fail_endpoint: tuple[str, int] | None,
    ) -> None:
        self.events = events
        self.fail_endpoint = fail_endpoint
        self.endpoint: tuple[str, int] | None = None

    def setsockopt(self, level: int, option: int, value: int) -> None:
        self.events.append(("setsockopt", (level, option, value)))

    def bind(self, endpoint: tuple[str, int]) -> None:
        self.endpoint = endpoint
        self.events.append(("bind", endpoint))
        if endpoint == self.fail_endpoint:
            raise OSError(10048, "address already in use")

    def listen(self, backlog: int) -> None:
        self.events.append(("listen", (self.endpoint, backlog)))

    def close(self) -> None:
        self.events.append(("close", self.endpoint))


def fake_socket_factory(
    events: list[tuple[str, object]],
    *,
    fail_endpoint: tuple[str, int] | None = None,
):
    def create(family: int, kind: int) -> FakeSocket:
        assert family == socket.AF_INET
        assert kind == socket.SOCK_STREAM
        return FakeSocket(events, fail_endpoint=fail_endpoint)

    return create


def test_preflight_holds_every_tcp_endpoint_until_all_are_listening() -> None:
    events: list[tuple[str, object]] = []
    forwards = (
        PortForward(17019, 17019, host_address="127.0.0.20"),
        PortForward(17567, 567, host_address="127.0.0.20"),
    )

    require_port_forwards_available(
        forwards,
        socket_factory=fake_socket_factory(events),
    )

    assert [value for name, value in events if name == "bind"] == [
        ("127.0.0.20", 17019),
        ("127.0.0.20", 17567),
    ]
    last_listen = max(
        index for index, event in enumerate(events) if event[0] == "listen"
    )
    first_close = min(
        index for index, event in enumerate(events) if event[0] == "close"
    )
    assert first_close > last_listen
    assert [value for name, value in events if name == "close"] == [
        ("127.0.0.20", 17567),
        ("127.0.0.20", 17019),
    ]


def test_preflight_reports_exact_conflict_and_closes_every_socket() -> None:
    events: list[tuple[str, object]] = []
    forwards = (
        PortForward(17019, 17019, host_address="127.0.0.20"),
        PortForward(17567, 567, host_address="127.0.0.20"),
    )

    with pytest.raises(
        P9QemuError,
        match=r"TCP host-forward endpoint is unavailable: 127\.0\.0\.20:17567",
    ):
        require_port_forwards_available(
            forwards,
            socket_factory=fake_socket_factory(
                events,
                fail_endpoint=("127.0.0.20", 17567),
            ),
        )

    assert [value for name, value in events if name == "close"] == [
        ("127.0.0.20", 17567),
        ("127.0.0.20", 17019),
    ]


def test_preflight_rejects_unsupported_protocol_without_opening_socket() -> None:
    calls: list[tuple[int, int]] = []

    def create(family: int, kind: int) -> FakeSocket:
        calls.append((family, kind))
        return FakeSocket([], fail_endpoint=None)

    with pytest.raises(P9QemuError, match="unsupported host-forward protocol: udp"):
        require_port_forwards_available(
            (
                PortForward(
                    17019,
                    17019,
                    protocol="udp",
                    host_address="127.0.0.20",
                ),
            ),
            socket_factory=create,
        )

    assert calls == []
