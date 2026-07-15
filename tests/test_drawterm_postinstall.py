from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from p9qemu.drawterm_postinstall import (
    DRAWTERM_PROFILE_V1,
    STAGED_PLAN9_INI,
    drive_drawterm_preparation,
    load_drawterm_postinstall_profile,
    parse_drawterm_postinstall_profile,
)
from p9qemu.errors import P9QemuError


ROOT = Path(__file__).parents[1]
PROFILE_DIRECTORY = ROOT / "images" / "p9qemu-9front-11554-amd64-hjfs-gmt-drawterm-001"
PROFILE_PATH = PROFILE_DIRECTORY / "postinstall.json"


def profile_document() -> dict:
    return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))


def source_plan9_ini() -> str:
    return (
        "bootfile=9pc64\n"
        "bootargs=local!/dev/sd00/fs -m 147\n"
        "mouseport=ps2\n"
        "monitor=vesa\n"
        "vgasize=1024x768x16\n"
        "console=0\n"
    )


class FakeTransport:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.commands: list[tuple[str, str]] = []
        self.waited: list[str] = []
        self.before = source_plan9_ini()
        self.after = self.before + (
            "nobootprompt=local!/dev/sd00/fs -m 147\nnvram=#S/sd00/nvram\nservice=cpu\n"
        )
        self.nvram_available = True
        self.staged_override: str | None = None
        self.echo_commands = False

    def wait(self, state: str, _pattern: str, _timeout_seconds: int) -> None:
        self.waited.append(state)

    def send_line(self, value: str) -> None:
        self.lines.append(value)

    def command(
        self, state: str, value: str, _prompt_pattern: str, _timeout_seconds: int
    ) -> str:
        self.commands.append((state, value))
        if state == "guest.plan9-ini-before":
            return f" {value}\r\n{self.before}" if self.echo_commands else self.before
        if state in ("guest.plan9-ini-staged", "guest.plan9-ini-after"):
            output = self.staged_override or self.after
            return f" {value}\r\n{output}" if self.echo_commands else output
        if state == "guest.nvram-partition":
            return "P9QEMU_NVRAM_READY\n" if self.nvram_available else ""
        if state == "guest.wrkey-status":
            return ""
        return ""


def test_profile_is_exact_and_pins_the_stock_parent() -> None:
    profile = load_drawterm_postinstall_profile(PROFILE_PATH)
    assert profile.profile_id == DRAWTERM_PROFILE_V1
    assert profile.parent.image_id == "p9qemu-9front-11554-amd64-hjfs-gmt-002"
    assert profile.parent.image_sha256 == (
        "1ef80c81a3f2dd09d2f173ff7dfa93d07ecee2ba453fc0f0964190adb6ee44a8"
    )
    assert profile.plan9_ini.additions == (
        "nobootprompt=local!/dev/sd00/fs -m 147",
        "nvram=#S/sd00/nvram",
        "service=cpu",
    )
    assert profile.nvram.credential_class == "public-demo"
    assert profile.drawterm.cpu_host_port == 17019
    assert profile.drawterm.auth_host_port == 17567


def test_profile_rejects_unknown_keys() -> None:
    document = profile_document()
    document["nvram"]["password_hint"] = "demo"
    with pytest.raises(P9QemuError, match="fields differ at nvram.*password_hint"):
        parse_drawterm_postinstall_profile(document)


def test_profile_rejects_unqualified_variation() -> None:
    document = profile_document()
    document["plan9_ini"]["target"]["required"]["service"] = "terminal"
    with pytest.raises(P9QemuError, match="unsupported Drawterm post-install"):
        parse_drawterm_postinstall_profile(document)


def test_build_document_tracks_machine_readable_values() -> None:
    profile = load_drawterm_postinstall_profile(PROFILE_PATH)
    build = (PROFILE_DIRECTORY / "BUILD.md").read_text(encoding="utf-8")
    for value in (
        profile.parent.image_id,
        profile.parent.image_sha256,
        *profile.plan9_ini.source_values,
        *profile.plan9_ini.additions,
        profile.nvram.authid,
        profile.nvram.authdom,
        profile.nvram.password,
        str(profile.drawterm.cpu_host_port),
        str(profile.drawterm.auth_host_port),
    ):
        assert value in build


