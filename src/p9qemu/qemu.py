"""Readable QEMU command construction and host-shell rendering."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shlex

from p9qemu.constants import DEFAULT_MAC_ADDRESS
from p9qemu.errors import P9QemuError
from p9qemu.host import Acceleration


@dataclass(frozen=True)
class PortForward:
    host_port: int
    guest_port: int
    protocol: str = "tcp"
    host_address: str = "127.0.0.1"

    def qemu_value(self) -> str:
        return (
            f"hostfwd={self.protocol}:{self.host_address}:{self.host_port}"
            f"-:{self.guest_port}"
        )


DEFAULT_PORT_FORWARDS = (
    PortForward(17019, 17019),
    PortForward(17564, 564),
    PortForward(17010, 17010),
    PortForward(17567, 567),
    PortForward(17020, 17020),
    PortForward(17021, 17021),
    PortForward(17022, 17022),
)


def _drive_path(path: Path, label: str) -> str:
    value = str(path)
    if "," in value:
        raise P9QemuError(
            f"{label} path contains a comma, which version 1 cannot safely "
            f"represent in a QEMU -drive option: {value}"
        )
    if "\n" in value or "\r" in value or "\x00" in value:
        raise P9QemuError(f"{label} path contains unsupported characters: {value!r}")
    return value


def _base_command(
    executable: str,
    *,
    memory_mib: int,
    acceleration: Acceleration,
    mac_address: str,
) -> list[str]:
    if memory_mib <= 0:
        raise P9QemuError("memory must be a positive number of MiB")
    return [
        executable,
        "-m",
        str(memory_mib),
        *acceleration.arguments,
        "-net",
        f"nic,model=virtio,macaddr={mac_address}",
    ]


def _disk_arguments(disk: Path) -> list[str]:
    value = _drive_path(disk, "disk")
    return [
        "-device",
        "virtio-scsi-pci,id=scsi",
        "-drive",
        f"if=none,id=vd0,file={value},format=qcow2",
        "-device",
        "scsi-hd,drive=vd0",
    ]


def build_install_command(
    executable: str,
    *,
    disk: Path,
    iso: Path,
    memory_mib: int,
    acceleration: Acceleration,
    mac_address: str = DEFAULT_MAC_ADDRESS,
) -> list[str]:
    iso_value = _drive_path(iso, "ISO")
    return [
        *_base_command(
            executable,
            memory_mib=memory_mib,
            acceleration=acceleration,
            mac_address=mac_address,
        ),
        "-net",
        "user",
        *_disk_arguments(disk),
        "-drive",
        f"if=none,id=vd1,file={iso_value},format=raw",
        "-device",
        "scsi-cd,drive=vd1,bootindex=0",
    ]


def build_start_command(
    executable: str,
    *,
    disk: Path,
    memory_mib: int,
    acceleration: Acceleration,
    forwards: tuple[PortForward, ...] = DEFAULT_PORT_FORWARDS,
    mac_address: str = DEFAULT_MAC_ADDRESS,
) -> list[str]:
    user_network = "user"
    if forwards:
        user_network += "," + ",".join(item.qemu_value() for item in forwards)
    return [
        *_base_command(
            executable,
            memory_mib=memory_mib,
            acceleration=acceleration,
            mac_address=mac_address,
        ),
        *_disk_arguments(disk),
        "-net",
        user_network,
    ]


_POWERSHELL_SAFE = re.compile(r"^[A-Za-z0-9_@%+=:,./\\-]+$")
_COMPACT_LEADING_OPTIONS = frozenset({"-m", "-cpu", "-smp", "-accel", "-display"})


def _quote_powershell(argument: str) -> str:
    if argument and _POWERSHELL_SAFE.fullmatch(argument):
        return argument
    return "'" + argument.replace("'", "''") + "'"


def _join_powershell(arguments: list[str]) -> str:
    return " ".join(_quote_powershell(argument) for argument in arguments)


def _group_options(arguments: list[str]) -> list[list[str]]:
    groups: list[list[str]] = []
    for argument in arguments:
        if argument.startswith("-") and argument != "-":
            groups.append([argument])
        elif groups:
            groups[-1].append(argument)
        else:
            groups.append([argument])
    return groups


def render_command(command: list[str], *, system: str) -> str:
    if not command:
        return ""

    groups = _group_options(command[1:])
    compact: list[str] = []
    while groups and groups[0][0] in _COMPACT_LEADING_OPTIONS:
        compact.extend(groups.pop(0))

    if system == "Windows":
        lines = [f"& {_quote_powershell(command[0])}"]
        quote_group = _join_powershell
        continuation = "`"
    else:
        lines = [shlex.quote(command[0])]
        quote_group = shlex.join
        continuation = "\\"

    if compact:
        lines.append(quote_group(compact))
    lines.extend(quote_group(group) for group in groups)
    return f" {continuation}\n    ".join(lines)
