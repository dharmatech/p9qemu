"""Rotate a Drawterm image password in an overlay and prove the cold-boot result."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import shlex
import sys

from p9qemu.drawterm_password_rotation import (
    build_new_password_probe_command,
    build_old_password_probe_command,
    build_rotation_guest_command,
    build_rotation_shutdown_command,
    generate_rotation_password,
    require_passwords_absent,
)
from p9qemu.drawterm_postinstall import load_drawterm_postinstall_profile
from p9qemu.drawterm_validation import (
    AUTH_SERVICE_GUEST_PORT,
    CPU_SERVICE_GUEST_PORT,
    build_drawterm_command,
)
from p9qemu.errors import P9QemuError
from p9qemu.host import current_host, discover_qemu, resolve_acceleration
from p9qemu.instance import prepare_validation_overlay
from p9qemu.media import sha256_file
from p9qemu.pexpect_drawterm_validation import (
    PasswordRotationValidationError,
    run_pexpect_drawterm_password_rotation,
)
from p9qemu.provenance import (
    artifact_record,
    qemu_img_check,
    qemu_img_info,
    query_tool_version,
    require_unchanged_image,
    utc_timestamp,
    validate_source_commit,
    write_json_new,
    write_text_new,
)
from p9qemu.qemu import (
    PortForward,
    build_automated_validation_command,
    render_command,
)


@dataclass
class EventRecorder:
    events: list[dict[str, str]] = field(default_factory=list)

    def __call__(self, message: str) -> None:
        self.events.append({"timestamp": utc_timestamp(), "message": message})
        print(message, flush=True)

    def json_lines(self) -> str:
        return "".join(
            json.dumps(event, sort_keys=True) + "\n" for event in self.events
        )


@dataclass(frozen=True)
class BundlePaths:
    root: Path
    overlay: Path
    mutation_console: Path
    verification_console: Path
    mutation_qemu_command: Path
    verification_qemu_command: Path
    drawterm_commands: Path
    mutation_stdout: Path
    mutation_stderr: Path
    old_password_stdout: Path
    old_password_stderr: Path
    new_password_stdout: Path
    new_password_stderr: Path
    shutdown_stdout: Path
    shutdown_stderr: Path
    events: Path
    base_info: Path
    base_check_before: Path
    base_check_after: Path
    overlay_check: Path
    manifest: Path


def _bundle_paths(root: Path) -> BundlePaths:
    return BundlePaths(
        root=root,
        overlay=root / "password-rotation-overlay.qcow2",
        mutation_console=root / "mutation-boot.raw.log",
        verification_console=root / "verification-boot.raw.log",
        mutation_qemu_command=root / "mutation-qemu-command.txt",
        verification_qemu_command=root / "verification-qemu-command.txt",
        drawterm_commands=root / "drawterm-commands.txt",
        mutation_stdout=root / "mutation.stdout.log",
        mutation_stderr=root / "mutation.stderr.log",
        old_password_stdout=root / "old-password.stdout.log",
        old_password_stderr=root / "old-password.stderr.log",
        new_password_stdout=root / "new-password.stdout.log",
        new_password_stderr=root / "new-password.stderr.log",
        shutdown_stdout=root / "shutdown.stdout.log",
        shutdown_stderr=root / "shutdown.stderr.log",
        events=root / "events.jsonl",
        base_info=root / "base-qemu-img-info.json",
        base_check_before=root / "base-qemu-img-check-before.txt",
        base_check_after=root / "base-qemu-img-check-after.txt",
        overlay_check=root / "overlay-qemu-img-check.txt",
        manifest=root / "manifest.json",
    )


def _positive_int(value: str) -> int:
    result = int(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Use a disposable overlay to rotate the Drawterm password, cold boot "
            "the result, reject the old credential, accept the new credential, "
            "and remove the overlay."
        )
    )
    parser.add_argument("--postinstall-profile", type=Path, required=True)
    parser.add_argument("--disk", type=Path, required=True)
    parser.add_argument("--expected-disk-sha256", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--drawterm", type=Path, required=True)
    parser.add_argument("--drawterm-source-commit", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--memory", type=_positive_int, default=2048, metavar="MIB")
    parser.add_argument("--accel", choices=("kvm", "tcg"), default="kvm")
    parser.add_argument(
        "--confirm-run",
        action="store_true",
        help="confirm the two-boot password mutation test",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _absolute(path: Path) -> Path:
    return path.expanduser().resolve()


def _require_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise P9QemuError(f"{label} is not an existing file: {path}")


def _require_new_directory(path: Path) -> None:
    if path.exists():
        raise P9QemuError(f"refusing to replace password-rotation output: {path}")
    if not path.parent.is_dir():
        raise P9QemuError(
            f"password-rotation output parent does not exist: {path.parent}"
        )


def _drawterm_commands(drawterm: Path, profile) -> tuple[list[str], ...]:
    guest_commands = (
        build_rotation_guest_command(),
        build_old_password_probe_command(),
        build_new_password_probe_command(),
        build_rotation_shutdown_command(),
    )
    return tuple(
        build_drawterm_command(drawterm, profile, command) for command in guest_commands
    )


def _render_drawterm_commands(commands: tuple[list[str], ...]) -> str:
    labels = (
        "NVRAM mutation (stdin answers redacted and not recorded)",
        "old-password rejection probe",
        "new-password acceptance probe",
        "rotated-password shutdown",
    )
    return "\n\n".join(
        f"# {label}\n{shlex.join(command)}"
        for label, command in zip(labels, commands, strict=True)
    )


def _write_process_evidence(paths: BundlePaths, result) -> None:
    values = (
        (paths.mutation_stdout, result.mutation_stdout),
        (paths.mutation_stderr, result.mutation_stderr),
        (paths.old_password_stdout, result.old_password_stdout),
        (paths.old_password_stderr, result.old_password_stderr),
        (paths.new_password_stdout, result.new_password_stdout),
        (paths.new_password_stderr, result.new_password_stderr),
        (paths.shutdown_stdout, result.shutdown_stdout),
        (paths.shutdown_stderr, result.shutdown_stderr),
    )
    for path, text in values:
        if not path.exists():
            write_text_new(path, text)


def _artifact_paths(paths: BundlePaths) -> dict[str, Path]:
    return {
        "mutation_console": paths.mutation_console,
        "verification_console": paths.verification_console,
        "mutation_qemu_command": paths.mutation_qemu_command,
        "verification_qemu_command": paths.verification_qemu_command,
        "drawterm_commands": paths.drawterm_commands,
        "mutation_stdout": paths.mutation_stdout,
        "mutation_stderr": paths.mutation_stderr,
        "old_password_stdout": paths.old_password_stdout,
        "old_password_stderr": paths.old_password_stderr,
        "new_password_stdout": paths.new_password_stdout,
        "new_password_stderr": paths.new_password_stderr,
        "shutdown_stdout": paths.shutdown_stdout,
        "shutdown_stderr": paths.shutdown_stderr,
        "events": paths.events,
        "base_qemu_img_info": paths.base_info,
        "base_qemu_img_check_before": paths.base_check_before,
        "base_qemu_img_check_after": paths.base_check_after,
        "overlay_qemu_img_check": paths.overlay_check,
    }


def _existing_artifacts(paths: BundlePaths) -> dict[str, dict[str, object]]:
    return {
        name: artifact_record(path, root=paths.root)
        for name, path in _artifact_paths(paths).items()
        if path.is_file()
    }


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    recorder = EventRecorder()
    paths: BundlePaths | None = None
    try:
        if not args.confirm_run:
            raise P9QemuError("--confirm-run is required")
        host = current_host()
        if host.system != "Linux":
            raise P9QemuError("Drawterm password rotation is supported only on Linux")

        source_commit = validate_source_commit(args.source_commit)
        drawterm_source_commit = validate_source_commit(args.drawterm_source_commit)
        profile_path = _absolute(args.postinstall_profile)
        disk = _absolute(args.disk)
        output_dir = _absolute(args.output_dir)
        drawterm = _absolute(args.drawterm)
        _require_file(profile_path, "post-install profile")
        _require_file(disk, "Drawterm derivative disk")
        _require_file(drawterm, "Drawterm executable")
        _require_new_directory(output_dir)
        paths = _bundle_paths(output_dir)

        profile = load_drawterm_postinstall_profile(profile_path)
        expected_disk_hash = args.expected_disk_sha256
        if len(expected_disk_hash) != 64 or any(
            character not in "0123456789abcdef" for character in expected_disk_hash
        ):
            raise P9QemuError("expected disk SHA-256 must be 64 lowercase hex digits")
        disk_hash_before = sha256_file(disk)
        if disk_hash_before != expected_disk_hash:
            raise P9QemuError(
                "Drawterm derivative digest mismatch: "
                f"expected {expected_disk_hash}, got {disk_hash_before}"
            )

        executables = discover_qemu(host)
        acceleration = resolve_acceleration(args.accel, host)
        base_check_before = qemu_img_check(executables.image, disk)
        base_info = qemu_img_info(executables.image, disk)
        if base_info.get("format") != "qcow2":
            raise P9QemuError("Drawterm derivative is not QCOW2")
        if base_info.get("backing-filename") is not None:
            raise P9QemuError("Drawterm derivative is not a standalone image")

        forwards = (
            PortForward(
                profile.drawterm.cpu_host_port,
                CPU_SERVICE_GUEST_PORT,
                host_address=profile.drawterm.bind_address,
            ),
            PortForward(
                profile.drawterm.auth_host_port,
                AUTH_SERVICE_GUEST_PORT,
                host_address=profile.drawterm.bind_address,
            ),
        )
        mutation_qemu = build_automated_validation_command(
            executables.system,
            overlay=paths.overlay,
            console_log=paths.mutation_console,
            memory_mib=args.memory,
            acceleration=acceleration,
            forwards=forwards,
        )
        verification_qemu = build_automated_validation_command(
            executables.system,
            overlay=paths.overlay,
            console_log=paths.verification_console,
            memory_mib=args.memory,
            acceleration=acceleration,
            forwards=forwards,
        )
        rendered_mutation_qemu = render_command(mutation_qemu, system="Linux")
        rendered_verification_qemu = render_command(verification_qemu, system="Linux")
        drawterm_commands = _drawterm_commands(drawterm, profile)
        rendered_drawterm = _render_drawterm_commands(drawterm_commands)
        require_passwords_absent(
            (profile.nvram.password,),
            "\n".join(
                (
                    rendered_mutation_qemu,
                    rendered_verification_qemu,
                    rendered_drawterm,
                )
            ),
            label="rendered password-rotation commands",
        )

        print("Drawterm security gate: disposable NVRAM password rotation")
        print(f"Profile: {profile.profile_id}")
        print(f"Exact derivative SHA-256: {disk_hash_before}")
        print(f"Immutable derivative: {disk}")
        print(f"New evidence directory: {output_dir}")
        print(f"Drawterm source commit: {drawterm_source_commit}")
        print(f"Drawterm executable SHA-256: {sha256_file(drawterm)}")
        print("Replacement password: generated in memory; never recorded")
        print("Mutation transport: Drawterm stdin; value omitted")
        print("Authentication transport: PASS environment; values omitted")
        print("Serial input: disabled")
        print("Overlay retention: never (success or failure)")
        print("\nWould start the mutation boot:\n")
        print(rendered_mutation_qemu)
        print("\nWould cold boot the mutated overlay:\n")
        print(rendered_verification_qemu)
        print("\nWould run these Drawterm commands:\n")
        print(rendered_drawterm)
        if args.dry_run:
            return 0

        paths.root.mkdir()
        write_text_new(paths.mutation_qemu_command, rendered_mutation_qemu + "\n")
        write_text_new(
            paths.verification_qemu_command, rendered_verification_qemu + "\n"
        )
        write_text_new(paths.drawterm_commands, rendered_drawterm + "\n")
        write_json_new(paths.base_info, base_info)
        write_text_new(paths.base_check_before, base_check_before)

        started_at = utc_timestamp()
        status = "failed"
        error_text: str | None = None
        result = None
        attempt_counts: tuple[int, ...] = ()
        overlay_check: str | None = None
        overlay_removed = False
        disk_hash_after = disk_hash_before
        base_check_after: str | None = None
        new_password = generate_rotation_password(profile.nvram.password)
        run_error: P9QemuError | OSError | None = None
        try:
            prepare_validation_overlay(
                executables.image, disk, paths.overlay, progress=recorder
            )
            result, observed_commands, attempt_counts = (
                run_pexpect_drawterm_password_rotation(
                    mutation_qemu,
                    verification_qemu,
                    profile,
                    new_password=new_password,
                    drawterm_executable=drawterm,
                    mutation_console_log=paths.mutation_console,
                    verification_console_log=paths.verification_console,
                    progress=recorder,
                )
            )
            if observed_commands != drawterm_commands:
                raise P9QemuError(
                    "executed Drawterm password-rotation commands differed from evidence"
                )
            _write_process_evidence(paths, result)
            status = result.status
        except (OSError, P9QemuError) as error:
            run_error = error
            error_text = str(error)
            if isinstance(error, PasswordRotationValidationError):
                _write_process_evidence(paths, error)
        finally:
            try:
                if paths.overlay.is_file():
                    overlay_check = qemu_img_check(executables.image, paths.overlay)
                    write_text_new(paths.overlay_check, overlay_check)
            except (OSError, P9QemuError) as error:
                if run_error is None:
                    run_error = error
                    error_text = str(error)
                    status = "failed"
            try:
                if paths.overlay.exists():
                    paths.overlay.unlink()
                overlay_removed = not paths.overlay.exists()
                if overlay_removed:
                    recorder("Removed the disposable password-rotation overlay.")
            except OSError as error:
                if run_error is None:
                    run_error = error
                    error_text = str(error)
                    status = "failed"

            try:
                disk_hash_after = sha256_file(disk)
                require_unchanged_image(disk_hash_before, disk_hash_after)
                base_check_after = qemu_img_check(executables.image, disk)
                write_text_new(paths.base_check_after, base_check_after)
            except (OSError, P9QemuError) as error:
                if run_error is None:
                    run_error = error
                    error_text = str(error)
                    status = "failed"

            require_passwords_absent(
                (profile.nvram.password, new_password),
                recorder.json_lines(),
                label="password-rotation event log",
            )
            write_text_new(paths.events, recorder.json_lines())
            artifacts = _existing_artifacts(paths)
            manifest = {
                "schema": 1,
                "kind": "p9qemu-drawterm-password-rotation-validation",
                "status": status,
                "started_at": started_at,
                "completed_at": utc_timestamp(),
                "p9qemu": {"commit": source_commit},
                "postinstall_profile": {
                    "path": str(profile_path),
                    "sha256": sha256_file(profile_path),
                    "profile_id": profile.profile_id,
                    "credential_class": profile.nvram.credential_class,
                    "password_redacted": True,
                },
                "image": {
                    "path": str(disk),
                    "expected_sha256": expected_disk_hash,
                    "sha256_before": disk_hash_before,
                    "sha256_after": disk_hash_after,
                    "unchanged": disk_hash_before == disk_hash_after,
                    "qemu_img_info": base_info,
                    "qemu_img_check_before": base_check_before,
                    "qemu_img_check_after": base_check_after,
                },
                "overlay": {
                    "path": str(paths.overlay),
                    "removed": overlay_removed,
                    "exists": paths.overlay.exists(),
                    "retained_on_failure": False,
                    "qemu_img_check": overlay_check,
                },
                "host": {
                    "system": host.system,
                    "distribution_id": host.distribution_id,
                    "version_id": host.version_id,
                },
                "qemu": {
                    "system_version": query_tool_version(executables.system),
                    "img_version": query_tool_version(executables.image),
                    "acceleration": acceleration.name,
                    "memory_mib": args.memory,
                    "mutation_command": {
                        "argv": mutation_qemu,
                        "rendered": rendered_mutation_qemu,
                    },
                    "verification_command": {
                        "argv": verification_qemu,
                        "rendered": rendered_verification_qemu,
                    },
                    "serial_input": False,
                },
                "drawterm": {
                    "executable_sha256": sha256_file(drawterm),
                    "source_commit": drawterm_source_commit,
                    "commands": [
                        {"argv": command, "rendered": shlex.join(command)}
                        for command in drawterm_commands
                    ],
                    "old_password_transport": "PASS environment",
                    "new_password_transport": "stdin then PASS environment",
                    "passwords_redacted": True,
                    "replacement_generated": True,
                    "replacement_format": "24 lowercase hexadecimal characters",
                    "replacement_recorded": False,
                    "attempts": list(attempt_counts),
                },
                "checks": (
                    [asdict(check) for check in result.checks]
                    if result is not None
                    else []
                ),
                "artifacts": artifacts,
                "error": error_text,
            }
            serialized_manifest = json.dumps(manifest, sort_keys=True)
            require_passwords_absent(
                (profile.nvram.password, new_password),
                serialized_manifest,
                label="password-rotation manifest",
            )
            write_json_new(paths.manifest, manifest)

        if run_error is not None:
            raise P9QemuError(str(run_error)) from run_error
        print(f"Validation status: {status}")
        print(f"Derivative remained unchanged: {disk_hash_after}")
        print(f"Validation manifest: {paths.manifest}")
        return 0
    except P9QemuError as error:
        suffix = (
            f"; sanitized evidence retained in {paths.root}"
            if paths and paths.root.exists()
            else ""
        )
        print(
            f"validate_drawterm_password_rotation: {error}{suffix}",
            file=sys.stderr,
        )
        return 1
    except KeyboardInterrupt:
        print("\nvalidate_drawterm_password_rotation: interrupted", file=sys.stderr)
        return 130


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
