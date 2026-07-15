"""Create a prepared runtime image from an immutable installed image."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shutil
import sys
from uuid import uuid4

import pexpect

from p9qemu.answers import load_answers
from p9qemu.errors import P9QemuError
from p9qemu.host import current_host, discover_qemu, resolve_acceleration
from p9qemu.media import sha256_file
from p9qemu.pexpect_validation import PexpectGuestValidationTransport
from p9qemu.provenance import (
    artifact_record,
    build_release_preparation_manifest,
    qemu_img_check,
    qemu_img_info,
    query_tool_version,
    require_unchanged_image,
    utc_timestamp,
    validate_source_commit,
    write_json_new,
    write_text_new,
)
from p9qemu.qemu import build_automated_validation_command, render_command
from p9qemu.release_preparation import drive_release_preparation
from p9qemu.runtime import load_runtime_profile


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Copy an immutable installed image, apply a qualified runtime boot "
            "profile through the serial console, and record the digest chain."
        )
    )
    parser.add_argument("--answers", type=Path, required=True)
    parser.add_argument("--runtime-profile", type=Path, required=True)
    parser.add_argument("--input-disk", type=Path, required=True)
    parser.add_argument("--output-disk", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--expected-input-sha256", required=True)
    parser.add_argument("--memory", type=int, default=2048, metavar="MIB")
    parser.add_argument("--accel", choices=("kvm", "tcg"), default="kvm")
    parser.add_argument(
        "--confirm-create-prepared-copy",
        action="store_true",
        help="confirm creation and mutation of the new --output-disk",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="validate inputs without copying a disk"
    )
    return parser


def _absolute(path: Path) -> Path:
    return path.expanduser().resolve()


def _copy_new(source: Path, destination: Path) -> None:
    temporary = destination.with_name(f".{destination.name}.p9qemu-{uuid4().hex}.part")
    try:
        with source.open("rb") as input_stream, temporary.open("xb") as output_stream:
            shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
        try:
            os.link(temporary, destination)
        except FileExistsError as error:
            raise P9QemuError(
                f"refusing to replace prepared disk: {destination}"
            ) from error
        except OSError as error:
            raise P9QemuError(
                f"could not publish prepared disk {destination}: {error}"
            ) from error
    except OSError as error:
        raise P9QemuError(
            f"could not copy installed disk to {destination}: {error}"
        ) from error
    finally:
        temporary.unlink(missing_ok=True)


def _terminate(child: pexpect.spawn) -> None:
    if child.isalive():
        child.terminate(force=True)
    child.close(force=True)


def _prepare(command: list[str], answers, runtime_profile):
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
        raise P9QemuError(
            f"could not start release-preparation QEMU: {error}"
        ) from error
    child.delaybeforesend = 0.05
    try:
        return drive_release_preparation(
            PexpectGuestValidationTransport(child), answers, runtime_profile
        )
    finally:
        _terminate(child)


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if not args.confirm_create_prepared_copy:
            raise P9QemuError("--confirm-create-prepared-copy is required")
        if args.memory <= 0:
            raise P9QemuError("memory must be a positive number of MiB")
        expected_hash = args.expected_input_sha256.lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            raise P9QemuError("--expected-input-sha256 must be 64 hexadecimal digits")
        host = current_host()
        if host.system != "Linux":
            raise P9QemuError(
                "release preparation is currently supported only on Linux"
            )

        source_commit = validate_source_commit(args.source_commit)
        answers_path = _absolute(args.answers)
        runtime_path = _absolute(args.runtime_profile)
        input_disk = _absolute(args.input_disk)
        output_disk = _absolute(args.output_disk)
        output_dir = _absolute(args.output_dir)
        for path, label in (
            (answers_path, "answer file"),
            (runtime_path, "runtime profile"),
            (input_disk, "installed input disk"),
        ):
            if not path.is_file():
                raise P9QemuError(f"{label} is not an existing file: {path}")
        if output_disk.exists():
            raise P9QemuError(f"refusing to replace prepared disk: {output_disk}")
        if not output_disk.parent.is_dir():
            raise P9QemuError(
                f"prepared disk parent does not exist: {output_disk.parent}"
            )
        if output_dir.exists():
            raise P9QemuError(f"refusing to replace preparation evidence: {output_dir}")
        if not output_dir.parent.is_dir():
            raise P9QemuError(f"evidence parent does not exist: {output_dir.parent}")

        answers = load_answers(answers_path)
        runtime_profile = load_runtime_profile(runtime_path)
        if runtime_profile.installer_profile != answers.installer_profile:
            raise P9QemuError("runtime profile does not match the installation answers")
        input_hash = sha256_file(input_disk)
        if input_hash != expected_hash:
            raise P9QemuError(
                f"input disk hash mismatch: expected {expected_hash}, got {input_hash}"
            )
        executables = discover_qemu(host)
        acceleration = resolve_acceleration(args.accel, host)
        input_check = qemu_img_check(executables.image, input_disk)
        input_info = qemu_img_info(executables.image, input_disk)

        console_log = output_dir / "preparation.raw.log"
        command = build_automated_validation_command(
            executables.system,
            overlay=output_disk,
            console_log=console_log,
            memory_mib=args.memory,
            acceleration=acceleration,
        )
        rendered = render_command(command, system="Linux")
        print("Release preparation: qualified graphical-plus-serial profile")
        print(f"Immutable input: {input_disk}")
        print(f"New prepared image: {output_disk}")
        print(f"New evidence directory: {output_dir}")
        print("\nWould start QEMU:\n" if args.dry_run else "\nStarting QEMU:\n")
        print(rendered, flush=True)
        if args.dry_run:
            return 0

        output_dir.mkdir()
        _copy_new(input_disk, output_disk)
        write_text_new(output_dir / "qemu-command.txt", rendered + "\n")
        write_text_new(output_dir / "qemu-img-check-input.txt", input_check)
        write_json_new(output_dir / "qemu-img-info-input.json", input_info)

        started_at = utc_timestamp()
        result = _prepare(command, answers, runtime_profile)
        completed_at = utc_timestamp()
        input_hash_after = sha256_file(input_disk)
        require_unchanged_image(input_hash, input_hash_after)
        output_hash = sha256_file(output_disk)
        if output_hash == input_hash:
            raise P9QemuError("release preparation did not change the copied image")
        output_check = qemu_img_check(executables.image, output_disk)
        output_info = qemu_img_info(executables.image, output_disk)
        write_text_new(output_dir / "plan9.ini.before.txt", result.before)
        write_text_new(output_dir / "plan9.ini.after.txt", result.after)
        write_text_new(output_dir / "qemu-img-check-output.txt", output_check)
        write_json_new(output_dir / "qemu-img-info-output.json", output_info)
        artifact_paths = {
            "console_log": output_dir / "preparation.raw.log",
            "qemu_command": output_dir / "qemu-command.txt",
            "plan9_ini_before": output_dir / "plan9.ini.before.txt",
            "plan9_ini_after": output_dir / "plan9.ini.after.txt",
            "qemu_img_check_input": output_dir / "qemu-img-check-input.txt",
            "qemu_img_check_output": output_dir / "qemu-img-check-output.txt",
            "qemu_img_info_input": output_dir / "qemu-img-info-input.json",
            "qemu_img_info_output": output_dir / "qemu-img-info-output.json",
        }
        artifacts = {
            name: artifact_record(path, root=output_dir)
            for name, path in artifact_paths.items()
        }
        manifest = build_release_preparation_manifest(
            started_at=started_at,
            completed_at=completed_at,
            source_commit=source_commit,
            answers=answers,
            answers_path=answers_path,
            answers_sha256=sha256_file(answers_path),
            runtime_profile=runtime_profile,
            runtime_profile_path=runtime_path,
            runtime_profile_sha256=sha256_file(runtime_path),
            input_image=input_disk,
            input_sha256=input_hash,
            input_sha256_after=input_hash_after,
            output_image=output_disk,
            output_sha256=output_hash,
            input_image_info=input_info,
            output_image_info=output_info,
            input_image_check=input_check,
            output_image_check=output_check,
            host=host,
            acceleration=acceleration,
            memory_mib=args.memory,
            qemu_system_version=query_tool_version(executables.system),
            qemu_img_version=query_tool_version(executables.image),
            qemu_command=command,
            rendered_qemu_command=rendered,
            artifacts=artifacts,
        )
        manifest_path = output_dir / "manifest.json"
        write_json_new(manifest_path, manifest)
        print(f"Input SHA-256:  {input_hash}")
        print(f"Output SHA-256: {output_hash}")
        print(f"Preparation manifest: {manifest_path}")
        return 0
    except P9QemuError as error:
        print(f"prepare_release_image: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nprepare_release_image: interrupted", file=sys.stderr)
        return 130


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
