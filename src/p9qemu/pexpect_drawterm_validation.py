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
from p9qemu.drawterm_password_rotation import (
    PasswordRotationCheck,
    PasswordRotationResult,
    build_new_password_probe_command,
    build_old_password_probe_command,
    build_rotation_guest_command,
    build_rotation_shutdown_command,
    build_wrkey_input,
    require_passwords_absent,
    validate_mutation_output,
    validate_new_password_acceptance,
    validate_old_password_rejection,
)
from p9qemu.drawterm_validation import (
    DrawtermAcceptanceCheck,
    DrawtermAcceptanceResult,
    NetworkMode,
    build_drawterm_command,
    build_drawterm_environment,
    build_guest_acceptance_commands,
    build_guest_shutdown_command,
    is_drawterm_protocol_readiness_failure,
    require_secret_absent,
    validate_drawterm_session_output,
    validate_unattended_boot_transcript,
)
from p9qemu.errors import P9QemuError


Progress = Callable[[str], None]


class DrawtermValidationError(P9QemuError):
    """A validation failure carrying already-redacted attempt evidence."""

    def __init__(
        self,
        message: str,
        *,
        session_stdout: str,
        session_stderr: str,
        shutdown_stdout: str,
        shutdown_stderr: str,
    ):
        super().__init__(message)
        self.session_stdout = session_stdout
        self.session_stderr = session_stderr
        self.shutdown_stdout = shutdown_stdout
        self.shutdown_stderr = shutdown_stderr


class PasswordRotationValidationError(P9QemuError):
    """A password-rotation failure carrying only scrubbed process evidence."""

    def __init__(
        self,
        message: str,
        *,
        mutation_stdout: str = "",
        mutation_stderr: str = "",
        old_password_stdout: str = "",
        old_password_stderr: str = "",
        new_password_stdout: str = "",
        new_password_stderr: str = "",
        shutdown_stdout: str = "",
        shutdown_stderr: str = "",
    ):
        super().__init__(message)
        self.mutation_stdout = mutation_stdout
        self.mutation_stderr = mutation_stderr
        self.old_password_stdout = old_password_stdout
        self.old_password_stderr = old_password_stderr
        self.new_password_stdout = new_password_stdout
        self.new_password_stderr = new_password_stderr
        self.shutdown_stdout = shutdown_stdout
        self.shutdown_stderr = shutdown_stderr


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
        if not any(
            _connectable(profile.drawterm.bind_address, port)
            for port in _ports(profile)
        ):
            progress("Confirmed CPU and auth host ports stopped accepting connections.")
            return
        if time.monotonic() >= deadline:
            raise P9QemuError(
                "CPU or auth host port still accepts connections after QEMU exit"
            )
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
    password: str | None = None,
    input_text: str | None = None,
    redacted_passwords: tuple[str, ...] = (),
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
            env=build_drawterm_environment(profile, password=password),
            input=input_text,
        )
    except subprocess.TimeoutExpired as error:
        raise P9QemuError(
            f"Drawterm command timed out after {timeout_seconds} seconds"
        ) from error
    except OSError as error:
        raise P9QemuError(f"could not run Drawterm: {error}") from error
    require_secret_absent(profile, result.stdout, label="Drawterm stdout")
    require_secret_absent(profile, result.stderr, label="Drawterm stderr")
    require_passwords_absent(redacted_passwords, result.stdout, label="Drawterm stdout")
    require_passwords_absent(redacted_passwords, result.stderr, label="Drawterm stderr")
    return result


def _attempt_log(
    attempts: list[tuple[int, int, subprocess.CompletedProcess[str]]],
    stream: str,
) -> str:
    sections = []
    for command_index, attempt, result in attempts:
        output = getattr(result, stream)
        sections.append(
            f"# acceptance command {command_index} attempt {attempt} "
            f"status {result.returncode}\n{output}"
        )
    return "\n".join(sections)


