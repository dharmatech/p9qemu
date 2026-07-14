"""Experimental Linux-only disposable-overlay image validation."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
from pathlib import Path
import shutil
import sys

from p9qemu.answers import InstallAnswers, load_answers
from p9qemu.errors import P9QemuError
from p9qemu.host import current_host, discover_qemu, resolve_acceleration
from p9qemu.instance import prepare_validation_overlay
from p9qemu.media import sha256_file
from p9qemu.pexpect_validation import run_pexpect_validation
from p9qemu.provenance import (
    artifact_record,
    build_validation_manifest,
    qemu_img_check,
    qemu_img_info,
    query_tool_version,
    require_unchanged_image,
    utc_timestamp,
    write_json_new,
    write_text_new,
)
from p9qemu.qemu import build_automated_validation_command, render_command
from p9qemu.validation import (
    GuestValidationResult,
    NetworkMode,
    build_guest_validation_profile,
)


@dataclass
class EventRecorder:
    events: list[dict[str, str]] = field(default_factory=list)

    def __call__(self, message: str) -> None:
        self.events.append({"timestamp": utc_timestamp(), "message": message})
        print(message, flush=True)

    def json_lines(self) -> str:
        return "".join(json.dumps(event, sort_keys=True) + "\n" for event in self.events)


@dataclass(frozen=True)
class BundlePaths:
    root: Path
    answers: Path
    overlay: Path
    console_log: Path
    command: Path
    events: Path
    info: Path
    check_before: Path
    check_after: Path
    manifest: Path


def _positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Experimentally validate an installed 9front image through a "
            "disposable QCOW2 overlay."
        )
    )
    parser.add_argument("--answers", type=Path, required=True)
    parser.add_argument("--disk", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--memory", type=_positive_int, default=2048, metavar="MIB")
    parser.add_argument("--accel", choices=("kvm", "tcg"), default="kvm")
    parser.add_argument(
        "--network-check",
        choices=("optional", "required", "skip"),
        default="optional",
        help="classify the bounded Internet ping as optional, required, or skipped",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and print the command without creating a bundle or overlay",
    )
    return parser


def _absolute(path: Path) -> Path:
    return path.expanduser().resolve()


def _require_existing_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise P9QemuError(f"{label} is not an existing file: {path}")


def _require_new_directory(path: Path) -> None:
    if path.exists():
        raise P9QemuError(f"refusing to replace validation output: {path}")
    if not path.parent.is_dir():
        raise P9QemuError(
            f"validation output parent directory does not exist: {path.parent}"
        )


def _bundle_paths(root: Path) -> BundlePaths:
    return BundlePaths(
        root=root,
        answers=root / "answers.toml",
        overlay=root / "validation-overlay.qcow2",
        console_log=root / "boot.raw.log",
        command=root / "qemu-command.txt",
        events=root / "events.jsonl",
        info=root / "base-qemu-img-info.json",
        check_before=root / "base-qemu-img-check-before.txt",
        check_after=root / "base-qemu-img-check-after.txt",
        manifest=root / "manifest.json",
    )


def _copy_new(source: Path, destination: Path) -> None:
    try:
        with source.open("rb") as input_stream, destination.open("xb") as output_stream:
            shutil.copyfileobj(input_stream, output_stream)
    except OSError as error:
        raise P9QemuError(
            f"could not preserve answer file in validation bundle: {error}"
        ) from error


def _existing_artifacts(paths: BundlePaths) -> dict[str, dict[str, object]]:
    candidates = {
        "answers": paths.answers,
        "qemu_command": paths.command,
        "events": paths.events,
        "console_log": paths.console_log,
        "qemu_img_info": paths.info,
        "qemu_img_check_before": paths.check_before,
        "qemu_img_check_after": paths.check_after,
    }
    if paths.overlay.is_file():
        candidates["retained_overlay"] = paths.overlay
    return {
        name: artifact_record(path, root=paths.root)
        for name, path in candidates.items()
        if path.is_file()
    }


def _write_bundle_evidence(
    paths: BundlePaths,
    *,
    recorder: EventRecorder,
    info: dict[str, object],
    check_before: str,
    check_after: str | None,
) -> None:
    if not paths.events.exists():
        write_text_new(paths.events, recorder.json_lines())
    if not paths.info.exists():
        write_json_new(paths.info, info)
    if not paths.check_before.exists():
        write_text_new(paths.check_before, check_before)
    if check_after is not None and not paths.check_after.exists():
        write_text_new(paths.check_after, check_after)


def _run_validation(
    *,
    answers: InstallAnswers,
    answers_path: Path,
    disk: Path,
    paths: BundlePaths,
    memory_mib: int,
    acceleration,
    executables,
    host,
    command: list[str],
    rendered: str,
    network_mode: NetworkMode,
) -> int:
    recorder = EventRecorder()
    started_at = utc_timestamp()
    base_sha256_before = sha256_file(disk)
    check_before = qemu_img_check(executables.image, disk)
    image_info = qemu_img_info(executables.image, disk)
    qemu_system_version = query_tool_version(executables.system)
    qemu_img_version = query_tool_version(executables.image)

    try:
        paths.root.mkdir()
        _copy_new(answers_path, paths.answers)
        write_text_new(paths.command, rendered + "\n")
        write_json_new(paths.info, image_info)
        write_text_new(paths.check_before, check_before)
    except (OSError, P9QemuError) as error:
        raise P9QemuError(f"could not create validation bundle: {error}") from error

    validation: GuestValidationResult | None = None
    check_after: str | None = None
    base_sha256_after = base_sha256_before
    overlay_removed = False
    error_text: str | None = None
    interrupted = False
    status = "failed"
    try:
        prepare_validation_overlay(
            executables.image,
            disk,
            paths.overlay,
            progress=recorder,
        )
        validation = run_pexpect_validation(
            command,
            build_guest_validation_profile(answers),
            network_mode=network_mode,
            progress=recorder,
        )
        base_sha256_after = sha256_file(disk)
        require_unchanged_image(base_sha256_before, base_sha256_after)
        check_after = qemu_img_check(executables.image, disk)
        paths.overlay.unlink()
        overlay_removed = True
        recorder("Removed the successful disposable validation overlay.")
        status = validation.status
    except (OSError, P9QemuError) as error:
        error_text = str(error)
        try:
            base_sha256_after = sha256_file(disk)
        except P9QemuError:
            base_sha256_after = ""
        try:
            check_after = qemu_img_check(executables.image, disk)
        except P9QemuError:
            check_after = None
    except KeyboardInterrupt:
        interrupted = True
        error_text = "validation was interrupted"
        try:
            base_sha256_after = sha256_file(disk)
        except P9QemuError:
            base_sha256_after = ""

    completed_at = utc_timestamp()
    _write_bundle_evidence(
        paths,
        recorder=recorder,
        info=image_info,
        check_before=check_before,
        check_after=check_after,
    )
    artifacts = _existing_artifacts(paths)
    manifest = build_validation_manifest(
        status=status,
        started_at=started_at,
        completed_at=completed_at,
        answers=answers,
        answers_sha256=sha256_file(paths.answers),
        base_image=disk,
        base_sha256_before=base_sha256_before,
        base_sha256_after=base_sha256_after,
        overlay=paths.overlay,
        overlay_removed=overlay_removed,
        overlay_exists=paths.overlay.is_file(),
        host=host,
        acceleration=acceleration,
        memory_mib=memory_mib,
        qemu_system_version=qemu_system_version,
        qemu_img_version=qemu_img_version,
        qemu_command=command,
        rendered_qemu_command=rendered,
        image_info=image_info,
        validation=validation,
        network_mode=network_mode,
        artifacts=artifacts,
        error=error_text,
    )
    write_json_new(paths.manifest, manifest)
    if interrupted:
        raise KeyboardInterrupt
    if error_text is not None:
        raise P9QemuError(
            f"validation failed; evidence and overlay were retained in {paths.root}: "
            f"{error_text}"
        )
    print(f"Validation status: {status}")
    print(f"Base image remained unchanged: {base_sha256_after}")
    print(f"Provenance manifest: {paths.manifest}")
    return 0


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        host = current_host()
        if host.system != "Linux":
            raise P9QemuError(
                "the experimental Pexpect validator is supported only on Linux"
            )
        answers_path = _absolute(args.answers)
        disk = _absolute(args.disk)
        output_dir = _absolute(args.output_dir)
        _require_existing_file(answers_path, "answer file")
        _require_existing_file(disk, "base disk image")
        _require_new_directory(output_dir)
        answers = load_answers(answers_path)
        executables = discover_qemu(host)
        acceleration = resolve_acceleration(args.accel, host)
        paths = _bundle_paths(output_dir)
        command = build_automated_validation_command(
            executables.system,
            overlay=paths.overlay,
            console_log=paths.console_log,
            memory_mib=args.memory,
            acceleration=acceleration,
        )
        rendered = render_command(command, system="Linux")

        print("Experimental mode: disposable-overlay validation")
        print(f"Answer file: {answers_path}")
        print(f"Immutable base image: {disk}")
        print(f"New evidence directory: {output_dir}")
        print(f"Network check: {args.network_check}")
        print(f"Acceleration: {acceleration.name}")
        print("\nWould start QEMU:\n" if args.dry_run else "\nStarting QEMU:\n")
        print(rendered, flush=True)
        if args.dry_run:
            return 0
        return _run_validation(
            answers=answers,
            answers_path=answers_path,
            disk=disk,
            paths=paths,
            memory_mib=args.memory,
            acceleration=acceleration,
            executables=executables,
            host=host,
            command=command,
            rendered=rendered,
            network_mode=args.network_check,
        )
    except P9QemuError as error:
        print(f"validate_image: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nvalidate_image: interrupted", file=sys.stderr)
        return 130


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
