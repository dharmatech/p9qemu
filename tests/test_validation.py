from __future__ import annotations

from pathlib import Path

import pexpect
import pytest

from p9qemu.answers import load_answers
from p9qemu.errors import P9QemuError
from p9qemu.pexpect_validation import PexpectGuestValidationTransport
from p9qemu.validation import (
    GuestValidationError,
    GuestValidationProfile,
    build_guest_validation_profile,
    drive_guest_validation,
)


ROOT = Path(__file__).parents[1]
REFERENCE_ANSWERS = (
    ROOT / "images" / "9front-11554-amd64-hjfs-manual-001" / "answers.toml"
)


def profile() -> GuestValidationProfile:
    return build_guest_validation_profile(load_answers(REFERENCE_ANSWERS))


class FakeTransport:
    def __init__(self, *, ping_output: str = "0: rtt 9000 us") -> None:
        self.waited: list[tuple[str, str, int]] = []
        self.lines: list[str] = []
        self.commands: list[str] = []
        self.outputs = {
            "echo $user": "glenda\n",
            "pwd": "/usr/glenda\n",
            "cat /dev/sysname": "cirno",
            "9fs 9fat": "",
            "cat /n/9fat/plan9.ini": (
                "bootargs=local!/dev/sd00/fs -m 147\n"
                "vgasize=text\nconsole=0\n"
            ),
            "ip/ping -n 1 google.com": ping_output,
        }

    def wait(self, state: str, pattern: str, timeout_seconds: int) -> None:
        self.waited.append((state, pattern, timeout_seconds))

    def send_line(self, value: str) -> None:
        self.lines.append(value)

    def command(
        self,
        state: str,
        value: str,
        prompt_pattern: str,
        timeout_seconds: int,
    ) -> str:
        self.commands.append(value)
        self.waited.append((state, prompt_pattern, timeout_seconds))
        return self.outputs[value]


def test_profile_is_derived_from_resolved_install_answers() -> None:
    validation = profile()
    assert validation.user == "glenda"
    assert validation.home == "/usr/glenda"
    assert validation.system_name == "cirno"
    assert validation.root_partition == "/dev/sd00/fs"
    assert "console=0" in validation.plan9_ini_values


def test_complete_guest_validation_checks_and_halts() -> None:
    transport = FakeTransport()
    messages: list[str] = []
    result = drive_guest_validation(
        transport,
        profile(),
        network_mode="optional",
        progress=messages.append,
    )
    assert result.status == "passed"
    assert transport.lines == ["", "glenda", "fshalt"]
    assert transport.commands[-1] == "ip/ping -n 1 google.com"
    assert transport.waited[-1][0] == "shutdown.fshalt"
    assert "user\\[glenda\\]:" in transport.waited[1][1]
    assert messages[-1] == "Guest completed fshalt."


def test_optional_environmental_failure_does_not_invalidate_image() -> None:
    transport = FakeTransport(ping_output="can't translate address")
    result = drive_guest_validation(
        transport,
        profile(),
        network_mode="optional",
        progress=lambda _message: None,
    )
    assert result.status == "passed-with-environmental-failures"
    network = next(check for check in result.checks if check.name == "network-ping")
    assert network.category == "environmental"
    assert network.status == "failed"
    assert transport.lines[-1] == "fshalt"


def test_required_network_failure_stops_before_shutdown_claim() -> None:
    transport = FakeTransport(ping_output="can't translate address")
    with pytest.raises(
        GuestValidationError, match="guest.network-ping.*'rtt'"
    ) as captured:
        drive_guest_validation(
            transport,
            profile(),
            network_mode="required",
            progress=lambda _message: None,
        )
    assert captured.value.category == "environmental"
    assert "fshalt" not in transport.lines


def test_deterministic_mismatch_fails_closed() -> None:
    transport = FakeTransport()
    transport.outputs["cat /dev/sysname"] = "wrong-name"
    with pytest.raises(P9QemuError, match="guest.sysname.*'cirno'"):
        drive_guest_validation(
            transport,
            profile(),
            network_mode="skip",
            progress=lambda _message: None,
        )
    assert "fshalt" not in transport.lines


class FailingChild:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.before = "last useful guest output\r\n"

    def expect(self, _pattern: str, *, timeout: int) -> None:
        raise self.error


def test_pexpect_validation_timeout_names_state_and_recent_output() -> None:
    transport = PexpectGuestValidationTransport(
        FailingChild(pexpect.TIMEOUT("timeout"))
    )
    with pytest.raises(
        P9QemuError,
        match="timed out after 9s.*'guest.state'.*last useful guest output",
    ):
        transport.wait("guest.state", "prompt", 9)


def test_pexpect_validation_eof_names_state() -> None:
    transport = PexpectGuestValidationTransport(FailingChild(pexpect.EOF("eof")))
    with pytest.raises(
        P9QemuError, match="QEMU exited before guest validation state 'guest.state'"
    ):
        transport.wait("guest.state", "prompt", 9)


def test_pexpect_network_timeout_is_categorized_as_environmental() -> None:
    transport = PexpectGuestValidationTransport(
        FailingChild(pexpect.TIMEOUT("timeout"))
    )
    with pytest.raises(GuestValidationError) as captured:
        transport.wait("guest.network-ping", "prompt", 9)
    assert captured.value.category == "environmental"
    assert captured.value.state == "guest.network-ping"
