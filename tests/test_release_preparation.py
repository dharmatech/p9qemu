from __future__ import annotations

from pathlib import Path

import pytest

from p9qemu.answers import load_answers
from p9qemu.errors import P9QemuError
from p9qemu.release_preparation import drive_release_preparation
from p9qemu.runtime import load_runtime_profile


ROOT = Path(__file__).parents[1]
REFERENCE = ROOT / "images" / "9front-11554-amd64-hjfs-gmt-reference-001"


class FakeTransport:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.commands: list[str] = []
        self.waited: list[str] = []
        before = (
            "bootfile=9pc64\nbootargs=local!/dev/sd00/fs -m 147\n"
            "mouseport=ask\nmonitor=ask\nvgasize=text\nconsole=0\n"
        )
        after = (
            before.replace("mouseport=ask", "mouseport=ps2")
            .replace("monitor=ask", "monitor=vesa")
            .replace("vgasize=text", "vgasize=1024x768x16")
        )
        self.outputs = {
            "9fs 9fat": "",
            "cat /n/9fat/plan9.ini": [before, after],
            "cat /tmp/p9qemu-plan9.ini": after,
            "cp /tmp/p9qemu-plan9.ini /n/9fat/plan9.ini": "",
            "rm /tmp/p9qemu-plan9.ini": "",
        }

    def wait(self, state: str, _pattern: str, _timeout_seconds: int) -> None:
        self.waited.append(state)

    def send_line(self, value: str) -> None:
        self.lines.append(value)

    def command(
        self, state: str, value: str, _prompt_pattern: str, _timeout_seconds: int
    ) -> str:
        self.commands.append(value)
        if state == "guest.rewrite":
            assert "mouseport=ps2" in value
            assert "monitor=vesa" in value
            assert "vgasize=1024x768x16" in value
            assert "console" not in value
            return ""
        result = self.outputs[value]
        if isinstance(result, list):
            return result.pop(0)
        return result


def inputs():
    return (
        load_answers(REFERENCE / "answers.toml"),
        load_runtime_profile(REFERENCE / "runtime.toml"),
    )


def test_release_preparation_changes_only_qualified_runtime_values() -> None:
    answers, profile = inputs()
    transport = FakeTransport()
    result = drive_release_preparation(transport, answers, profile)
    assert "mouseport=ask" in result.before
    assert "mouseport=ps2" in result.after
    assert "console=0" in result.before and "console=0" in result.after
    assert transport.lines == ["", "glenda", "fshalt"]
    assert transport.waited[-1] == "shutdown.fshalt"


def test_release_preparation_rejects_duplicate_setting_before_write() -> None:
    answers, profile = inputs()
    transport = FakeTransport()
    transport.outputs["cat /n/9fat/plan9.ini"][0] += "vgasize=text\n"
    with pytest.raises(P9QemuError, match="duplicates=.*vgasize"):
        drive_release_preparation(transport, answers, profile)
    assert not any(command.startswith("sed ") for command in transport.commands)
    assert "fshalt" not in transport.lines


def test_release_preparation_rejects_unexpected_source_value() -> None:
    answers, profile = inputs()
    transport = FakeTransport()
    transport.outputs["cat /n/9fat/plan9.ini"][0] = transport.outputs[
        "cat /n/9fat/plan9.ini"
    ][0].replace("monitor=ask", "monitor=xga")
    with pytest.raises(P9QemuError, match="do not match the qualified profile"):
        drive_release_preparation(transport, answers, profile)
