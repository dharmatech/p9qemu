"""Transport-independent live installer state-machine driver."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from p9qemu.errors import P9QemuError
from p9qemu.installer import (
    InstallerAction,
    InstallerProfile,
    InstallerStateMachine,
    InstallerStep,
)


Progress = Callable[[str], None]


class InstallerTransport(Protocol):
    """Minimal terminal operations required by the installer policy layer."""

    def wait(self, step: InstallerStep) -> None: ...

    def send_raw(self, value: str) -> None: ...

    def send_line(self, value: str) -> None: ...


@dataclass(frozen=True)
class LiveDriveResult:
    """States observed before completion or a deliberate smoke-test stop."""

    states: tuple[str, ...]
    actions: tuple[InstallerAction, ...]
    stopped_before: str | None

    @property
    def complete(self) -> bool:
        return self.stopped_before is None


def drive_installer(
    transport: InstallerTransport,
    profile: InstallerProfile,
    *,
    progress: Progress,
    stop_before: str | None = None,
) -> LiveDriveResult:
    """Drive a live terminal while authorizing only profile-defined responses."""

    known_states = {step.state for step in profile.steps}
    if stop_before is not None and stop_before not in known_states:
        raise P9QemuError(f"unknown installer stop state: {stop_before!r}")

    machine = InstallerStateMachine(profile)
    states: list[str] = []
    actions: list[InstallerAction] = []

    while not machine.complete:
        step = machine.expected
        assert step is not None
        transport.wait(step)
        states.append(step.state)
        progress(f"Observed installer state: {step.state}")

        if step.state == stop_before:
            return LiveDriveResult(
                states=tuple(states),
                actions=tuple(actions),
                stopped_before=step.state,
            )

        action = machine.observe(step.state)
        if action is None:
            continue
        if action.send_mode == "raw":
            transport.send_raw(action.response)
        elif action.send_mode == "line":
            transport.send_line(action.response)
        else:  # pragma: no cover - SendMode constrains constructed profiles
            raise P9QemuError(f"unknown installer send mode: {action.send_mode!r}")
        actions.append(action)
        progress(f"Sent response for installer state: {step.state}")

    return LiveDriveResult(
        states=tuple(states),
        actions=tuple(actions),
        stopped_before=None,
    )
