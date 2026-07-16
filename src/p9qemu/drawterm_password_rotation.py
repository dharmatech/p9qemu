"""Pure rules for the disposable Drawterm password-rotation gate."""

from __future__ import annotations

from dataclasses import dataclass
import re
import secrets

from p9qemu.drawterm_postinstall import DrawtermPostinstallProfile
from p9qemu.errors import P9QemuError


ROTATION_MARKER = "P9QEMU_NVRAM_PASSWORD_CHANGED"
OLD_PASSWORD_MARKER = "P9QEMU_OLD_PASSWORD_ACCEPTED"
NEW_PASSWORD_MARKER = "P9QEMU_NEW_PASSWORD_ACCEPTED"

_ROTATION_PASSWORD = re.compile(r"^[0-9a-f]{24}$")
_PASSWORD_MISMATCH = "password mismatch with auth server"
_WRONG_PASSWORD = "wrong password"


@dataclass(frozen=True)
class PasswordRotationCheck:
    """One successful observation from the password-rotation gate."""

    name: str
    detail: str


@dataclass(frozen=True)
class PasswordRotationResult:
    """Sanitized process evidence from both cold boots."""

    checks: tuple[PasswordRotationCheck, ...]
    mutation_stdout: str
    mutation_stderr: str
    old_password_stdout: str
    old_password_stderr: str
    new_password_stdout: str
    new_password_stderr: str
    shutdown_stdout: str
    shutdown_stderr: str

    @property
    def status(self) -> str:
        return "passed"


def generate_rotation_password(current_password: str) -> str:
    """Generate a bounded, rc-safe password distinct from the public default."""

    for _attempt in range(10):
        password = secrets.token_hex(12)
        if password != current_password:
            return password
    raise P9QemuError("could not generate a distinct password for the rotation gate")


def validate_rotation_password(password: str, current_password: str) -> None:
    """Require the exact safe format generated for this test."""

    if not _ROTATION_PASSWORD.fullmatch(password):
        raise P9QemuError(
            "rotation password must contain exactly 24 lowercase hexadecimal characters"
        )
    if password == current_password:
        raise P9QemuError("rotation password must differ from the current password")


def require_passwords_absent(
    passwords: tuple[str, ...], text: str, *, label: str
) -> None:
    """Reject evidence containing any non-empty password without echoing it."""

    if any(password and password in text for password in passwords):
        raise P9QemuError(f"{label} exposed password material")


def build_wrkey_input(profile: DrawtermPostinstallProfile, new_password: str) -> str:
    """Build the unrecorded stdin stream for the fixed auth/wrkey prompts."""

    validate_rotation_password(new_password, profile.nvram.password)
    responses = (
        profile.nvram.authid,
        profile.nvram.authdom,
        profile.nvram.secstore_key,
        new_password,
        new_password,
        "yes" if profile.nvram.legacy_p9sk1 else "",
    )
    return "\n".join(responses) + "\n"


def build_rotation_guest_command() -> str:
    """Rewrite NVRAM, emit a success marker, and halt even after failure."""

    command = f"auth/wrkey && echo {ROTATION_MARKER}; fshalt"
    _require_transport_bound(command)
    return command


def build_old_password_probe_command() -> str:
    """Emit a marker only if the obsolete credential authenticates."""

    command = f"echo {OLD_PASSWORD_MARKER}"
    _require_transport_bound(command)
    return command


def build_new_password_probe_command() -> str:
    """Emit a marker after authentication with the rotated credential."""

    command = f"echo {NEW_PASSWORD_MARKER}"
    _require_transport_bound(command)
    return command


def build_rotation_shutdown_command() -> str:
    """Halt the verification boot using the rotated credential."""

    return "fshalt"


def _require_transport_bound(command: str) -> None:
    if len(command) >= 128:
        raise P9QemuError(
            "password-rotation guest command exceeds the qualified "
            "128-character transport bound"
        )


def validate_mutation_output(
    returncode: int,
    stdout: str,
    stderr: str,
    *,
    passwords: tuple[str, ...],
) -> tuple[PasswordRotationCheck, ...]:
    """Prove auth/wrkey completed before the first shutdown."""

    combined = "\n".join((stdout, stderr))
    require_passwords_absent(passwords, combined, label="auth/wrkey output")
    if ROTATION_MARKER not in combined:
        raise P9QemuError(
            "auth/wrkey did not emit the password-rotation success marker"
        )
    # Drawterm may report the connection closing while fshalt terminates QEMU.
    # The marker is emitted only after auth/wrkey exits successfully.
    return (
        PasswordRotationCheck(
            "nvram-password-write",
            "auth/wrkey completed before the mutation guest halted",
        ),
        PasswordRotationCheck(
            "mutation-session-exit",
            f"Drawterm mutation session exited with status {returncode} after its marker",
        ),
    )


def validate_old_password_rejection(
    returncode: int,
    stdout: str,
    stderr: str,
    *,
    passwords: tuple[str, ...],
) -> PasswordRotationCheck:
    """Require Drawterm's explicit wrong-password rejection signature."""

    combined = "\n".join((stdout, stderr))
    require_passwords_absent(passwords, combined, label="old-password probe output")
    if returncode == 0 or OLD_PASSWORD_MARKER in combined:
        raise P9QemuError("the old demonstration password still authenticates")
    missing = [
        marker
        for marker in (_PASSWORD_MISMATCH, _WRONG_PASSWORD)
        if marker not in combined
    ]
    if missing:
        raise P9QemuError(
            "old-password probe failed without Drawterm's qualified "
            f"authentication-rejection signature; missing={missing}"
        )
    return PasswordRotationCheck(
        "old-password-rejected",
        "Drawterm reported password mismatch and wrong password",
    )


def validate_new_password_acceptance(
    returncode: int,
    stdout: str,
    stderr: str,
    *,
    passwords: tuple[str, ...],
) -> PasswordRotationCheck:
    """Require an authenticated marker using the replacement password."""

    combined = "\n".join((stdout, stderr))
    require_passwords_absent(passwords, combined, label="new-password probe output")
    if returncode != 0:
        raise P9QemuError(
            f"new-password Drawterm probe exited with status {returncode}"
        )
    if NEW_PASSWORD_MARKER not in combined:
        raise P9QemuError(
            "new-password Drawterm probe did not emit its authenticated marker"
        )
    return PasswordRotationCheck(
        "new-password-accepted",
        "the rotated credential completed an authenticated Drawterm command",
    )
