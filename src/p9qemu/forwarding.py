"""Host-forward address validation and listener-conflict preflight."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack
from ipaddress import AddressValueError, IPv4Address
import socket

from p9qemu.errors import P9QemuError
from p9qemu.qemu import PortForward


SocketFactory = Callable[[int, int], socket.socket]


def loopback_ipv4_address(value: str) -> str:
    """Validate one canonical IPv4 loopback literal for a host listener."""

    try:
        address = IPv4Address(value)
    except AddressValueError as error:
        raise P9QemuError(
            "host-forward address must be a canonical IPv4 loopback literal "
            f"in 127.0.0.0/8: {value!r}"
        ) from error
    if not address.is_loopback or str(address) != value:
        raise P9QemuError(
            "host-forward address must be a canonical IPv4 loopback literal "
            f"in 127.0.0.0/8: {value!r}"
        )
    return value


def require_port_forwards_available(
    forwards: tuple[PortForward, ...],
    *,
    socket_factory: SocketFactory = socket.socket,
) -> None:
    """Hold every TCP endpoint together, then release them before QEMU starts."""

    for forward in forwards:
        if forward.protocol != "tcp":
            raise P9QemuError(f"unsupported host-forward protocol: {forward.protocol}")

    with ExitStack() as listeners:
        for forward in forwards:
            endpoint = (forward.host_address, forward.host_port)
            try:
                candidate = socket_factory(socket.AF_INET, socket.SOCK_STREAM)
                listeners.callback(candidate.close)
                exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
                if exclusive is not None:
                    candidate.setsockopt(socket.SOL_SOCKET, exclusive, 1)
                candidate.bind(endpoint)
                candidate.listen(1)
            except OSError as error:
                raise P9QemuError(
                    "TCP host-forward endpoint is unavailable: "
                    f"{forward.host_address}:{forward.host_port}: {error}"
                ) from error
