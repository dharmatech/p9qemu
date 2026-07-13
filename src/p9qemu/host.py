"""Host detection, cache selection, QEMU discovery, and acceleration."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess

from p9qemu.errors import P9QemuError


@dataclass(frozen=True)
class HostInfo:
    system: str
    distribution_id: str = ""
    distribution_name: str = ""
    version_id: str = ""
    id_like: tuple[str, ...] = ()


@dataclass(frozen=True)
class QemuExecutables:
    system: str
    image: str


@dataclass(frozen=True)
class Acceleration:
    name: str
    arguments: tuple[str, ...]


AcceleratorRunner = Callable[..., subprocess.CompletedProcess[str]]

_ACCELERATOR_NAME = re.compile(r"^[a-z][a-z0-9_-]*$")


def parse_os_release(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value.replace(r"\"", '"').replace(r"\\", "\\")
    return values


def current_host(
    *, system: str | None = None, os_release: Path | None = None
) -> HostInfo:
    system_name = system or platform.system()
    if system_name != "Linux":
        return HostInfo(system=system_name)

    release_path = os_release or Path("/etc/os-release")
    try:
        values = parse_os_release(release_path.read_text(encoding="utf-8"))
    except OSError:
        values = {}
    return HostInfo(
        system=system_name,
        distribution_id=values.get("ID", "").lower(),
        distribution_name=values.get("PRETTY_NAME", values.get("NAME", "")),
        version_id=values.get("VERSION_ID", ""),
        id_like=tuple(values.get("ID_LIKE", "").lower().split()),
    )


def user_cache_dir(
    host: HostInfo,
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    env = os.environ if environ is None else environ
    home_dir = Path.home() if home is None else home
    if host.system == "Windows":
        configured = env.get("LOCALAPPDATA")
        root = Path(configured) if configured else home_dir / "AppData" / "Local"
    elif host.system == "Darwin":
        root = home_dir / "Library" / "Caches"
    else:
        configured = env.get("XDG_CACHE_HOME")
        root = Path(configured) if configured else home_dir / ".cache"
    return root / "p9qemu"


def installation_guidance(host: HostInfo) -> str:
    if host.system == "Linux" and host.distribution_id == "ubuntu":
        return (
            "Install QEMU on Ubuntu with:\n\n"
            "  sudo apt install qemu-system-x86 qemu-utils"
        )
    if host.system == "Windows":
        return (
            "Install QEMU for Windows and ensure its executables are on PATH:\n\n"
            "  https://www.qemu.org/download/#windows"
        )
    if host.system == "Darwin":
        return (
            "Install QEMU for macOS and ensure its executables are on PATH:\n\n"
            "  https://www.qemu.org/download/#macos"
        )
    return (
        "Install QEMU with your operating system's package manager and ensure "
        "its executables are on PATH."
    )


def discover_qemu(
    host: HostInfo,
    *,
    which: Callable[[str], str | None] = shutil.which,
) -> QemuExecutables:
    names = ("qemu-img", "qemu-system-x86_64")
    found = {name: which(name) for name in names}
    missing = [name for name in names if found[name] is None]
    if missing:
        joined = " and ".join(missing)
        verb = "was" if len(missing) == 1 else "were"
        raise P9QemuError(
            f"{joined} {verb} not found.\n\n"
            f"{installation_guidance(host)}\n\n"
            "Then run p9qemu again."
        )
    return QemuExecutables(
        system=found["qemu-system-x86_64"] or "qemu-system-x86_64",
        image=found["qemu-img"] or "qemu-img",
    )


def kvm_is_usable(*, device: Path = Path("/dev/kvm")) -> bool:
    return device.exists() and os.access(device, os.R_OK | os.W_OK)


def query_qemu_accelerators(
    executable: str,
    *,
    runner: AcceleratorRunner = subprocess.run,
) -> frozenset[str]:
    command = [executable, "-accel", "help"]
    try:
        result = runner(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        raise P9QemuError(
            f"could not query QEMU accelerator support: {error}"
        ) from error
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        suffix = f": {detail}" if detail else ""
        raise P9QemuError(
            f"QEMU accelerator query exited with status {result.returncode}{suffix}"
        )

    output = f"{result.stdout}\n{result.stderr}"
    accelerators = frozenset(
        line.strip().lower()
        for line in output.splitlines()
        if _ACCELERATOR_NAME.fullmatch(line.strip().lower())
    )
    if not accelerators:
        raise P9QemuError(
            "QEMU did not report any supported accelerators from -accel help"
        )
    return accelerators


def resolve_acceleration(
    requested: str,
    host: HostInfo,
    *,
    kvm_usable: bool | None = None,
    available_accelerators: Collection[str] | None = None,
) -> Acceleration:
    if requested == "tcg":
        return Acceleration("TCG software emulation", ("-accel", "tcg"))

    if requested == "whpx":
        if host.system != "Windows":
            raise P9QemuError("WHPX acceleration is available only on Windows hosts")
        if available_accelerators is None:
            raise P9QemuError("WHPX support was not queried from QEMU")
        if "whpx" not in available_accelerators:
            raise P9QemuError(
                "WHPX acceleration was requested, but this QEMU build does not "
                "advertise WHPX support"
            )
        return Acceleration(
            "WHPX with userspace irqchip, 2 vCPUs, and SDL (no fallback)",
            (
                "-smp",
                "2",
                "-accel",
                "whpx,kernel-irqchip=off",
                "-display",
                "sdl",
            ),
        )

    usable = kvm_is_usable() if kvm_usable is None else kvm_usable
    if requested == "kvm":
        if host.system != "Linux":
            raise P9QemuError("KVM acceleration is available only on Linux hosts")
        if not usable:
            raise P9QemuError(
                "KVM acceleration was requested, but /dev/kvm is unavailable "
                "or not accessible"
            )
        return Acceleration("KVM", ("-cpu", "host", "-accel", "kvm"))

    if requested != "auto":
        raise P9QemuError(f"unsupported acceleration mode: {requested}")
    if host.system == "Linux" and usable:
        return Acceleration("KVM", ("-cpu", "host", "-accel", "kvm"))
    return Acceleration("TCG software emulation", ("-accel", "tcg"))
