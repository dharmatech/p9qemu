"""Strict answer-file parsing for experimental automated installations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
import tomllib
from typing import Any

from p9qemu.errors import P9QemuError


PROFILE_ID_11554_HJFS = "9front-11554-amd64"
PROFILE_ID_11554_HJFS_GMT_V1 = "9front-11554-amd64-hjfs-gmt-v1"
ISO_SHA256_11554 = "1dcfbb3ec221307329545a37d1562beeea1f4174f6df80d245e0a222893b3bb6"

_SUPPORTED_TIMEZONES = {
    PROFILE_ID_11554_HJFS: "US_Pacific",
    PROFILE_ID_11554_HJFS_GMT_V1: "GMT",
}


@dataclass(frozen=True)
class InstallAnswers:
    """Resolved semantic answers for the first pinned installer profile."""

    schema: int
    installer_profile: str
    iso_sha256: str
    disk_format: str
    disk_size: str
    disk_target: str
    partition_table: str
    use_entire_disk: bool
    console: str
    vgasize: str
    filesystem: str
    hjfs_partition: str
    hjfs_cache_mib: int
    ream_filesystem: bool
    distribution_device: str
    distribution_path: str
    system_name: str
    user: str
    timezone: str
    network_method: str
    boot_partition: str
    install_plan9_mbr: bool
    mark_plan9_partition_active: bool


_TOP_LEVEL_KEYS = {
    "schema",
    "installer_profile",
    "iso_sha256",
    "disk",
    "boot_console",
    "install",
    "network",
    "boot_disk",
}

_TABLE_KEYS = {
    "disk": {
        "format",
        "size",
        "target",
        "partition_table",
        "use_entire_disk",
    },
    "boot_console": {"console", "vgasize"},
    "install": {
        "filesystem",
        "hjfs_partition",
        "hjfs_cache_mib",
        "ream_filesystem",
        "distribution_device",
        "distribution_path",
        "system_name",
        "user",
        "timezone",
    },
    "network": {"method"},
    "boot_disk": {
        "partition",
        "install_plan9_mbr",
        "mark_plan9_partition_active",
    },
}


def _reject_unknown(table: Mapping[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        names = ", ".join(repr(name) for name in unknown)
        raise P9QemuError(f"unknown answer key in {label}: {names}")


def _table(document: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = document.get(name)
    if not isinstance(value, dict):
        raise P9QemuError(f"answer file requires a [{name}] table")
    _reject_unknown(value, _TABLE_KEYS[name], f"[{name}]")
    return value


def _value(table: Mapping[str, Any], key: str, expected_type: type, label: str):
    if key not in table:
        raise P9QemuError(f"answer file requires {label}")
    value = table[key]
    if type(value) is not expected_type:
        raise P9QemuError(f"{label} must be {expected_type.__name__}")
    return value


def _require_supported(
    profile_id: str, label: str, actual: object, expected: object
) -> None:
    if actual != expected:
        raise P9QemuError(
            f"installer profile {profile_id!r} supports only "
            f"{label}={expected!r}; got {actual!r}"
        )


def parse_answers(document: Mapping[str, Any]) -> InstallAnswers:
    """Validate a decoded TOML document and return fully resolved answers."""

    _reject_unknown(document, _TOP_LEVEL_KEYS, "the top level")
    disk = _table(document, "disk")
    boot_console = _table(document, "boot_console")
    install = _table(document, "install")
    network = _table(document, "network")
    boot_disk = _table(document, "boot_disk")

    answers = InstallAnswers(
        schema=_value(document, "schema", int, "schema"),
        installer_profile=_value(
            document, "installer_profile", str, "installer_profile"
        ),
        iso_sha256=_value(document, "iso_sha256", str, "iso_sha256").lower(),
        disk_format=_value(disk, "format", str, "disk.format"),
        disk_size=_value(disk, "size", str, "disk.size"),
        disk_target=_value(disk, "target", str, "disk.target"),
        partition_table=_value(disk, "partition_table", str, "disk.partition_table"),
        use_entire_disk=_value(disk, "use_entire_disk", bool, "disk.use_entire_disk"),
        console=_value(boot_console, "console", str, "boot_console.console"),
        vgasize=_value(boot_console, "vgasize", str, "boot_console.vgasize"),
        filesystem=_value(install, "filesystem", str, "install.filesystem"),
        hjfs_partition=_value(install, "hjfs_partition", str, "install.hjfs_partition"),
        hjfs_cache_mib=_value(install, "hjfs_cache_mib", int, "install.hjfs_cache_mib"),
        ream_filesystem=_value(
            install, "ream_filesystem", bool, "install.ream_filesystem"
        ),
        distribution_device=_value(
            install,
            "distribution_device",
            str,
            "install.distribution_device",
        ),
        distribution_path=_value(
            install, "distribution_path", str, "install.distribution_path"
        ),
        system_name=_value(install, "system_name", str, "install.system_name"),
        user=_value(install, "user", str, "install.user"),
        timezone=_value(install, "timezone", str, "install.timezone"),
        network_method=_value(network, "method", str, "network.method"),
        boot_partition=_value(boot_disk, "partition", str, "boot_disk.partition"),
        install_plan9_mbr=_value(
            boot_disk,
            "install_plan9_mbr",
            bool,
            "boot_disk.install_plan9_mbr",
        ),
        mark_plan9_partition_active=_value(
            boot_disk,
            "mark_plan9_partition_active",
            bool,
            "boot_disk.mark_plan9_partition_active",
        ),
    )

    expected_timezone = _SUPPORTED_TIMEZONES.get(answers.installer_profile)
    if expected_timezone is None:
        raise P9QemuError(
            f"unsupported installer_profile={answers.installer_profile!r}"
        )

    supported_values = {
        "schema": (answers.schema, 1),
        "iso_sha256": (answers.iso_sha256, ISO_SHA256_11554),
        "disk.format": (answers.disk_format, "qcow2"),
        "disk.size": (answers.disk_size, "30G"),
        "disk.target": (answers.disk_target, "sd00"),
        "disk.partition_table": (answers.partition_table, "mbr"),
        "disk.use_entire_disk": (answers.use_entire_disk, True),
        "boot_console.console": (answers.console, "0"),
        "boot_console.vgasize": (answers.vgasize, "text"),
        "install.filesystem": (answers.filesystem, "hjfs"),
        "install.hjfs_partition": (answers.hjfs_partition, "/dev/sd00/fs"),
        "install.hjfs_cache_mib": (answers.hjfs_cache_mib, 147),
        "install.ream_filesystem": (answers.ream_filesystem, True),
        "install.distribution_device": (
            answers.distribution_device,
            "/dev/sd01/data",
        ),
        "install.distribution_path": (answers.distribution_path, "/"),
        "install.system_name": (answers.system_name, "cirno"),
        "install.user": (answers.user, "glenda"),
        "install.timezone": (answers.timezone, expected_timezone),
        "network.method": (answers.network_method, "automatic"),
        "boot_disk.partition": (answers.boot_partition, "/dev/sd00/9fat"),
        "boot_disk.install_plan9_mbr": (answers.install_plan9_mbr, True),
        "boot_disk.mark_plan9_partition_active": (
            answers.mark_plan9_partition_active,
            True,
        ),
    }
    for label, (actual, expected) in supported_values.items():
        _require_supported(answers.installer_profile, label, actual, expected)
    return answers


def load_answers(path: Path) -> InstallAnswers:
    """Read and strictly validate an experimental automated-install answer file."""

    try:
        with path.open("rb") as stream:
            document = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise P9QemuError(f"could not read answer file {path}: {error}") from error
    return parse_answers(document)
