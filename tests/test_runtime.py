from __future__ import annotations

from pathlib import Path
import tomllib

import pytest

from p9qemu.errors import P9QemuError
from p9qemu.runtime import (
    GRAPHICAL_SERIAL_PROFILE_V1,
    load_runtime_profile,
    parse_runtime_profile,
)


ROOT = Path(__file__).parents[1]
RUNTIME_PROFILE = (
    ROOT / "images" / "9front-11554-amd64-hjfs-gmt-reference-001" / "runtime.toml"
)


def runtime_document() -> dict:
    with RUNTIME_PROFILE.open("rb") as stream:
        return tomllib.load(stream)


def test_graphical_serial_profile_is_exact_and_retains_console() -> None:
    profile = load_runtime_profile(RUNTIME_PROFILE)
    assert profile.profile_id == GRAPHICAL_SERIAL_PROFILE_V1
    assert profile.source_values == (
        "mouseport=ask",
        "monitor=ask",
        "vgasize=text",
        "console=0",
    )
    assert profile.target_values == (
        "mouseport=ps2",
        "monitor=vesa",
        "vgasize=1024x768x16",
        "console=0",
    )


def test_runtime_profile_rejects_unknown_keys() -> None:
    document = runtime_document()
    document["plan9_ini"]["target"]["consol"] = "0"
    with pytest.raises(P9QemuError, match="unknown runtime profile key.*consol"):
        parse_runtime_profile(document)


def test_runtime_profile_rejects_unqualified_variation() -> None:
    document = runtime_document()
    document["plan9_ini"]["target"]["vgasize"] = "800x600x16"
    with pytest.raises(P9QemuError, match="unsupported runtime boot profile"):
        parse_runtime_profile(document)
