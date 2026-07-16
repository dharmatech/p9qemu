"""Pure acceptance rules for the unattended Drawterm image variant."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from pathlib import Path

from p9qemu.drawterm_postinstall import DrawtermPostinstallProfile
from p9qemu.errors import P9QemuError


NetworkMode = str
CPU_SERVICE_GUEST_PORT = 17019
AUTH_SERVICE_GUEST_PORT = 567

_BEGIN = "P9QEMU_DRAWTERM_BEGIN"
_USER = "P9QEMU_USER"
_SYSNAME = "P9QEMU_SYSNAME"
_HOME = "P9QEMU_HOME"
_TIMEZONE = "P9QEMU_TIMEZONE_GMT"
_PLAN9_INI = "P9QEMU_PLAN9_INI"
_NETWORK = "P9QEMU_NETWORK"
_COMPLETE = "P9QEMU_DRAWTERM_COMPLETE"


@dataclass(frozen=True)
class DrawtermAcceptanceCheck:
    """One successful observation made through the real Drawterm session."""

    name: str
    detail: str


@dataclass(frozen=True)
class DrawtermAcceptanceResult:
    """Sanitized host-side results from a Drawterm acceptance run."""

    checks: tuple[DrawtermAcceptanceCheck, ...]
    session_attempts: tuple[int, ...]
    session_stdout: str
    session_stderr: str
    shutdown_stdout: str
    shutdown_stderr: str

    @property
    def status(self) -> str:
        return "passed"


def build_guest_acceptance_commands(
    profile: DrawtermPostinstallProfile,
    *,
    network_mode: NetworkMode,
) -> tuple[str, ...]:
    """Build bounded rc commands that emit machine-readable observations."""

    if network_mode not in {"required", "skip"}:
        raise P9QemuError("Drawterm network mode must be either 'required' or 'skip'")
    commands = [
        f"echo {_BEGIN}; echo {_USER}; echo $user",
        f"echo {_SYSNAME}; cat /dev/sysname",
        f"echo {_HOME}; pwd",
        (f"cmp /adm/timezone/GMT /adm/timezone/local && echo {_TIMEZONE}"),
    ]
    if network_mode == "required":
        commands.append(f"echo {_NETWORK}; ip/ping -n 1 google.com")
    commands.append(f"echo {_COMPLETE}")
    if any(len(command) >= 128 for command in commands):
        raise P9QemuError(
            "Drawterm guest acceptance command exceeds the qualified "
            "128-character transport bound"
        )
    return tuple(commands)


def build_guest_shutdown_command(profile: DrawtermPostinstallProfile) -> str:
    """Read 9fat through the FQA namespace recipe, then halt the VM."""

    command = (
        f"bind -b '#S' /dev; 9fs 9fat /dev/sd00/9fat; "
        f"echo {_PLAN9_INI}; cat {profile.plan9_ini.path}; fshalt"
    )
    if len(command) >= 128:
        raise P9QemuError(
            "Drawterm guest shutdown command exceeds the qualified "
            "128-character transport bound"
        )
    return command


def is_drawterm_protocol_readiness_failure(output: str) -> bool:
    """Identify the one qualified transient seen before p9any is ready."""

    return "cannot read p9any negotiation: hung up" in output


def build_drawterm_command(
    executable: Path,
    profile: DrawtermPostinstallProfile,
    guest_command: str,
) -> list[str]:
    """Build Drawterm argv without placing the password on the command line."""

    return [
        str(executable),
        "-G",
        "-h",
        (f"tcp!{profile.drawterm.bind_address}!{profile.drawterm.cpu_host_port}"),
        "-a",
        (f"tcp!{profile.drawterm.bind_address}!{profile.drawterm.auth_host_port}"),
        "-u",
        profile.nvram.authid,
        "-c",
        guest_command,
    ]


def build_drawterm_environment(
    profile: DrawtermPostinstallProfile,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return a child environment with the password only in PASS."""

    result = dict(os.environ if environ is None else environ)
    result["PASS"] = profile.nvram.password
    return result


def require_secret_absent(
    profile: DrawtermPostinstallProfile,
    text: str,
    *,
    label: str,
) -> None:
    """Reject any evidence text that contains the demonstration password."""

    if profile.nvram.password in text:
        raise P9QemuError(f"{label} exposed the Drawterm demonstration password")


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.replace("\r", "").splitlines()]


