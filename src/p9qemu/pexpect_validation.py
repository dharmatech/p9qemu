"""Linux Pexpect adapter for post-install guest validation."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pexpect

from p9qemu.errors import P9QemuError
from p9qemu.validation import (
    GuestValidationError,
    GuestValidationProfile,
    GuestValidationResult,
    NetworkMode,
    drive_guest_validation,
)


Progress = Callable[[str], None]


class PexpectGuestValidationTransport:
    """Expose state-named waits and commands over a QEMU serial PTY."""

    def __init__(self, child: Any) -> None:
        self.child = child

    def _recent_output(self) -> str:
        value = self.child.before
        if not isinstance(value, str):
            return ""
        return value[-500:].replace("\r", "\\r").replace("\n", "\\n")

    def wait(self, state: str, pattern: str, timeout_seconds: int) -> None:
        category = "environmental" if state == "guest.network-ping" else "deterministic"
        try:
            self.child.expect(pattern, timeout=timeout_seconds)
        except pexpect.TIMEOUT as error:
            raise GuestValidationError(
                f"timed out after {timeout_seconds}s waiting for guest validation "
                f"state {state!r}; recent output: {self._recent_output()!r}",
                category=category,
                state=state,
            ) from error
        except pexpect.EOF as error:
            raise GuestValidationError(
                f"QEMU exited before guest validation state {state!r}; "
                f"recent output: {self._recent_output()!r}",
                category=category,
                state=state,
            ) from error

    def send_line(self, value: str) -> None:
        self.child.sendline(value)

    def command(
        self,
        state: str,
        value: str,
        prompt_pattern: str,
        timeout_seconds: int,
    ) -> str:
        self.send_line(value)
        self.wait(state, prompt_pattern, timeout_seconds)
        output = self.child.before
        return output.replace("\r", "") if isinstance(output, str) else ""


def _terminate(child: Any) -> None:
    if child.isalive():
        child.terminate(force=True)
    child.close(force=True)


def run_pexpect_validation(
    command: list[str],
    profile: GuestValidationProfile,
    *,
    network_mode: NetworkMode,
    progress: Progress,
) -> GuestValidationResult:
    """Run validation and close QEMU only after fshalt or a failure."""

    if not command:
        raise P9QemuError("cannot start an empty QEMU validation command")
    try:
        child = pexpect.spawn(
            command[0],
            command[1:],
            encoding="utf-8",
            codec_errors="replace",
            echo=False,
            timeout=None,
        )
    except (OSError, pexpect.ExceptionPexpect) as error:
        raise P9QemuError(f"could not start QEMU validation: {error}") from error

    child.delaybeforesend = 0.05
    try:
        return drive_guest_validation(
            PexpectGuestValidationTransport(child),
            profile,
            network_mode=network_mode,
            progress=progress,
        )
    finally:
        _terminate(child)