def test_driver_applies_exact_additions_and_configures_nvram() -> None:
    profile = load_drawterm_postinstall_profile(PROFILE_PATH)
    transport = FakeTransport()
    result = drive_drawterm_preparation(transport, profile)
    assert result.before == transport.before
    assert result.after == transport.after
    assert transport.lines == [
        "",
        "glenda",
        "auth/wrkey",
        "glenda",
        "9front",
        "",
        "p9qemu-demo",
        "p9qemu-demo",
        "",
        "fshalt",
    ]
    stage_commands = [
        value for state, value in transport.commands if state == "guest.stage-plan9-ini"
    ]
    assert len(stage_commands) == 1
    assert stage_commands[0].startswith(f"cp /n/9fat/plan9.ini {STAGED_PLAN9_INI}")
    assert transport.waited[-1] == "shutdown.fshalt"


def test_driver_ignores_serial_command_echo_when_comparing_file_contents() -> None:
    profile = load_drawterm_postinstall_profile(PROFILE_PATH)
    transport = FakeTransport()
    transport.echo_commands = True
    result = drive_drawterm_preparation(transport, profile)
    assert result.before == transport.before
    assert result.after == transport.after


@pytest.mark.parametrize(
    "mutator,match",
    (
        (
            lambda value: value + "vgasize=1024x768x16\n",
            "duplicate controlled settings.*vgasize",
        ),
        (
            lambda value: value.replace("monitor=vesa", "monitor=xga"),
            "source plan9.ini does not match",
        ),
        (
            lambda value: value + "service=cpu\n",
            "expected_absent_but_present=.*service",
        ),
    ),
)
def test_driver_rejects_unexpected_source_before_writing(mutator, match: str) -> None:
    profile = load_drawterm_postinstall_profile(PROFILE_PATH)
    transport = FakeTransport()
    transport.before = mutator(transport.before)
    with pytest.raises(P9QemuError, match=match):
        drive_drawterm_preparation(transport, profile)
    assert not any(state == "guest.stage-plan9-ini" for state, _ in transport.commands)
    assert "auth/wrkey" not in transport.lines


def test_driver_requires_the_pinned_nvram_partition() -> None:
    profile = load_drawterm_postinstall_profile(PROFILE_PATH)
    transport = FakeTransport()
    transport.nvram_available = False
    with pytest.raises(P9QemuError, match="NVRAM partition is not available"):
        drive_drawterm_preparation(transport, profile)
    assert not any(state == "guest.stage-plan9-ini" for state, _ in transport.commands)


def test_driver_requires_source_file_to_end_with_newline() -> None:
    profile = load_drawterm_postinstall_profile(PROFILE_PATH)
    transport = FakeTransport()
    transport.before = transport.before.rstrip("\n")
    with pytest.raises(P9QemuError, match="must end with a newline"):
        drive_drawterm_preparation(transport, profile)
    assert not any(state == "guest.stage-plan9-ini" for state, _ in transport.commands)


def test_driver_rejects_changes_outside_qualified_additions() -> None:
    profile = load_drawterm_postinstall_profile(PROFILE_PATH)
    transport = FakeTransport()
    transport.staged_override = transport.after.replace(
        "bootfile=9pc64", "unrelated=value\nbootfile=9pc64"
    )
    with pytest.raises(P9QemuError, match="changed content outside"):
        drive_drawterm_preparation(transport, profile)
    assert "auth/wrkey" not in transport.lines


def test_parsed_profile_is_immutable_under_document_copy() -> None:
    document = profile_document()
    profile = parse_drawterm_postinstall_profile(deepcopy(document))
    document["nvram"]["password"] = "changed"
    assert profile.nvram.password == "p9qemu-demo"