def _value_after(lines: list[str], marker: str) -> str:
    try:
        index = lines.index(marker)
    except ValueError as error:
        raise P9QemuError(f"Drawterm output is missing marker {marker}") from error
    for value in lines[index + 1 :]:
        if value:
            return value
    raise P9QemuError(f"Drawterm output has no value after marker {marker}")


def _values_between(lines: list[str], start: str, end: str) -> tuple[str, ...]:
    try:
        start_index = lines.index(start)
        end_index = lines.index(end, start_index + 1)
    except ValueError as error:
        raise P9QemuError(
            f"Drawterm output does not contain ordered markers {start} and {end}"
        ) from error
    return tuple(value for value in lines[start_index + 1 : end_index] if value)


def validate_unattended_boot_transcript(
    transcript: str,
    profile: DrawtermPostinstallProfile,
) -> tuple[DrawtermAcceptanceCheck, ...]:
    """Prove the serial boot was unattended and reached the installed HJFS root."""

    require_secret_absent(profile, transcript, label="serial boot transcript")
    prohibited = (
        "bootargs is (tcp, tls, il, local!device)",
        f"user[{profile.guest.user}]:",
    )
    present = [value for value in prohibited if value in transcript]
    if present:
        raise P9QemuError(
            f"unattended boot transcript contains interactive prompts: {present}"
        )
    required = (
        f"hjfs: fs is {profile.guest.root_partition}",
        "init: starting /bin/rc",
    )
    missing = [value for value in required if value not in transcript]
    if missing:
        raise P9QemuError(
            f"unattended boot transcript is missing required evidence: {missing}"
        )
    return (
        DrawtermAcceptanceCheck(
            "unattended-boot", "serial boot contained no bootargs or user prompt"
        ),
        DrawtermAcceptanceCheck(
            "root-filesystem",
            f"HJFS mounted from {profile.guest.root_partition}",
        ),
        DrawtermAcceptanceCheck(
            "serial-diagnostics", "serial boot messages remained available"
        ),
    )


def validate_drawterm_session_output(
    output: str,
    profile: DrawtermPostinstallProfile,
    *,
    network_mode: NetworkMode,
) -> tuple[DrawtermAcceptanceCheck, ...]:
    """Validate the exact observations returned by an authenticated session."""

    require_secret_absent(profile, output, label="Drawterm session output")
    lines = _lines(output)
    for marker in (_BEGIN, _TIMEZONE, _PLAN9_INI, _COMPLETE):
        if marker not in lines:
            raise P9QemuError(f"Drawterm output is missing marker {marker}")
    observed = {
        "user": _value_after(lines, _USER),
        "system_name": _value_after(lines, _SYSNAME),
        "home": _value_after(lines, _HOME),
    }
    expected = {
        "user": profile.guest.user,
        "system_name": profile.guest.system_name,
        "home": f"/usr/{profile.guest.user}",
    }
    if observed != expected:
        raise P9QemuError(
            f"Drawterm guest identity mismatch: expected {expected}, got {observed}"
        )

    plan9_ini_end = _NETWORK if network_mode == "required" else _COMPLETE
    observed_settings = _values_between(lines, _PLAN9_INI, plan9_ini_end)
    if observed_settings != profile.plan9_ini.target_values:
        raise P9QemuError(
            "Drawterm plan9.ini output does not exactly match the qualified target: "
            f"expected {profile.plan9_ini.target_values}, got {observed_settings}"
        )

    checks = [
        DrawtermAcceptanceCheck(
            "drawterm-authentication", "authenticated Drawterm command completed"
        ),
        DrawtermAcceptanceCheck("guest-user", f"active user is {observed['user']}"),
        DrawtermAcceptanceCheck(
            "system-name", f"system name is {observed['system_name']}"
        ),
        DrawtermAcceptanceCheck(
            "guest-home", f"working directory is {observed['home']}"
        ),
        DrawtermAcceptanceCheck("timezone", "persistent timezone is GMT"),
        DrawtermAcceptanceCheck(
            "plan9-ini", "qualified unattended CPU-server settings are present"
        ),
    ]
    if network_mode == "required":
        if _NETWORK not in lines or "rtt" not in output:
            raise P9QemuError("Drawterm network validation did not observe an ICMP RTT")
        checks.append(
            DrawtermAcceptanceCheck(
                "network-ping", "DNS resolution and an ICMP response were observed"
            )
        )
    elif network_mode != "skip":
        raise P9QemuError("Drawterm network mode must be either 'required' or 'skip'")
    return tuple(checks)
