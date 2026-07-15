"""Transport-independent, fail-closed 9front release preparation."""

from __future__ import annotations

from dataclasses import dataclass
import re

from p9qemu.answers import InstallAnswers
from p9qemu.errors import P9QemuError
from p9qemu.runtime import RuntimeBootProfile
from p9qemu.validation import GuestValidationTransport, SHELL_PROMPT


@dataclass(frozen=True)
class ReleasePreparationResult:
    """Verified plan9.ini state before and after release preparation."""

    before: str
    after: str


def _settings(
    output: str, profile: RuntimeBootProfile, *, state: str
) -> dict[str, str]:
    found: dict[str, list[str]] = {name: [] for name in profile.setting_names}
    pattern = re.compile(
        rf"^({'|'.join(re.escape(name) for name in profile.setting_names)})=(.*)$"
    )
    for line in output.replace("\r", "").splitlines():
        match = pattern.fullmatch(line.strip())
        if match:
            found[match.group(1)].append(match.group(2))
    duplicates = sorted(name for name, values in found.items() if len(values) > 1)
    missing = sorted(name for name, values in found.items() if not values)
    if duplicates or missing:
        raise P9QemuError(
            f"{state} has invalid runtime-setting cardinality; "
            f"duplicates={duplicates}, missing={missing}"
        )
    return {name: values[0] for name, values in found.items()}


def _require_settings(
    output: str,
    profile: RuntimeBootProfile,
    expected: tuple[str, ...],
    *,
    state: str,
) -> None:
    actual = _settings(output, profile, state=state)
    expected_mapping = dict(value.split("=", 1) for value in expected)
    if actual != expected_mapping:
        raise P9QemuError(
            f"{state} runtime settings do not match the qualified profile; "
            f"expected={expected_mapping!r}, actual={actual!r}"
        )


def _rewrite_command(profile: RuntimeBootProfile) -> str:
    substitutions = []
    for name in profile.setting_names:
        source = getattr(profile, f"source_{name}")
        target = getattr(profile, f"target_{name}")
        if source != target:
            substitutions.append(f"s/^{name}=.*/{name}={target}/")
    expression = "; ".join(substitutions)
    return f"sed '{expression}' {profile.plan9_ini_path} >/tmp/p9qemu-plan9.ini"


def drive_release_preparation(
    transport: GuestValidationTransport,
    answers: InstallAnswers,
    profile: RuntimeBootProfile,
) -> ReleasePreparationResult:
    """Apply one qualified runtime profile and verify every selected value."""

    if profile.installer_profile != answers.installer_profile:
        raise P9QemuError(
            "runtime profile installer binding does not match the answer file"
        )

    transport.wait("boot.bootargs", r"bootargs is .*?\[[^\]\n]+\][ \t]*", 120)
    transport.send_line("")
    transport.wait("boot.user", re.escape(f"user[{answers.user}]:"), 120)
    transport.send_line(answers.user)
    transport.wait("boot.root", re.escape(f"hjfs: fs is {answers.hjfs_partition}"), 120)
    transport.wait("boot.shell", SHELL_PROMPT, 120)
    transport.command("guest.mount-9fat", "9fs 9fat", SHELL_PROMPT, 60)

    before = transport.command(
        "guest.plan9-ini-before",
        f"cat {profile.plan9_ini_path}",
        SHELL_PROMPT,
        60,
    )
    _require_settings(
        before,
        profile,
        profile.source_values,
        state="original plan9.ini",
    )

    transport.command("guest.rewrite", _rewrite_command(profile), SHELL_PROMPT, 60)
    staged = transport.command(
        "guest.plan9-ini-staged",
        "cat /tmp/p9qemu-plan9.ini",
        SHELL_PROMPT,
        60,
    )
    _require_settings(
        staged,
        profile,
        profile.target_values,
        state="staged plan9.ini",
    )
    transport.command(
        "guest.install-plan9-ini",
        f"cp /tmp/p9qemu-plan9.ini {profile.plan9_ini_path}",
        SHELL_PROMPT,
        60,
    )
    after = transport.command(
        "guest.plan9-ini-after",
        f"cat {profile.plan9_ini_path}",
        SHELL_PROMPT,
        60,
    )
    _require_settings(
        after,
        profile,
        profile.target_values,
        state="installed plan9.ini",
    )
    transport.command(
        "guest.remove-temporary",
        "rm /tmp/p9qemu-plan9.ini",
        SHELL_PROMPT,
        60,
    )
    transport.send_line("fshalt")
    transport.wait("shutdown.fshalt", re.escape("done halting"), 120)
    return ReleasePreparationResult(
        before=before.replace("\r", ""),
        after=after.replace("\r", ""),
    )
