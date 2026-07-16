"""Linux process adapter for unattended boot and real Drawterm acceptance."""

from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path
import re
import socket
import subprocess
import time

import pexpect

from p9qemu.drawterm_postinstall import DrawtermPostinstallProfile
from p9qemu.drawterm_validation import (
    DrawtermAcceptanceCheck,
    DrawtermAcceptanceResult,
    NetworkMode,
    build_drawterm_command,
    build_drawterm_environment,
    build_guest_acceptance_commands,
    require_secret_absent,
    validate_drawterm_session_output,
    validate_unattended_boot_transcript,
)
from p9qemu.errors import P9QemuError


Progress = Callable[[str], None]


def _ports(profile: DrawtermPostinstallProfile) -> tuple[int, int]:
    return (
        profile.drawterm.cpu_host_port,
        profile.drawterm.auth_host_port,
    )


def require_drawterm_ports_available(profile: DrawtermPostinstallProfile) -> None:
    """Fail before launch if either loopback host port is already occupied."""

    sockets: list[socket.socket] = []
    try:
        for port in _ports(profile):
            candidate = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sockets.append(candidate)
            try:
                candidate.bind((profile.drawterm.bind_address, port))
            except OSError as error:
                raise P9QemuError(
                    f"Drawterm validation host port is unavailable: "
                    f"{profile.drawterm.bind_address}:{port}: {error}"
                ) from error
    finally:
        for candidate in sockets:
            candidate.close()


def _wait_for_drawterm_ports_released(
    profile: DrawtermPostinstallProfile, *, progress: Progress
) -> None:
    deadline = time.monotonic() + 10
    while True:
        try:
            require_drawterm_ports_available(profile)
            progress("Confirmed CPU and auth host ports were released.")
            return
        except P9QemuError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.25)


def _terminate(child: pexpect.spawn) -> None:
    if child.isalive():
        child.terminate(force=True)
    child.close(force=True)


def _wait_for_unattended_boot(
    child: pexpect.spawn,
    profile: DrawtermPostinstallProfile,
    *,
    progress: Progress,
) -> None:
    prompt_patterns = (
        re.escape("bootargs is (tcp, tls, il, local!device)"),
        re.escape(f"user[{profile.guest.user}]:"),
    )
    root_pattern = re.escape(f"hjfs: fs is {profile.guest.root_partition}")
    init_pattern = re.escape("init: starting /bin/rc")
    found_root = False
    found_init = False
    deadline = time.monotonic() + 180
    while not (found_root and found_init):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise P9QemuError("timed out waiting for unattended HJFS boot diagnostics")
        try:
            index = child.expect(
                [*prompt_patterns, root_pattern, init_pattern, pexpect.EOF],
                timeout=min(30, remaining),
            )
        except pexpect.TIMEOUT:
            progress("Still waiting for unattended boot diagnostics.")
            continue
        if index < len(prompt_patterns):
            raise P9QemuError(
                "unattended Drawterm image stopped at an interactive boot prompt"
            )
        if index == 2:
            found_root = True
        elif index == 3:
            found_init = True
        else:
            raise P9QemuError(
                "QEMU exited before the unattended Drawterm image completed boot"
            )
    progress("Observed unattended HJFS boot and init diagnostics without input.")


def _connectable(address: str, port: int) -> bool:
    try:
        with socket.create_connection((address, port), timeout=1):
            return True
    except OSError:
        return False


def _wait_for_drawterm_services(
    child: pexpect.spawn,
    profile: DrawtermPostinstallProfile,
    *,
    progress: Progress,
) -> None:
    deadline = time.monotonic() + 120
    reported = False
    while time.monotonic() < deadline:
        if not child.isalive():
            raise P9QemuError("QEMU exited while waiting for Drawterm services")
        if all(
            _connectable(profile.drawterm.bind_address, port)
            for port in _ports(profile)
        ):
            progress("Observed loopback-only CPU and auth listeners.")
            return
        try:
            index = child.expect([pexpect.EOF, pexpect.TIMEOUT], timeout=1)
        except pexpect.ExceptionPexpect as error:
            raise P9QemuError(
                f"could not monitor QEMU while waiting for Drawterm: {error}"
            ) from error
        if index == 0:
            raise P9QemuError("QEMU exited while waiting for Drawterm services")
        if not reported and deadline - time.monotonic() < 90:
            progress("Still waiting for the CPU and auth listeners.")
            reported = True
    raise P9QemuError("timed out waiting for loopback CPU and auth listeners")


def _run_drawterm(
    command: list[str],
    profile: DrawtermPostinstallProfile,
    *,
    timeout_seconds: int,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout_seconds,
            env=build_drawterm_environment(profile),
        )
    except subprocess.TimeoutExpired as error:
        raise P9QemuError(
            f"Drawterm command timed out after {timeout_seconds} seconds"
        ) from error
    except OSError as error:
        raise P9QemuError(f"could not run Drawterm: {error}") from error
    require_secret_absent(profile, result.stdout, label="Drawterm stdout")
    require_secret_absent(profile, result.stderr, label="Drawterm stderr")
    return result