def _read_console_log(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        raise P9QemuError(f"could not read QEMU serial transcript: {error}") from error


def _wait_for_shutdown(child: pexpect.spawn, console_log: Path) -> str:
    try:
        index = child.expect([re.escape("done halting"), pexpect.EOF], timeout=60)
    except pexpect.TIMEOUT as error:
        raise P9QemuError("timed out waiting for guest fshalt") from error
    if index == 1:
        transcript = _read_console_log(console_log)
        if "hjfs: ending" not in transcript:
            raise P9QemuError(
                "QEMU exited without done halting or HJFS shutdown evidence"
            )
        return "HJFS reported ending before QEMU exited"
    try:
        child.expect(pexpect.EOF, timeout=60)
    except pexpect.TIMEOUT as error:
        raise P9QemuError("QEMU did not exit after guest fshalt") from error
    return "guest reported done halting before QEMU exited"


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
    shutdown_command = build_drawterm_command(
        drawterm_executable, profile, build_guest_shutdown_command(profile)
    )
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
    attempts: list[tuple[int, int, subprocess.CompletedProcess[str]]] = []
    attempt_counts: list[int] = []
    shutdown: subprocess.CompletedProcess[str] | None = None
    try:
        _wait_for_unattended_boot(child, profile, progress=progress)
        _wait_for_drawterm_services(child, profile, progress=progress)
        for index, command in enumerate(session_commands, start=1):
            for attempt in range(1, 11):
                session = _run_drawterm(command, profile, timeout_seconds=60)
                attempts.append((index, attempt, session))
                if session.returncode == 0:
                    sessions.append(session)
                    attempt_counts.append(attempt)
                    break
                command_output = "\n".join((session.stdout, session.stderr))
                transient = is_drawterm_protocol_readiness_failure(command_output)
                if attempt == 10 or not transient:
                    raise P9QemuError(
                        f"Drawterm acceptance command {index} exited with status "
                        f"{session.returncode} on attempt {attempt}: "
                        f"{command_output[-500:]!r}"
                    )
                if not child.isalive():
                    raise P9QemuError(
                        "QEMU exited during Drawterm protocol-readiness retries"
                    )
                progress(
                    f"Drawterm acceptance command {index} attempt {attempt} "
                    "was not yet accepted; retrying."
                )
                time.sleep(1)
        shutdown = _run_drawterm(shutdown_command, profile, timeout_seconds=60)
        shutdown_evidence = _wait_for_shutdown(child, console_log)
        progress(f"Guest completed fshalt: {shutdown_evidence}.")
        combined_session = "\n".join(
            [
                *(
                    output
                    for session in sessions
                    for output in (session.stdout, session.stderr)
                ),
                shutdown.stdout,
                shutdown.stderr,
            ]
        )
        session_checks = validate_drawterm_session_output(
            combined_session,
            profile,
            network_mode=network_mode,
        )
        progress("Authenticated with Drawterm and verified guest state.")
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
                "drawterm-session-attempts",
                f"bounded attempts per acceptance command: {attempt_counts}",
            ),
            DrawtermAcceptanceCheck("orderly-shutdown", shutdown_evidence),
            DrawtermAcceptanceCheck(
                "port-release",
                "CPU and auth host ports stopped accepting connections after shutdown",
            ),
        )
        return (
            DrawtermAcceptanceResult(
                checks=checks,
                session_attempts=tuple(attempt_counts),
                session_stdout=_attempt_log(attempts, "stdout"),
                session_stderr=_attempt_log(attempts, "stderr"),
                shutdown_stdout=shutdown.stdout,
                shutdown_stderr=shutdown.stderr,
            ),
            session_commands,
            shutdown_command,
        )
    except P9QemuError as error:
        if attempts:
            raise DrawtermValidationError(
                str(error),
                session_stdout=_attempt_log(attempts, "stdout"),
                session_stderr=_attempt_log(attempts, "stderr"),
                shutdown_stdout=shutdown.stdout if shutdown is not None else "",
                shutdown_stderr=shutdown.stderr if shutdown is not None else "",
            ) from error
        raise
    finally:
        _terminate(child)


def _spawn_password_rotation_qemu(command: list[str], *, phase: str) -> pexpect.spawn:
    if not command:
        raise P9QemuError(f"cannot start an empty {phase} QEMU command")
    try:
        return pexpect.spawn(
            command[0],
            command[1:],
            encoding="utf-8",
            codec_errors="replace",
            echo=False,
            timeout=None,
        )
    except (OSError, pexpect.ExceptionPexpect) as error:
        raise P9QemuError(f"could not start {phase} QEMU: {error}") from error


