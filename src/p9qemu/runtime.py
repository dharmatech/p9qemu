"""Qualified runtime boot profiles for prepared 9front images."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
import tomllib
from typing import Any

from p9qemu.answers import PROFILE_ID_11554_HJFS_GMT_V1
from p9qemu.errors import P9QemuError


GRAPHICAL_SERIAL_PROFILE_V1 = "9front-11554-amd64-graphical-serial-v1"
_SETTING_NAMES = ("mouseport", "monitor", "vgasize", "console")


@dataclass(frozen=True)
class RuntimeBootProfile:
    """Exact pre- and post-preparation values for one qualified image."""

    schema: int
    profile_id: str
    installer_profile: str
    plan9_ini_path: str
    source_mouseport: str
    source_monitor: str
    source_vgasize: str
    source_console: str
    target_mouseport: str
    target_monitor: str
    target_vgasize: str
    target_console: str

    @property
    def source_values(self) -> tuple[str, ...]:
        return tuple(
            f"{name}={getattr(self, f'source_{name}')}" for name in _SETTING_NAMES
        )

    @property
    def target_values(self) -> tuple[str, ...]:
        return tuple(
            f"{name}={getattr(self, f'target_{name}')}" for name in _SETTING_NAMES
        )

    @property
    def setting_names(self) -> tuple[str, ...]:
        return _SETTING_NAMES


def _reject_unknown(table: Mapping[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        names = ", ".join(repr(name) for name in unknown)
        raise P9QemuError(f"unknown runtime profile key in {label}: {names}")


def _value(table: Mapping[str, Any], key: str, expected_type: type, label: str):
    if key not in table:
        raise P9QemuError(f"runtime profile requires {label}")
    value = table[key]
    if type(value) is not expected_type:
        raise P9QemuError(f"{label} must be {expected_type.__name__}")
    return value


def _table(document: Mapping[str, Any], name: str, label: str) -> Mapping[str, Any]:
    value = document.get(name)
    if not isinstance(value, dict):
        raise P9QemuError(f"runtime profile requires {label}")
    return value


def parse_runtime_profile(document: Mapping[str, Any]) -> RuntimeBootProfile:
    """Strictly parse the first qualified graphical-plus-serial profile."""

    _reject_unknown(
        document,
        {"schema", "profile_id", "installer_profile", "plan9_ini"},
        "the top level",
    )
    plan9_ini = _table(document, "plan9_ini", "a [plan9_ini] table")
    _reject_unknown(plan9_ini, {"path", "source", "target"}, "[plan9_ini]")
    source = _table(plan9_ini, "source", "a [plan9_ini.source] table")
    target = _table(plan9_ini, "target", "a [plan9_ini.target] table")
    _reject_unknown(source, set(_SETTING_NAMES), "[plan9_ini.source]")
    _reject_unknown(target, set(_SETTING_NAMES), "[plan9_ini.target]")

    profile = RuntimeBootProfile(
        schema=_value(document, "schema", int, "schema"),
        profile_id=_value(document, "profile_id", str, "profile_id"),
        installer_profile=_value(
            document, "installer_profile", str, "installer_profile"
        ),
        plan9_ini_path=_value(plan9_ini, "path", str, "plan9_ini.path"),
        **{
            f"{state}_{name}": _value(values, name, str, f"plan9_ini.{state}.{name}")
            for state, values in (("source", source), ("target", target))
            for name in _SETTING_NAMES
        },
    )

    expected = RuntimeBootProfile(
        schema=1,
        profile_id=GRAPHICAL_SERIAL_PROFILE_V1,
        installer_profile=PROFILE_ID_11554_HJFS_GMT_V1,
        plan9_ini_path="/n/9fat/plan9.ini",
        source_mouseport="ask",
        source_monitor="ask",
        source_vgasize="text",
        source_console="0",
        target_mouseport="ps2",
        target_monitor="vesa",
        target_vgasize="1024x768x16",
        target_console="0",
    )
    if profile != expected:
        raise P9QemuError(
            f"unsupported runtime boot profile {profile.profile_id!r} or values"
        )
    return profile


def load_runtime_profile(path: Path) -> RuntimeBootProfile:
    """Load and strictly validate a runtime boot profile TOML file."""

    try:
        with path.open("rb") as stream:
            document = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise P9QemuError(f"could not read runtime profile {path}: {error}") from error
    return parse_runtime_profile(document)
