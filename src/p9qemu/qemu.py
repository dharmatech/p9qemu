"""Readable QEMU command construction and host-shell rendering."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shlex

from p9qemu.constants import DEFAULT_MAC_ADDRESS
from p9qemu.errors import P9QemuError
from p9qemu.host import Acceleration


DEFAULT_HOST_FORWARD_ADDRESS = "127.0.0.1"


@dataclass(frozen=True)
class PortForward:
    host_port: int
    guest_port: int
    protocol: str = "tcp"
    host_address: str = DEFAULT_HOST_FORWARD_ADDRESS

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


def port_forwards_for_host_address(
    host_address: str,
    forwards: tuple[PortForward, ...] = DEFAULT_PORT_FORWARDS,
) -> tuple[PortForward, ...]:
    """Return the same port map bound to one explicit host address."""

    return tuple(
        PortForward(
            forward.host_port,
            forward.guest_port,
            protocol=forward.protocol,
            host_address=host_address,
        )
        for forward in forwards
    )


def _option_path(path: Path, label: str, option: str) -> str:
    value = str(path)
    if "," in value:
        raise P9QemuError(
            f"{label} path contains a comma, which version 1 cannot safely "
            f"represent in a QEMU {option} option: {value}"
        )
    if "\n" in value or "\r" in value or "\x00" in value:
        raise P9QemuError(f"{label} path contains unsupported characters: {value!r}")
    return value


def _drive_path(path: Path, label: str) -> str:
    return _option_path(path, label, "-drive")


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


def build_automated_install_command(
    executable: str,
    *,
    disk: Path,
    iso: Path,
    console_log: Path,
    memory_mib: int,
    acceleration: Acceleration,
    mac_address: str = DEFAULT_MAC_ADDRESS,
) -> list[str]:
    """Build the experimental dedicated-serial automated-install command."""

    log_value = _option_path(console_log, "console log", "-chardev")
    return [
        *build_install_command(
            executable,
            disk=disk,
            iso=iso,
            memory_mib=memory_mib,
            acceleration=acceleration,
            mac_address=mac_address,
        ),
        "-nographic",
        "-monitor",
        "none",
        "-chardev",
        f"stdio,id=serial0,logfile={log_value},logappend=off",
        "-serial",
        "chardev:serial0",
        "-no-reboot",
    ]


def build_automated_validation_command(
    executable: str,
    *,
    overlay: Path,
    console_log: Path,
    memory_mib: int,
    acceleration: Acceleration,
    forwards: tuple[PortForward, ...] = (),
    mac_address: str = DEFAULT_MAC_ADDRESS,
) -> list[str]:
    """Build the dedicated-serial command for disposable-overlay validation."""

    log_value = _option_path(console_log, "console log", "-chardev")
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
        "-net",
        user_network,
        *_disk_arguments(overlay),
        "-nographic",
        "-monitor",
        "none",
        "-chardev",
        f"stdio,id=serial0,logfile={log_value},logappend=off",
        "-serial",
        "chardev:serial0",
        "-no-reboot",
    ]


def _start_serial_arguments(
    *,
    serial_console: bool,
    serial_log: Path | None,
) -> list[str]:
    if not serial_console and serial_log is None:
        return []

    backend = "stdio" if serial_console else "vc"
    chardev = f"{backend},id=serial0"
    if serial_log is not None:
        log_value = _option_path(serial_log, "serial log", "-chardev")
        # Public start reserves a new empty log before launch. Appending lets
        # QEMU use that exclusively created file without ever truncating it.
        chardev += f",logfile={log_value},logappend=on"
    return [
        "-monitor",
        "none",
        "-chardev",
        chardev,
        "-serial",
        "chardev:serial0",
    ]


def build_start_command(
    executable: str,
    *,
    disk: Path,
    memory_mib: int,
    acceleration: Acceleration,
    forwards: tuple[PortForward, ...] = DEFAULT_PORT_FORWARDS,
    mac_address: str = DEFAULT_MAC_ADDRESS,
    serial_console: bool = False,
    serial_log: Path | None = None,
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
        *_start_serial_arguments(
            serial_console=serial_console,
            serial_log=serial_log,
        ),
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
