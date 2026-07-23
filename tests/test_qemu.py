from pathlib import Path

import pytest

from p9qemu.errors import P9QemuError
from p9qemu.host import Acceleration
from p9qemu.qemu import (
    DEFAULT_PORT_FORWARDS,
    PortForward,
    build_automated_install_command,
    build_automated_validation_command,
    build_install_command,
    build_start_command,
    port_forwards_for_host_address,
    render_command,
)


TCG = Acceleration("TCG software emulation", ("-accel", "tcg"))
KVM = Acceleration("KVM", ("-cpu", "host", "-accel", "kvm"))


def test_install_command_matches_plan9_storage_and_network_profile() -> None:
    disk = Path("vms") / "9front.qcow2.img"
    iso = Path("cache") / "9front.iso"
    command = build_install_command(
        "qemu-system-x86_64",
        disk=disk,
        iso=iso,
        memory_mib=1024,
        acceleration=TCG,
    )
    assert command == [
        "qemu-system-x86_64",
        "-m",
        "1024",
        "-accel",
        "tcg",
        "-net",
        "nic,model=virtio,macaddr=00:20:91:37:33:77",
        "-net",
        "user",
        "-device",
        "virtio-scsi-pci,id=scsi",
        "-drive",
        f"if=none,id=vd0,file={disk},format=qcow2",
        "-device",
        "scsi-hd,drive=vd0",
        "-drive",
        f"if=none,id=vd1,file={iso},format=raw",
        "-device",
        "scsi-cd,drive=vd1,bootindex=0",
    ]


def test_start_command_includes_kvm_and_all_known_forwards() -> None:
    command = build_start_command(
        "qemu-system-x86_64",
        disk=Path("9front.qcow2.img"),
        memory_mib=2048,
        acceleration=KVM,
    )
    assert command[:7] == [
        "qemu-system-x86_64",
        "-m",
        "2048",
        "-cpu",
        "host",
        "-accel",
        "kvm",
    ]
    network = command[-1]
    for forward in DEFAULT_PORT_FORWARDS:
        assert forward.qemu_value() in network


def test_host_forward_address_rewrites_only_listener_addresses() -> None:
    rewritten = port_forwards_for_host_address("127.0.0.20")

    assert rewritten == tuple(
        PortForward(
            forward.host_port,
            forward.guest_port,
            protocol=forward.protocol,
            host_address="127.0.0.20",
        )
        for forward in DEFAULT_PORT_FORWARDS
    )
    assert all(forward.host_address == "127.0.0.1" for forward in DEFAULT_PORT_FORWARDS)


def test_explicit_default_host_forward_address_keeps_command_unchanged() -> None:
    arguments = {
        "executable": "qemu-system-x86_64",
        "disk": Path("9front.qcow2.img"),
        "memory_mib": 2048,
        "acceleration": TCG,
    }

    assert build_start_command(**arguments) == build_start_command(
        **arguments,
        forwards=port_forwards_for_host_address("127.0.0.1"),
    )


def test_automated_install_has_dedicated_logged_serial_without_monitor() -> None:
    console_log = Path("run") / "console.raw.log"
    command = build_automated_install_command(
        "qemu-system-x86_64",
        disk=Path("target.qcow2"),
        iso=Path("9front.iso"),
        console_log=console_log,
        memory_mib=1024,
        acceleration=KVM,
    )
    assert command[-8:] == [
        "-nographic",
        "-monitor",
        "none",
        "-chardev",
        f"stdio,id=serial0,logfile={console_log},logappend=off",
        "-serial",
        "chardev:serial0",
        "-no-reboot",
    ]
    assert "-display" not in command
    assert "mon:stdio" not in command


def test_comma_in_console_log_path_is_rejected() -> None:
    with pytest.raises(P9QemuError, match="console log path contains a comma"):
        build_automated_install_command(
            "qemu-system-x86_64",
            disk=Path("target.qcow2"),
            iso=Path("9front.iso"),
            console_log=Path("bad,log.txt"),
            memory_mib=1024,
            acceleration=KVM,
        )


def test_automated_validation_boots_only_the_overlay_on_logged_serial() -> None:
    overlay = Path("run") / "validation-overlay.qcow2"
    console_log = Path("run") / "boot.raw.log"
    command = build_automated_validation_command(
        "qemu-system-x86_64",
        overlay=overlay,
        console_log=console_log,
        memory_mib=2048,
        acceleration=KVM,
    )
    assert f"if=none,id=vd0,file={overlay},format=qcow2" in command
    assert not any("vd1" in argument for argument in command)
    assert command[-8:] == [
        "-nographic",
        "-monitor",
        "none",
        "-chardev",
        f"stdio,id=serial0,logfile={console_log},logappend=off",
        "-serial",
        "chardev:serial0",
        "-no-reboot",
    ]


def test_automated_validation_can_forward_only_drawterm_loopback_ports() -> None:
    command = build_automated_validation_command(
        "qemu-system-x86_64",
        overlay=Path("run") / "validation-overlay.qcow2",
        console_log=Path("run") / "boot.raw.log",
        memory_mib=2048,
        acceleration=KVM,
        forwards=(PortForward(17019, 17019), PortForward(17567, 567)),
    )
    network = command[command.index("-net", 8) + 1]
    assert network == (
        "user,hostfwd=tcp:127.0.0.1:17019-:17019,hostfwd=tcp:127.0.0.1:17567-:567"
    )
    assert "-nographic" in command
    assert "-display" not in command


def test_posix_rendering_is_copyable() -> None:
    rendered = render_command(
        [
            "qemu-system-x86_64",
            "-m",
            "1024",
            "-cpu",
            "host",
            "-accel",
            "kvm",
            "-net",
            "user",
            "-drive",
            "file=/tmp/a disk.qcow2",
        ],
        system="Linux",
    )
    assert rendered == (
        "qemu-system-x86_64 \\\n"
        "    -m 1024 -cpu host -accel kvm \\\n"
        "    -net user \\\n"
        "    -drive 'file=/tmp/a disk.qcow2'"
    )


def test_powershell_rendering_is_copyable() -> None:
    rendered = render_command(
        [
            r"C:\Program Files\qemu\qemu-system-x86_64.exe",
            "-m",
            "1024",
            "-accel",
            "whpx,kernel-irqchip=off",
            "-display",
            "sdl",
            "-drive",
            r"file=C:\VMs\it's fine.qcow2",
        ],
        system="Windows",
    )
    assert rendered == (
        "& 'C:\\Program Files\\qemu\\qemu-system-x86_64.exe' `\n"
        "    -m 1024 -accel whpx,kernel-irqchip=off -display sdl `\n"
        "    -drive 'file=C:\\VMs\\it''s fine.qcow2'"
    )


def test_comma_in_drive_path_is_rejected() -> None:
    with pytest.raises(P9QemuError, match="contains a comma"):
        build_start_command(
            "qemu-system-x86_64",
            disk=Path("bad,name.qcow2"),
            memory_mib=2048,
            acceleration=TCG,
        )