def _run_after_protocol_readiness(
    command: list[str],
    profile: DrawtermPostinstallProfile,
    *,
    password: str,
    timeout_seconds: int,
    progress: Progress,
    label: str,
    input_text: str | None = None,
    redacted_passwords: tuple[str, ...],
) -> tuple[subprocess.CompletedProcess[str], int]:
    """Retry only the qualified pre-authentication p9any hangup."""

    for attempt in range(1, 11):
        result = _run_drawterm(
            command,
            profile,
            timeout_seconds=timeout_seconds,
            password=password,
            input_text=input_text,
            redacted_passwords=redacted_passwords,
        )
        output = "\n".join((result.stdout, result.stderr))
        if result.returncode == 0 or not is_drawterm_protocol_readiness_failure(output):
            return result, attempt
        if attempt == 10:
            break
        progress(f"{label} attempt {attempt} reached p9any too early; retrying.")
        time.sleep(1)
    raise P9QemuError(f"{label} did not pass p9any readiness after 10 attempts")


def _password_rotation_command_set(
    drawterm_executable: Path,
    profile: DrawtermPostinstallProfile,
) -> tuple[list[str], list[str], list[str], list[str]]:
    return (
        build_drawterm_command(
            drawterm_executable, profile, build_rotation_guest_command()
        ),
        build_drawterm_command(
            drawterm_executable, profile, build_old_password_probe_command()
        ),
        build_drawterm_command(
            drawterm_executable, profile, build_new_password_probe_command()
        ),
        build_drawterm_command(
            drawterm_executable, profile, build_rotation_shutdown_command()
        ),
    )


def _scrubbed_output(
    result: subprocess.CompletedProcess[str] | None,
) -> tuple[str, str]:
    if result is None:
        return "", ""
    return result.stdout, result.stderr


