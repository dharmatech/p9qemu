from __future__ import annotations

from pathlib import Path

import pexpect
import pytest

from p9qemu.answers import load_answers
from p9qemu.errors import P9QemuError
from p9qemu.installer import InstallerStep, build_11554_hjfs_profile
from p9qemu.live import drive_installer
from p9qemu.pexpect_transport import PexpectTransport


ROOT = Path(__file__).parents[1]
REFERENCE_ANSWERS = (
    ROOT / "images" / "9front-11554-amd64-hjfs-manual-001" / "answers.toml"
)


def profile():
    return build_11554_hjfs_profile(load_answers(REFERENCE_ANSWERS))


class FakeTransport:
    def __init__(self) -> None:
        self.waited: list[str] = []
        self.raw: list[str] = []
        self.lines: list[str] = []

    def wait(self, step: InstallerStep) -> None:
        self.waited.append(step.state)

    def send_raw(self, value: str) -> None:
        self.raw.append(value)

    def send_line(self, value: str) -> None:
        self.lines.append(value)


def test_smoke_test_stops_before_first_installer_task_response() -> None:
    transport = FakeTransport()
    messages: list[str] = []
    result = drive_installer(
        transport,
        profile(),
        progress=messages.append,
        stop_before="menu.configfs",
    )
    assert result.stopped_before == "menu.configfs"
    assert not result.complete
    assert transport.waited[-1] == "menu.configfs"
    assert "configfs.filesystem" not in transport.waited
    assert transport.raw == [" "]
    assert "configfs" not in transport.lines
    assert messages[-1] == "Observed installer state: menu.configfs"


def test_complete_drive_uses_raw_only_for_9boot_interrupt() -> None:
    transport = FakeTransport()
    result = drive_installer(
        transport,
        profile(),
        progress=lambda _message: None,
    )
    assert result.complete
    assert result.stopped_before is None
    assert transport.raw == [" "]
    assert transport.lines[:2] == ["console=0", "boot"]
    assert transport.lines[-1] == "finish"
    assert "yes" in transport.lines


def test_checkpoints_never_send_terminal_input() -> None:
    transport = FakeTransport()
    result = drive_installer(
        transport,
        profile(),
        progress=lambda _message: None,
    )
    action_states = {action.state for action in result.actions}
    assert "partdisk.target_disk" not in action_states
    assert "partdisk.install_media" not in action_states
    assert "partdisk.layout" not in action_states
    assert "prepdisk.layout" not in action_states


def test_unknown_smoke_stop_state_is_rejected_before_transport_use() -> None:
    transport = FakeTransport()
    with pytest.raises(P9QemuError, match="unknown installer stop state"):
        drive_installer(
            transport,
            profile(),
            progress=lambda _message: None,
            stop_before="not-a-state",
        )
    assert transport.waited == []


class FailingChild:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.before = "last useful output\r\n"

    def expect(self, _pattern: str, *, timeout: int) -> None:
        raise self.error


def test_pexpect_timeout_names_state_and_recent_output() -> None:
    transport = PexpectTransport(FailingChild(pexpect.TIMEOUT("timeout")))
    with pytest.raises(
        P9QemuError, match="timed out after 7s.*'test.state'.*last useful output"
    ):
        transport.wait(
            InstallerStep("test.state", "prompt", "answer", timeout_seconds=7)
        )


def test_pexpect_eof_names_state_and_recent_output() -> None:
    transport = PexpectTransport(FailingChild(pexpect.EOF("eof")))
    with pytest.raises(
        P9QemuError, match="QEMU exited before installer state 'test.state'"
    ):
        transport.wait(InstallerStep("test.state", "prompt", "answer"))
