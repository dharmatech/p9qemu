from pathlib import Path

import pytest

from p9qemu.drawterm_postinstall import load_drawterm_postinstall_profile
from p9qemu.drawterm_validation import (
    DrawtermAcceptanceResult,
    build_drawterm_command,
    build_drawterm_environment,
    build_guest_acceptance_commands,
    is_drawterm_protocol_readiness_failure,
    validate_drawterm_session_output,
    validate_unattended_boot_transcript,
)
from p9qemu.errors import P9QemuError


PROFILE_PATH = (
    Path(__file__).parents[1]
    / "images"
    / "p9qemu-9front-11554-amd64-hjfs-gmt-drawterm-001"
    / "postinstall.json"
)


def profile():
    return load_drawterm_postinstall_profile(PROFILE_PATH)


def session_output(*, network: bool = True) -> str:
    target = profile().plan9_ini.target_values
    lines = [
        "P9QEMU_DRAWTERM_BEGIN",
        "P9QEMU_USER",
        "glenda",
        "P9QEMU_SYSNAME",
        "cirno",
        "P9QEMU_HOME",
        "/usr/glenda",
        "P9QEMU_TIMEZONE_GMT",
        "P9QEMU_PLAN9_INI",
        *target,
    ]
    if network:
        lines.extend(("P9QEMU_NETWORK", "0: rtt 8599 µs, avg rtt 8599 µs"))
    lines.append("P9QEMU_DRAWTERM_COMPLETE")
    return "\n".join(lines) + "\n"


def test_drawterm_argv_uses_loopback_endpoints_without_password() -> None:
    value = profile()
    guest_command = build_guest_acceptance_commands(value, network_mode="required")[0]
    command = build_drawterm_command(Path("/opt/drawterm"), value, guest_command)
    assert command[:8] == [
        str(Path("/opt/drawterm")),
        "-G",
        "-h",
        "tcp!127.0.0.1!17019",
        "-a",
        "tcp!127.0.0.1!17567",
        "-u",
        "glenda",
    ]
    assert value.nvram.password not in "\n".join(command)
    assert build_drawterm_environment(value, {"HOME": "/tmp"}) == {
        "HOME": "/tmp",
        "PASS": value.nvram.password,
    }


def test_guest_command_can_skip_the_environmental_ping() -> None:
    value = profile()
    commands = build_guest_acceptance_commands(value, network_mode="skip")
    assert all(len(command) < 128 for command in commands)
    assert "ip/ping" not in "\n".join(commands)
    assert "P9QEMU_NETWORK" not in "\n".join(commands)


def test_every_required_guest_command_stays_under_transport_bound() -> None:
    commands = build_guest_acceptance_commands(profile(), network_mode="required")
    assert len(commands) == 7
    assert all(len(command) < 128 for command in commands)
    assert "bind -b '#S' /dev; 9fs 9fat /dev/sd00/9fat" in commands[4]


def test_only_the_qualified_pre_auth_hangup_is_retryable() -> None:
    assert is_drawterm_protocol_readiness_failure(
        "drawterm: cannot read p9any negotiation: hung up"
    )
    assert not is_drawterm_protocol_readiness_failure("no 9fat partition found")


def test_unattended_boot_requires_hjfs_and_init_without_prompts() -> None:
    checks = validate_unattended_boot_transcript(
        "hjfs: fs is /dev/sd00/fs\ninit: starting /bin/rc\n", profile()
    )
    assert [check.name for check in checks] == [
        "unattended-boot",
        "root-filesystem",
        "serial-diagnostics",
    ]


@pytest.mark.parametrize(
    "prompt",
    (
        "bootargs is (tcp, tls, il, local!device)[local!/dev/sd00/fs -m 147]",
        "user[glenda]:",
    ),
)
def test_unattended_boot_rejects_any_interactive_prompt(prompt: str) -> None:
    with pytest.raises(P9QemuError, match="interactive prompts"):
        validate_unattended_boot_transcript(
            f"{prompt}\nhjfs: fs is /dev/sd00/fs\ninit: starting /bin/rc\n",
            profile(),
        )


def test_drawterm_output_proves_identity_settings_and_network() -> None:
    checks = validate_drawterm_session_output(
        session_output(), profile(), network_mode="required"
    )
    assert checks[0].name == "drawterm-authentication"
    assert checks[-1].name == "network-ping"


def test_drawterm_output_rejects_wrong_identity() -> None:
    with pytest.raises(P9QemuError, match="identity mismatch"):
        validate_drawterm_session_output(
            session_output().replace("\nglenda\n", "\neve\n", 1),
            profile(),
            network_mode="required",
        )


def test_drawterm_output_rejects_any_plan9_ini_difference() -> None:
    with pytest.raises(P9QemuError, match="does not exactly match.*service=cpu"):
        validate_drawterm_session_output(
            session_output().replace("service=cpu\n", ""),
            profile(),
            network_mode="required",
        )


def test_acceptance_evidence_must_not_contain_password() -> None:
    value = profile()
    with pytest.raises(P9QemuError, match="demonstration password"):
        validate_drawterm_session_output(
            session_output() + value.nvram.password,
            value,
            network_mode="required",
        )


def test_acceptance_result_records_bounded_session_attempt_counts() -> None:
    result = DrawtermAcceptanceResult(
        checks=(),
        session_attempts=(2, 1, 1, 1, 1, 1, 1),
        session_stdout="",
        session_stderr="",
        shutdown_stdout="",
        shutdown_stderr="",
    )
    assert result.status == "passed"
    assert result.session_attempts[0] == 2
