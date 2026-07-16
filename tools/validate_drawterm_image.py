"""Cold-boot and authenticate to a Drawterm-ready image on Linux."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import shlex
import sys

from p9qemu.drawterm_postinstall import load_drawterm_postinstall_profile
from p9qemu.drawterm_validation import (
    AUTH_SERVICE_GUEST_PORT,
    CPU_SERVICE_GUEST_PORT,
    build_drawterm_command,
    build_guest_acceptance_commands,
    require_secret_absent,
)
from p9qemu.errors import P9QemuError
from p9qemu.host import current_host, discover_qemu, resolve_acceleration
from p9qemu.instance import prepare_validation_overlay
from p9qemu.media import sha256_file
from p9qemu.pexpect_drawterm_validation import (
    DrawtermValidationError,
    run_pexpect_drawterm_validation,
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
    console_log: Path
    qemu_command: Path
    drawterm_command: Path
    drawterm_shutdown_command: Path
    drawterm_stdout: Path
    drawterm_stderr: Path
    drawterm_shutdown_stdout: Path
    drawterm_shutdown_stderr: Path
    events: Path
    base_info: Path
    base_check_before: Path
    base_check_after: Path
    overlay_check: Path
    manifest: Path


def _bundle_paths(root: Path) -> BundlePaths:
    return BundlePaths(
        root=root,
        overlay=root / "validation-overlay.qcow2",
        console_log=root / "boot.raw.log",
        qemu_command=root / "qemu-command.txt",
        drawterm_command=root / "drawterm-command.txt",
        drawterm_shutdown_command=root / "drawterm-shutdown-command.txt",
        drawterm_stdout=root / "drawterm.stdout.log",
        drawterm_stderr=root / "drawterm.stderr.log",
        drawterm_shutdown_stdout=root / "drawterm-shutdown.stdout.log",
        drawterm_shutdown_stderr=root / "drawterm-shutdown.stderr.log",
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
            "Cold boot an exact Drawterm derivative without serial input, "
            "authenticate through loopback forwards, verify guest state, and "
            "retain provenance evidence."
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
        "--network-check", choices=("required", "skip"), default="required"
    )
    parser.add_argument(
        "--confirm-run",
        action="store_true",
        help="confirm the isolated cold-boot and Drawterm login test",
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
        raise P9QemuError(f"refusing to replace Drawterm validation output: {path}")
    if not path.parent.is_dir():
        raise P9QemuError(
            f"Drawterm validation output parent does not exist: {path.parent}"
        )


def _artifact_paths(paths: BundlePaths) -> dict[str, Path]:
    return {
        "console_log": paths.console_log,
        "qemu_command": paths.qemu_command,
        "drawterm_command": paths.drawterm_command,
        "drawterm_shutdown_command": paths.drawterm_shutdown_command,
        "drawterm_stdout": paths.drawterm_stdout,
        "drawterm_stderr": paths.drawterm_stderr,
        "drawterm_shutdown_stdout": paths.drawterm_shutdown_stdout,
        "drawterm_shutdown_stderr": paths.drawterm_shutdown_stderr,
        "events": paths.events,
        "base_qemu_img_info": paths.base_info,
        "base_qemu_img_check_before": paths.base_check_before,
        "base_qemu_img_check_after": paths.base_check_after,
        "overlay_qemu_img_check": paths.overlay_check,
        "retained_overlay": paths.overlay,
    }


def _existing_artifacts(paths: BundlePaths) -> dict[str, dict[str, object]]:
    return {
        name: artifact_record(path, root=paths.root)
        for name, path in _artifact_paths(paths).items()
        if path.is_file()
    }


def _write_result(paths: BundlePaths, result) -> None:
    write_text_new(paths.drawterm_stdout, result.session_stdout)
    write_text_new(paths.drawterm_stderr, result.session_stderr)
    write_text_new(paths.drawterm_shutdown_stdout, result.shutdown_stdout)
    write_text_new(paths.drawterm_shutdown_stderr, result.shutdown_stderr)


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    recorder = EventRecorder()
    paths: BundlePaths | None = None
    try:
        if not args.confirm_run:
            raise P9QemuError("--confirm-run is required")
        host = current_host()
        if host.system != "Linux":
            raise P9QemuError("Drawterm image validation is supported only on Linux")

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
        qemu_command = build_automated_validation_command(
            executables.system,
            overlay=paths.overlay,
            console_log=paths.console_log,
            memory_mib=args.memory,
            acceleration=acceleration,
            forwards=forwards,
        )
        rendered_qemu = render_command(qemu_command, system="Linux")
        guest_commands = build_guest_acceptance_commands(
            profile, network_mode=args.network_check
        )
        drawterm_commands = [
            build_drawterm_command(drawterm, profile, command)
            for command in guest_commands
        ]
        drawterm_shutdown_command = build_drawterm_command(drawterm, profile, "fshalt")
        rendered_drawterm_commands = [
            shlex.join(command) for command in drawterm_commands
        ]
        rendered_drawterm = "\n\n".join(
            f"# acceptance command {index}\n{command}"
            for index, command in enumerate(rendered_drawterm_commands, start=1)
        )
        rendered_shutdown = shlex.join(drawterm_shutdown_command)
        for label, text in (
            ("QEMU command", rendered_qemu),
            ("Drawterm command", rendered_drawterm),
            ("Drawterm shutdown command", rendered_shutdown),
        ):
            require_secret_absent(profile, text, label=label)

        print("Drawterm acceptance: unattended cold boot and real authentication")
        print(f"Profile: {profile.profile_id}")
        print(f"Exact derivative SHA-256: {disk_hash_before}")
        print(f"Immutable derivative: {disk}")
        print(f"New evidence directory: {output_dir}")
        print(f"Drawterm source commit: {drawterm_source_commit}")
        print(f"Drawterm executable SHA-256: {sha256_file(drawterm)}")
        print("Credential transport: PASS environment (value omitted)")
        print("Serial input: disabled")
        print(f"Network check: {args.network_check}")
        print("\nWould start QEMU:\n" if args.dry_run else "\nStarting QEMU:\n")
        print(rendered_qemu)
        print("\nWould authenticate with Drawterm:\n")
        print(rendered_drawterm)
        if args.dry_run:
            return 0

        paths.root.mkdir()
        write_text_new(paths.qemu_command, rendered_qemu + "\n")
        write_text_new(paths.drawterm_command, rendered_drawterm + "\n")
        write_text_new(paths.drawterm_shutdown_command, rendered_shutdown + "\n")
        write_json_new(paths.base_info, base_info)
        write_text_new(paths.base_check_before, base_check_before)

        started_at = utc_timestamp()
        status = "failed"
        error_text: str | None = None
        result = None
        overlay_removed = False
        disk_hash_after = disk_hash_before
        base_check_after: str | None = None
        overlay_check: str | None = None
        try:
            prepare_validation_overlay(
                executables.image,
                disk,
                paths.overlay,
                progress=recorder,
            )
            result, observed_drawterm_command, observed_shutdown_command = (
                run_pexpect_drawterm_validation(
                    qemu_command,
                    profile,
                    drawterm_executable=drawterm,
                    console_log=paths.console_log,
                    network_mode=args.network_check,
                    progress=recorder,
                )
            )
            if observed_drawterm_command != drawterm_commands:
                raise P9QemuError("executed Drawterm commands differed from evidence")
            if observed_shutdown_command != drawterm_shutdown_command:
                raise P9QemuError(
                    "executed Drawterm shutdown command differed from evidence"
                )
            _write_result(paths, result)
            disk_hash_after = sha256_file(disk)
            require_unchanged_image(disk_hash_before, disk_hash_after)
            base_check_after = qemu_img_check(executables.image, disk)
            overlay_check = qemu_img_check(executables.image, paths.overlay)
            write_text_new(paths.base_check_after, base_check_after)
            write_text_new(paths.overlay_check, overlay_check)
            paths.overlay.unlink()
            overlay_removed = True
            recorder("Removed the successful disposable validation overlay.")
            status = result.status
        except (OSError, P9QemuError) as error:
            error_text = str(error)
            if isinstance(error, DrawtermValidationError):
                if not paths.drawterm_stdout.exists():
                    write_text_new(paths.drawterm_stdout, error.session_stdout)
                if not paths.drawterm_stderr.exists():
                    write_text_new(paths.drawterm_stderr, error.session_stderr)
            try:
                disk_hash_after = sha256_file(disk)
            except P9QemuError:
                disk_hash_after = ""
            if base_check_after is None:
                try:
                    base_check_after = qemu_img_check(executables.image, disk)
                    write_text_new(paths.base_check_after, base_check_after)
                except P9QemuError:
                    base_check_after = None
            raise
        finally:
            if not paths.events.exists():
                write_text_new(paths.events, recorder.json_lines())
            completed_at = utc_timestamp()
            artifacts = _existing_artifacts(paths)
            manifest = {
                "schema": 1,
                "kind": "p9qemu-drawterm-image-validation",
                "status": status,
                "started_at": started_at,
                "completed_at": completed_at,
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
                    "exists": paths.overlay.is_file(),
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
                    "command": {"argv": qemu_command, "rendered": rendered_qemu},
                    "serial_input": False,
                },
                "drawterm": {
                    "executable_sha256": sha256_file(drawterm),
                    "source_commit": drawterm_source_commit,
                    "commands": [
                        {"argv": command, "rendered": rendered}
                        for command, rendered in zip(
                            drawterm_commands,
                            rendered_drawterm_commands,
                            strict=True,
                        )
                    ],
                    "shutdown_command": {
                        "argv": drawterm_shutdown_command,
                        "rendered": rendered_shutdown,
                    },
                    "password_transport": "PASS environment",
                    "password_redacted": True,
                    "session_attempts": (
                        list(result.session_attempts) if result is not None else []
                    ),
                },
                "network_check": args.network_check,
                "checks": (
                    [asdict(check) for check in result.checks]
                    if result is not None
                    else []
                ),
                "artifacts": artifacts,
                "error": error_text,
            }
            write_json_new(paths.manifest, manifest)

        print(f"Validation status: {status}")
        print(f"Derivative remained unchanged: {disk_hash_after}")
        print(f"Validation manifest: {paths.manifest}")
        return 0
    except P9QemuError as error:
        suffix = (
            f"; evidence retained in {paths.root}"
            if paths and paths.root.exists()
            else ""
        )
        print(f"validate_drawterm_image: {error}{suffix}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nvalidate_drawterm_image: interrupted", file=sys.stderr)
        return 130


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
