"""Transport-independent post-install guest validation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import re
from typing import Literal, Protocol

from p9qemu.answers import InstallAnswers
from p9qemu.errors import P9QemuError


CheckCategory = Literal["deterministic", "environmental"]
CheckStatus = Literal["passed", "failed", "skipped"]
NetworkMode = Literal["optional", "required", "skip"]
Progress = Callable[[str], None]

SHELL_PROMPT = r"term%[ \t]*"


@dataclass(frozen=True)
class ValidationCheck:
    """One guest validation observation."""

    name: str
    category: CheckCategory
    status: CheckStatus
    detail: str


@dataclass(frozen=True)
class GuestValidationProfile:
    """Expected installed state derived from the pinned answer file."""

    profile_id: str
    user: str
    home: str
    system_name: str
    root_partition: str
    plan9_ini_values: tuple[str, ...]


@dataclass(frozen=True)
class GuestValidationResult:
    """Completed deterministic and environmental guest checks."""

    checks: tuple[ValidationCheck, ...]

    @property
    def status(self) -> str:
        if any(
            check.category == "deterministic" and check.status != "passed"
            for check in self.checks
        ):
            return "failed"
        if any(
            check.category == "environmental" and check.status == "failed"
            for check in self.checks
        ):
            return "passed-with-environmental-failures"
        return "passed"


class GuestValidationTransport(Protocol):
    """Minimal console operations required by the validation profile."""

    def wait(self, state: str, pattern: str, timeout_seconds: int) -> None: ...

    def send_line(self, value: str) -> None: ...

    def command(
        self,
        state: str,
        value: str,
        prompt_pattern: str,
        timeout_seconds: int,
    ) -> str: ...


def build_guest_validation_profile(
    answers: InstallAnswers,
) -> GuestValidationProfile:
    """Resolve expected guest state from qualified installation answers."""

    return GuestValidationProfile(
        profile_id=answers.installer_profile,
        user=answers.user,
        home=f"/usr/{answers.user}",
        system_name=answers.system_name,
        root_partition=answers.hjfs_partition,
        plan9_ini_values=(
            f"bootargs=local!{answers.hjfs_partition} -m {answers.hjfs_cache_mib}",
            f"vgasize={answers.vgasize}",
            f"console={answers.console}",
        ),
    )


def _passed(name: str, detail: str) -> ValidationCheck:
    return ValidationCheck(name, "deterministic", "passed", detail)


def _require_output(state: str, output: str, expected: tuple[str, ...]) -> None:
    missing = [value for value in expected if value not in output]
    if missing:
        names = ", ".join(repr(value) for value in missing)
        recent = output[-500:].replace("\r", "\\r").replace("\n", "\\n")
        raise P9QemuError(
            f"guest validation state {state!r} did not contain {names}; "
            f"recent output: {recent!r}"
        )


def _validated_command(
    transport: GuestValidationTransport,
    checks: list[ValidationCheck],
    *,
    state: str,
    command: str,
    expected: tuple[str, ...],
    detail: str,
) -> None:
    output = transport.command(state, command, SHELL_PROMPT, 60)
    _require_output(state, output, expected)
    checks.append(_passed(state, detail))


def drive_guest_validation(
    transport: GuestValidationTransport,
    profile: GuestValidationProfile,
    *,
    network_mode: NetworkMode,
    progress: Progress,
) -> GuestValidationResult:
    """Boot, inspect, network-check, and halt an installed Plan 9 guest."""

    if network_mode not in ("optional", "required", "skip"):
        raise P9QemuError(f"unsupported network validation mode: {network_mode}")

    checks: list[ValidationCheck] = []
    transport.wait(
        "boot.bootargs",
        r"bootargs is .*?\[[^\]\n]+\][ \t]*",
        120,
    )
    transport.send_line("")
    transport.wait(
        "boot.user",
        re.escape(f"user[{profile.user}]:"),
        120,
    )
    transport.send_line(profile.user)
    transport.wait(
        "boot.root",
        re.escape(f"hjfs: fs is {profile.root_partition}"),
        120,
    )
    transport.wait("boot.shell", SHELL_PROMPT, 120)
    checks.extend(
        (
            _passed("serial-boot", "installed disk reached the Plan 9 shell"),
            _passed(
                "root-filesystem",
                f"HJFS mounted from {profile.root_partition}",
            ),
        )
    )
    progress("Booted the installed HJFS root and reached the Plan 9 shell.")

    _validated_command(
        transport,
        checks,
        state="guest.user",
        command="echo $user",
        expected=(profile.user,),
        detail=f"active user is {profile.user}",
    )
    _validated_command(
        transport,
        checks,
        state="guest.home",
        command="pwd",
        expected=(profile.home,),
        detail=f"working directory is {profile.home}",
    )
    _validated_command(
        transport,
        checks,
        state="guest.sysname",
        command="cat /dev/sysname",
        expected=(profile.system_name,),
        detail=f"system name is {profile.system_name}",
    )
    transport.command("guest.mount-9fat", "9fs 9fat", SHELL_PROMPT, 60)
    _validated_command(
        transport,
        checks,
        state="guest.plan9-ini",
        command="cat /n/9fat/plan9.ini",
        expected=profile.plan9_ini_values,
        detail="installed plan9.ini contains the resolved serial boot settings",
    )

    if network_mode == "skip":
        checks.append(
            ValidationCheck(
                "network-ping",
                "environmental",
                "skipped",
                "network validation was disabled",
            )
        )
    else:
        output = transport.command(
            "guest.network-ping",
            "ip/ping -n 1 google.com",
            SHELL_PROMPT,
            60,
        )
        if "rtt" in output:
            checks.append(
                ValidationCheck(
                    "network-ping",
                    "environmental",
                    "passed",
                    "DNS resolution and an ICMP response were observed",
                )
            )
        elif network_mode == "required":
            _require_output("guest.network-ping", output, ("rtt",))
        else:
            checks.append(
                ValidationCheck(
                    "network-ping",
                    "environmental",
                    "failed",
                    "the bounded ping returned without an RTT response",
                )
            )

    transport.send_line("fshalt")
    transport.wait("shutdown.fshalt", re.escape("done halting"), 120)
    checks.append(_passed("orderly-shutdown", "guest completed fshalt"))
    progress("Guest completed fshalt.")
    return GuestValidationResult(tuple(checks))
