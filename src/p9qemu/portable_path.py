"""Portable archive and manifest path validation."""

from __future__ import annotations

from pathlib import PurePosixPath

from p9qemu.errors import P9QemuError


_WINDOWS_RESERVED_NAMES = {
    "aux",
    "con",
    "nul",
    "prn",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}
_WINDOWS_INVALID_CHARACTERS = set('<>:"\\|?*')


def portable_relative_path(value: str, label: str) -> PurePosixPath:
    """Return one canonical POSIX relative path safe on every host."""

    if not value or "\\" in value or "\x00" in value:
        raise P9QemuError(f"{label} is not a safe portable path: {value!r}")
    if any(ord(character) < 32 for character in value):
        raise P9QemuError(f"{label} contains a control character")
    path = PurePosixPath(value)
    invalid_component = any(
        part in {"", "."}
        or part.endswith((" ", "."))
        or any(character in _WINDOWS_INVALID_CHARACTERS for character in part)
        or part.split(".", 1)[0].casefold() in _WINDOWS_RESERVED_NAMES
        for part in path.parts
    )
    if (
        path.is_absolute()
        or not path.parts
        or ".." in path.parts
        or invalid_component
        or path.as_posix() != value
    ):
        raise P9QemuError(f"{label} is not a safe portable path: {value!r}")
    return path
