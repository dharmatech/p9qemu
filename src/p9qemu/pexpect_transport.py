"""Linux Pexpect transport for the experimental live installer."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pexpect

from p9qemu.errors import P9QemuError
from p9qemu.installer import InstallerProfile, InstallerStep
from p9qemu.live import LiveDriveResult, drive_installer


Progress = Callable[[str], None]


class PexpectTransport:
    """Adapt a Pexpect child to the transport-independent driver."""

    def __init__(self, child: Any) -> None:
        self.child = child

    def _recent_output(self) -> str:
        value = self.child.before
        if not isinstance(value, str):
            return ""
        return value[-500:].replace("\r", "\\r").replace("\n", "\\n")

    def wait(self, step: InstallerStep) -> None:
        try:
            self.child.expect(step.pattern, timeout=step.timeout_seconds)
        except pexpect.TIMEOUT as error:
            raise P9QemuError(
                f"timed out after {step.timeout_seconds}s waiting for installer "
                f"state {step.state!r}; recent output: {self._recent_output()!r}"
            ) from error
        except pexpect.EOF as error:
            raise P9QemuError(
                f"QEMU exited before installer state {step.state!r}; "
                f"recent output: {self._recent_output()!r}"
            ) from error

    def send_raw(self, value: str) -> None:
        self.child.send(value)

    def send_line(self, value: str) -> None:
        self.child.sendline(value)


def _terminate(child: Any) -> None:
    if child.isalive():
        child.terminate(force=True)
    child.close(force=True)


def run_pexpect_session(
    command: list[str],
    profile: InstallerProfile,
    *,
    progress: Progress,
    stop_before: str | None = None,
) -> LiveDriveResult:
    """Spawn QEMU, drive the installer, and preserve failure diagnostics."""

    if not command:
        raise P9QemuError("cannot start an empty QEMU command")

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
        raise P9QemuError(f"could not start QEMU under Pexpect: {error}") from error

    child.delaybeforesend = 0.05
    completed = False
    try:
        result = drive_installer(
            PexpectTransport(child),
            profile,
            progress=progress,
            stop_before=stop_before,
        )
        if result.stopped_before is not None:
            return result

        try:
            child.expect(pexpect.EOF, timeout=60)
        except pexpect.TIMEOUT as error:
            raise P9QemuError(
                "installer completed, but QEMU did not exit within 60s"
            ) from error
        child.close()
        if child.exitstatus not in (0, None):
            raise P9QemuError(f"QEMU exited with status {child.exitstatus}")
        if child.signalstatus is not None:
            raise P9QemuError(f"QEMU exited from signal {child.signalstatus}")
        completed = True
        return result
    finally:
        if not completed:
            _terminate(child)
