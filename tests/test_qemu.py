from pathlib import Path

import pytest

from p9qemu.errors import P9QemuError
from p9qemu.host import Acceleration
from p9qemu.qemu import (
    DEFAULT_PORT_FORWARDS,
    build_install_command,
    build_start_command,
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


def test_posix_rendering_is_copyable() -> None:
    rendered = render_command(
        ["qemu-system-x86_64", "-drive", "file=/tmp/a disk.qcow2"],
        system="Linux",
    )
    assert rendered == "qemu-system-x86_64 -drive 'file=/tmp/a disk.qcow2'"


def test_powershell_rendering_is_copyable() -> None:
    rendered = render_command(
        [r"C:\Program Files\qemu\qemu-system-x86_64.exe", "", "it's fine"],
        system="Windows",
    )
    assert rendered == (
        "'C:\\Program Files\\qemu\\qemu-system-x86_64.exe' '' 'it''s fine'"
    )


def test_comma_in_drive_path_is_rejected() -> None:
    with pytest.raises(P9QemuError, match="contains a comma"):
        build_start_command(
            "qemu-system-x86_64",
            disk=Path("bad,name.qcow2"),
            memory_mib=2048,
            acceleration=TCG,
        )
