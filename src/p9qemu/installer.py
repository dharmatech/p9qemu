"""Release-pinned installer state machines and transcript replay."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal

from p9qemu.answers import (
    ISO_SHA256_11554,
    PROFILE_ID_11554_HJFS,
    PROFILE_ID_11554_HJFS_GMT_V1,
    InstallAnswers,
)
from p9qemu.errors import P9QemuError


INSTALLER_PROFILE_REVISION_11554 = 1
INSTALLER_SOURCE_REVISION_11554 = "db4a6fa3843734802a6870bbd93b1a97e2c37b2b"
SendMode = Literal["line", "raw"]

_ANSI_ESCAPE = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\)|[@-_])"
)


def normalize_console(text: str) -> str:
    """Remove terminal control traffic while retaining installer text."""

    text = _ANSI_ESCAPE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
    output: list[str] = []
    for character in text:
        if character == "\b":
            if output and output[-1] != "\n":
                output.pop()
            continue
        codepoint = ord(character)
        if character == "\x7f" or (codepoint < 32 and character not in {"\n", "\t"}):
            continue
        output.append(character)
    return "".join(output)


@dataclass(frozen=True)
class InstallerStep:
    """One expected installer state and the line to send after observing it."""

    state: str
    pattern: str
    response: str | None
    send_mode: SendMode = "line"
    timeout_seconds: int = 60


@dataclass(frozen=True)
class InstallerAction:
    """A response authorized by a successful state transition."""

    state: str
    response: str
    send_mode: SendMode


@dataclass(frozen=True)
class InstallerProfile:
    """A strict ordered state machine bound to one installation medium."""

    profile_id: str
    revision: int
    iso_sha256: str
    source_revision: str
    steps: tuple[InstallerStep, ...]


@dataclass(frozen=True)
class ReplayResult:
    """Observed states and authorized actions from transcript replay."""

    states: tuple[str, ...]
    actions: tuple[InstallerAction, ...]


def _literal_step(
    state: str,
    prompt: str,
    response: str | None,
    *,
    send_mode: SendMode = "line",
    timeout_seconds: int = 60,
) -> InstallerStep:
    return InstallerStep(
        state=state,
        pattern=re.escape(prompt) + r"[ \t]*",
        response=response,
        send_mode=send_mode,
        timeout_seconds=timeout_seconds,
    )


def build_11554_hjfs_profile(answers: InstallAnswers) -> InstallerProfile:
    """Build the first intentionally narrow 9front 11554 HJFS profile."""

    supported_profiles = {
        PROFILE_ID_11554_HJFS,
        PROFILE_ID_11554_HJFS_GMT_V1,
    }
    if answers.installer_profile not in supported_profiles:
        raise P9QemuError(
            f"unsupported installer profile {answers.installer_profile!r}"
        )
    if answers.iso_sha256 != ISO_SHA256_11554:
        raise P9QemuError(
            "the 9front 11554 installer profile is not certified for "
            f"ISO digest {answers.iso_sha256}"
        )

    steps = (
        InstallerStep(
            "boot.interrupt",
            r"bootfile=/amd64/9pc64",
            " ",
            send_mode="raw",
        ),
        InstallerStep(
            "boot.console",
            r"(?m)^>",
            f"console={answers.console}",
        ),
        InstallerStep("boot.kernel", r"(?m)^>", "boot"),
        InstallerStep(
            "boot.bootargs",
            r"bootargs is .*?\[[^\]\n]+\][ \t]*",
            "",
            timeout_seconds=120,
        ),
        _literal_step("boot.user", "user[glenda]:", answers.user),
        InstallerStep(
            "boot.vgasize",
            r"vgasize is .*?\[[^\]\n]+\][ \t]*",
            answers.vgasize,
        ),
        InstallerStep("shell.start_installer", r"(?m)^term%[ \t]*", "inst/start"),
        _literal_step("menu.configfs", "Task to do [configfs]:", "configfs"),
        _literal_step(
            "configfs.filesystem",
            "File system (cwfs64x, hjfs, gefs)[cwfs64x]:",
            answers.filesystem,
        ),
        _literal_step("menu.partdisk", "Task to do [partdisk]:", "partdisk"),
        _literal_step(
            "partdisk.target_disk",
            "sd00 - QEMU QEMU HARDDISK 2.5+",
            None,
        ),
        _literal_step(
            "partdisk.install_media",
            "sd01 - QEMU QEMU CD-ROM 2.5+",
            None,
        ),
        _literal_step(
            "partdisk.target",
            "Disk to partition (sd00, sd01)[no default]:",
            answers.disk_target,
        ),
        _literal_step(
            "partdisk.table",
            "Install mbr or gpt (mbr, gpt)[no default]:",
            answers.partition_table,
        ),
        InstallerStep(
            "partdisk.layout",
            r"(?m)^'\* p1\s+0 3916\s+"
            r"\(3916 cylinders, 29\.99 GB\) PLAN9[ \t]*",
            None,
        ),
        InstallerStep("fdisk.write", r"(?m)^>>>[ \t]*", "w"),
        InstallerStep("fdisk.quit", r"(?m)^>>>[ \t]*", "q"),
        _literal_step("menu.prepdisk", "Task to do [prepdisk]:", "prepdisk"),
        _literal_step(
            "prepdisk.partition",
            "Plan 9 partition to subdivide (/dev/sd00/plan9)[/dev/sd00/plan9]:",
            "/dev/sd00/plan9",
        ),
        InstallerStep(
            "prepdisk.layout",
            r"(?m)^fs 62705676[ \t]*",
            None,
        ),
        InstallerStep("prep.write", r"(?m)^>>>[ \t]*", "w"),
        InstallerStep("prep.quit", r"(?m)^>>>[ \t]*", "q"),
        _literal_step("menu.mountfs", "Task to do [mountfs]:", "mountfs"),
        _literal_step(
            "mountfs.partition",
            "Hjfs partition (/dev/sd00/fs)[/dev/sd00/fs]:",
            answers.hjfs_partition,
        ),
        _literal_step(
            "mountfs.cache",
            "Size of RAM filesystem cache (MB)? [147]:",
            str(answers.hjfs_cache_mib),
        ),
        _literal_step(
            "mountfs.ream",
            "Ream the filesystem? (yes, no)[yes]:",
            "yes" if answers.ream_filesystem else "no",
        ),
        _literal_step("menu.confignet", "Task to do [confignet]:", "confignet"),
        _literal_step(
            "confignet.method",
            "Configuration method (manual, automatic)[automatic]:",
            answers.network_method,
        ),
        _literal_step("menu.mountdist", "Task to do [mountdist]:", "mountdist"),
        _literal_step(
            "mountdist.device",
            "Distribution disk (/dev/sd01/data, /dev/sd00/fs, /)[/]:",
            answers.distribution_device,
        ),
        _literal_step(
            "mountdist.path",
            "Location of archives [/]:",
            answers.distribution_path,
        ),
        _literal_step("menu.copydist", "Task to do [copydist]:", "copydist"),
        _literal_step(
            "menu.ndbsetup",
            "Task to do [ndbsetup]:",
            "ndbsetup",
            timeout_seconds=1800,
        ),
        _literal_step("ndbsetup.system_name", "sysname [cirno]:", answers.system_name),
        _literal_step("menu.tzsetup", "Task to do [tzsetup]:", "tzsetup"),
        InstallerStep(
            "tzsetup.timezone",
            r"(?m)^Time Zone \([^\n]+\)\[US_Eastern\]:[ \t]*",
            answers.timezone,
        ),
        _literal_step("menu.bootsetup", "Task to do [bootsetup]:", "bootsetup"),
        _literal_step(
            "bootsetup.partition",
            "Plan 9 FAT partition (/dev/sd00/9fat)[/dev/sd00/9fat]:",
            answers.boot_partition,
        ),
        _literal_step(
            "bootsetup.mbr",
            "Install the Plan 9 master boot record (yes, no)[no default]:",
            "yes" if answers.install_plan9_mbr else "no",
        ),
        _literal_step(
            "bootsetup.active",
            "Mark the Plan 9 partition active (yes, no)[no default]:",
            "yes" if answers.mark_plan9_partition_active else "no",
        ),
        _literal_step("menu.finish", "Task to do [finish]:", "finish"),
        _literal_step(
            "finish.completed",
            "Congratulations; you've completed the install.",
            None,
            timeout_seconds=120,
        ),
        _literal_step("finish.rebooting", "rebooting...", None),
    )
    return InstallerProfile(
        profile_id=answers.installer_profile,
        revision=INSTALLER_PROFILE_REVISION_11554,
        iso_sha256=answers.iso_sha256,
        source_revision=INSTALLER_SOURCE_REVISION_11554,
        steps=steps,
    )


class InstallerStateMachine:
    """Authorize responses only for the profile's next expected state."""

    def __init__(self, profile: InstallerProfile) -> None:
        self.profile = profile
        self._position = 0

    @property
    def complete(self) -> bool:
        return self._position == len(self.profile.steps)

    @property
    def expected(self) -> InstallerStep | None:
        if self.complete:
            return None
        return self.profile.steps[self._position]

    def observe(self, state: str) -> InstallerAction | None:
        expected = self.expected
        if expected is None:
            raise P9QemuError(
                f"unexpected installer state {state!r} after profile completion"
            )
        if state != expected.state:
            raise P9QemuError(
                f"unexpected installer state {state!r}; expected {expected.state!r}"
            )
        self._position += 1
        if expected.response is None:
            return None
        return InstallerAction(
            state=state,
            response=expected.response,
            send_mode=expected.send_mode,
        )


def replay_transcript(text: str, profile: InstallerProfile) -> ReplayResult:
    """Replay profile recognition against a captured console transcript."""

    normalized = normalize_console(text)
    machine = InstallerStateMachine(profile)
    cursor = 0
    states: list[str] = []
    actions: list[InstallerAction] = []

    while not machine.complete:
        step = machine.expected
        assert step is not None
        match = re.search(step.pattern, normalized[cursor:])
        if match is None:
            tail = normalized[max(0, cursor - 160) : cursor].replace("\n", "\\n")
            raise P9QemuError(
                f"transcript ended before installer state {step.state!r}; "
                f"recent output: {tail!r}"
            )
        cursor += match.end()
        states.append(step.state)
        action = machine.observe(step.state)
        if action is not None:
            actions.append(action)

    return ReplayResult(states=tuple(states), actions=tuple(actions))