def _read_console_log(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        raise P9QemuError(f"could not read QEMU serial transcript: {error}") from error


def _wait_for_shutdown(child: pexpect.spawn) -> None:
    try:
        child.expect(re.escape("done halting"), timeout=60)
    except pexpect.TIMEOUT as error:
        raise P9QemuError("timed out waiting for guest fshalt") from error
    except pexpect.EOF as error:
        raise P9QemuError("QEMU exited before reporting done halting") from error
    try:
        child.expect(pexpect.EOF, timeout=60)
    except pexpect.TIMEOUT as error:
        raise P9QemuError("QEMU did not exit after guest fshalt") from error


def run_pexpect_drawterm_validation(
    qemu_command: list[str],
    profile: DrawtermPostinstallProfile,
    *,
    drawterm_executable: Path,
    console_log: Path,
    network_mode: NetworkMode,
    progress: Progress,
) -> tuple[DrawtermAcceptanceResult, list[list[str]], list[str]]:
    """Cold boot without input, authenticate via Drawterm, and halt cleanly."""

    if not qemu_command:
        raise P9QemuError("cannot start an empty QEMU Drawterm validation command")
    if not drawterm_executable.is_file() or not os.access(drawterm_executable, os.X_OK):
        raise P9QemuError(
            f"Drawterm executable is not an executable file: {drawterm_executable}"
        )
    require_drawterm_ports_available(profile)
    guest_commands = build_guest_acceptance_commands(profile, network_mode=network_mode)
    session_commands = [
        build_drawterm_command(drawterm_executable, profile, command)
        for command in guest_commands
    ]
    shutdown_command = build_drawterm_command(drawterm_executable, profile, "fshalt")
    for index, command in enumerate(session_commands, start=1):
        require_secret_absent(
            profile, "\n".join(command), label=f"Drawterm session {index} argv"
        )
    for label, command in (("Drawterm shutdown argv", shutdown_command),):
        require_secret_absent(profile, "\n".join(command), label=label)

    try:
        child = pexpect.spawn(
            qemu_command[0],
            qemu_command[1:],
            encoding="utf-8",
            codec_errors="replace",
            echo=False,
            timeout=None,
        )
    except (OSError, pexpect.ExceptionPexpect) as error:
        raise P9QemuError(
            f"could not start unattended Drawterm validation QEMU: {error}"
        ) from error

    sessions: list[subprocess.CompletedProcess[str]] = []
    shutdown: subprocess.CompletedProcess[str] | None = None
    try:
        _wait_for_unattended_boot(child, profile, progress=progress)
        _wait_for_drawterm_services(child, profile, progress=progress)
        for index, command in enumerate(session_commands, start=1):
            session = _run_drawterm(command, profile, timeout_seconds=60)
            command_output = "\n".join((session.stdout, session.stderr))
            if session.returncode != 0:
                raise P9QemuError(
                    f"Drawterm acceptance command {index} exited with status "
                    f"{session.returncode}: {command_output[-500:]!r}"
                )
            sessions.append(session)
        combined_session = "\n".join(
            output
            for session in sessions
            for output in (session.stdout, session.stderr)
        )
        session_checks = validate_drawterm_session_output(
            combined_session,
            profile,
            network_mode=network_mode,
        )
        progress("Authenticated with Drawterm and verified guest state.")

        shutdown = _run_drawterm(shutdown_command, profile, timeout_seconds=60)
        _wait_for_shutdown(child)
        progress("Guest completed fshalt and QEMU exited.")
        transcript = _read_console_log(console_log)
        boot_checks = validate_unattended_boot_transcript(transcript, profile)
        _wait_for_drawterm_ports_released(profile, progress=progress)
        checks = (
            *boot_checks,
            DrawtermAcceptanceCheck(
                "loopback-services",
                "CPU and auth listeners were reachable only through pinned loopback forwards",
            ),
            *session_checks,
            DrawtermAcceptanceCheck(
                "orderly-shutdown", "guest completed fshalt and QEMU exited"
            ),
            DrawtermAcceptanceCheck(
                "port-release", "CPU and auth host ports were released after shutdown"
            ),
        )
        return (
            DrawtermAcceptanceResult(
                checks=checks,
                session_stdout="\n".join(session.stdout for session in sessions),
                session_stderr="\n".join(session.stderr for session in sessions),
                shutdown_stdout=shutdown.stdout,
                shutdown_stderr=shutdown.stderr,
            ),
            session_commands,
            shutdown_command,
        )
    finally:
        _terminate(child)