def run_pexpect_drawterm_password_rotation(
    mutation_qemu_command: list[str],
    verification_qemu_command: list[str],
    profile: DrawtermPostinstallProfile,
    *,
    new_password: str,
    drawterm_executable: Path,
    mutation_console_log: Path,
    verification_console_log: Path,
    progress: Progress,
) -> tuple[PasswordRotationResult, tuple[list[str], ...], tuple[int, ...]]:
    """Rotate NVRAM in an overlay, cold boot, and prove old/new behavior."""

    if not drawterm_executable.is_file() or not os.access(drawterm_executable, os.X_OK):
        raise P9QemuError(
            f"Drawterm executable is not an executable file: {drawterm_executable}"
        )
    wrkey_input = build_wrkey_input(profile, new_password)
    passwords = (profile.nvram.password, new_password)
    commands = _password_rotation_command_set(drawterm_executable, profile)
    for index, command in enumerate(commands, start=1):
        require_passwords_absent(
            passwords,
            "\n".join(command),
            label=f"password-rotation Drawterm argv {index}",
        )

    mutation: subprocess.CompletedProcess[str] | None = None
    old_probe: subprocess.CompletedProcess[str] | None = None
    new_probe: subprocess.CompletedProcess[str] | None = None
    shutdown: subprocess.CompletedProcess[str] | None = None
    attempt_counts: list[int] = []
    checks: list[PasswordRotationCheck] = []

    try:
        require_drawterm_ports_available(profile)
        mutation_child = _spawn_password_rotation_qemu(
            mutation_qemu_command, phase="password-mutation"
        )
        try:
            _wait_for_unattended_boot(mutation_child, profile, progress=progress)
            _wait_for_drawterm_services(mutation_child, profile, progress=progress)
            mutation, attempts = _run_after_protocol_readiness(
                commands[0],
                profile,
                password=profile.nvram.password,
                input_text=wrkey_input,
                timeout_seconds=90,
                progress=progress,
                label="auth/wrkey mutation",
                redacted_passwords=passwords,
            )
            attempt_counts.append(attempts)
            checks.extend(
                validate_mutation_output(
                    mutation.returncode,
                    mutation.stdout,
                    mutation.stderr,
                    passwords=passwords,
                )
            )
            shutdown_evidence = _wait_for_shutdown(mutation_child, mutation_console_log)
            checks.append(PasswordRotationCheck("mutation-shutdown", shutdown_evidence))
            transcript = _read_console_log(mutation_console_log)
            require_passwords_absent(
                passwords, transcript, label="password-mutation serial transcript"
            )
            validate_unattended_boot_transcript(transcript, profile)
        finally:
            _terminate(mutation_child)
            _wait_for_drawterm_ports_released(profile, progress=progress)

        require_drawterm_ports_available(profile)
        verification_child = _spawn_password_rotation_qemu(
            verification_qemu_command, phase="password-verification"
        )
        try:
            _wait_for_unattended_boot(verification_child, profile, progress=progress)
            _wait_for_drawterm_services(verification_child, profile, progress=progress)
            old_probe, attempts = _run_after_protocol_readiness(
                commands[1],
                profile,
                password=profile.nvram.password,
                timeout_seconds=20,
                progress=progress,
                label="old-password rejection probe",
                redacted_passwords=passwords,
            )
            attempt_counts.append(attempts)
            checks.append(
                validate_old_password_rejection(
                    old_probe.returncode,
                    old_probe.stdout,
                    old_probe.stderr,
                    passwords=passwords,
                )
            )
            new_probe, attempts = _run_after_protocol_readiness(
                commands[2],
                profile,
                password=new_password,
                timeout_seconds=60,
                progress=progress,
                label="new-password acceptance probe",
                redacted_passwords=passwords,
            )
            attempt_counts.append(attempts)
            checks.append(
                validate_new_password_acceptance(
                    new_probe.returncode,
                    new_probe.stdout,
                    new_probe.stderr,
                    passwords=passwords,
                )
            )
            shutdown, attempts = _run_after_protocol_readiness(
                commands[3],
                profile,
                password=new_password,
                timeout_seconds=60,
                progress=progress,
                label="rotated-password shutdown",
                redacted_passwords=passwords,
            )
            attempt_counts.append(attempts)
            shutdown_evidence = _wait_for_shutdown(
                verification_child, verification_console_log
            )
            checks.extend(
                (
                    PasswordRotationCheck(
                        "verification-cold-boot",
                        "the mutated overlay completed a separate unattended boot",
                    ),
                    PasswordRotationCheck("verification-shutdown", shutdown_evidence),
                )
            )
            transcript = _read_console_log(verification_console_log)
            require_passwords_absent(
                passwords, transcript, label="password-verification serial transcript"
            )
            validate_unattended_boot_transcript(transcript, profile)
        finally:
            _terminate(verification_child)
            _wait_for_drawterm_ports_released(profile, progress=progress)
        checks.append(
            PasswordRotationCheck(
                "port-release",
                "CPU and auth host ports stopped accepting connections after both boots",
            )
        )
    except P9QemuError as error:
        mutation_stdout, mutation_stderr = _scrubbed_output(mutation)
        old_stdout, old_stderr = _scrubbed_output(old_probe)
        new_stdout, new_stderr = _scrubbed_output(new_probe)
        shutdown_stdout, shutdown_stderr = _scrubbed_output(shutdown)
        raise PasswordRotationValidationError(
            str(error),
            mutation_stdout=mutation_stdout,
            mutation_stderr=mutation_stderr,
            old_password_stdout=old_stdout,
            old_password_stderr=old_stderr,
            new_password_stdout=new_stdout,
            new_password_stderr=new_stderr,
            shutdown_stdout=shutdown_stdout,
            shutdown_stderr=shutdown_stderr,
        ) from error

    assert mutation is not None
    assert old_probe is not None
    assert new_probe is not None
    assert shutdown is not None
    return (
        PasswordRotationResult(
            checks=tuple(checks),
            mutation_stdout=mutation.stdout,
            mutation_stderr=mutation.stderr,
            old_password_stdout=old_probe.stdout,
            old_password_stderr=old_probe.stderr,
            new_password_stdout=new_probe.stdout,
            new_password_stderr=new_probe.stderr,
            shutdown_stdout=shutdown.stdout,
            shutdown_stderr=shutdown.stderr,
        ),
        commands,
        tuple(attempt_counts),
    )
