from pathlib import Path
from types import SimpleNamespace

import pytest

from p9qemu.errors import P9QemuError
from p9qemu.host import (
    HostInfo,
    discover_qemu,
    parse_os_release,
    query_qemu_accelerators,
    resolve_acceleration,
    user_cache_dir,
)


def test_parse_os_release() -> None:
    values = parse_os_release(
        'NAME="Ubuntu"\nID=ubuntu\nVERSION_ID="24.04"\nID_LIKE="debian linux"\n'
    )
    assert values == {
        "NAME": "Ubuntu",
        "ID": "ubuntu",
        "VERSION_ID": "24.04",
        "ID_LIKE": "debian linux",
    }


@pytest.mark.parametrize(
    ("host", "environment", "home", "expected"),
    [
        (
            HostInfo("Linux"),
            {"XDG_CACHE_HOME": "/cache"},
            Path("/home/glenda"),
            Path("/cache/p9qemu"),
        ),
        (
            HostInfo("Linux"),
            {},
            Path("/home/glenda"),
            Path("/home/glenda/.cache/p9qemu"),
        ),
        (
            HostInfo("Linux"),
            {"XDG_CACHE_HOME": ""},
            Path("/home/glenda"),
            Path("/home/glenda/.cache/p9qemu"),
        ),
        (
            HostInfo("Windows"),
            {"LOCALAPPDATA": r"C:\Users\glenda\AppData\Local"},
            Path(r"C:\Users\glenda"),
            Path(r"C:\Users\glenda\AppData\Local") / "p9qemu",
        ),
        (
            HostInfo("Darwin"),
            {},
            Path("/Users/glenda"),
            Path("/Users/glenda/Library/Caches/p9qemu"),
        ),
    ],
)
def test_user_cache_dir(
    host: HostInfo,
    environment: dict[str, str],
    home: Path,
    expected: Path,
) -> None:
    assert user_cache_dir(host, environ=environment, home=home) == expected


def test_discover_qemu_finds_both_programs() -> None:
    locations = {
        "qemu-img": "/usr/bin/qemu-img",
        "qemu-system-x86_64": "/usr/bin/qemu-system-x86_64",
    }
    result = discover_qemu(HostInfo("Linux"), which=locations.get)
    assert result.image == "/usr/bin/qemu-img"
    assert result.system == "/usr/bin/qemu-system-x86_64"


def test_missing_qemu_on_ubuntu_has_actionable_guidance() -> None:
    with pytest.raises(P9QemuError) as caught:
        discover_qemu(
            HostInfo("Linux", distribution_id="ubuntu"),
            which=lambda _name: None,
        )
    message = str(caught.value)
    assert "qemu-img and qemu-system-x86_64 were not found" in message
    assert "sudo apt install qemu-system-x86 qemu-utils" in message
    assert "Then run p9qemu again" in message


def test_missing_qemu_on_windows_links_trusted_instructions() -> None:
    with pytest.raises(P9QemuError, match=r"qemu.org/download/#windows"):
        discover_qemu(HostInfo("Windows"), which=lambda _name: None)


def test_auto_acceleration_uses_kvm_when_available() -> None:
    result = resolve_acceleration("auto", HostInfo("Linux"), kvm_usable=True)
    assert result.name == "KVM"
    assert result.arguments == ("-cpu", "host", "-accel", "kvm")


def test_auto_acceleration_falls_back_portably() -> None:
    result = resolve_acceleration("auto", HostInfo("Windows"), kvm_usable=True)
    assert result.name == "TCG software emulation"
    assert result.arguments == ("-accel", "tcg")


def test_requested_kvm_must_be_usable() -> None:
    with pytest.raises(P9QemuError, match="/dev/kvm"):
        resolve_acceleration("kvm", HostInfo("Linux"), kvm_usable=False)


def test_explicit_tcg_is_portable() -> None:
    result = resolve_acceleration("tcg", HostInfo("Windows"))
    assert result.name == "TCG software emulation"
    assert result.arguments == ("-accel", "tcg")


def test_explicit_whpx_requires_windows_and_advertised_support() -> None:
    result = resolve_acceleration(
        "whpx",
        HostInfo("Windows"),
        available_accelerators={"tcg", "whpx"},
    )
    assert result.name == "WHPX with userspace irqchip, 2 vCPUs, and SDL (no fallback)"
    assert result.arguments == (
        "-smp",
        "2",
        "-accel",
        "whpx,kernel-irqchip=off",
        "-display",
        "sdl",
    )

    with pytest.raises(P9QemuError, match="only on Windows"):
        resolve_acceleration(
            "whpx",
            HostInfo("Linux"),
            available_accelerators={"tcg", "whpx"},
        )
    with pytest.raises(P9QemuError, match="does not advertise WHPX"):
        resolve_acceleration(
            "whpx",
            HostInfo("Windows"),
            available_accelerators={"tcg"},
        )


def test_query_qemu_accelerators_parses_qemu_output() -> None:
    commands: list[list[str]] = []

    def runner(command: list[str], **_kwargs):
        commands.append(command)
        return SimpleNamespace(
            returncode=0,
            stdout=("Accelerators supported in QEMU binary:\ntcg\nwhpx\n"),
            stderr="",
        )

    result = query_qemu_accelerators("qemu-system-x86_64", runner=runner)
    assert result == frozenset({"tcg", "whpx"})
    assert commands == [["qemu-system-x86_64", "-accel", "help"]]


def test_query_qemu_accelerators_reports_query_failure() -> None:
    def runner(_command: list[str], **_kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="query failed")

    with pytest.raises(P9QemuError, match="status 1: query failed"):
        query_qemu_accelerators("qemu-system-x86_64", runner=runner)
