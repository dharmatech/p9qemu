"""Experimental Linux-only automated 9front installer driver."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from p9qemu.answers import load_answers
from p9qemu.errors import P9QemuError
from p9qemu.host import current_host, discover_qemu, resolve_acceleration
from p9qemu.instance import prepare_disk
from p9qemu.installer import build_11554_hjfs_profile
from p9qemu.media import sha256_file
from p9qemu.pexpect_transport import run_pexpect_session
from p9qemu.provenance import (
    build_install_manifest,
    qemu_img_check,
    qemu_img_info,
    query_tool_version,
    utc_timestamp,
    validate_source_commit,
    write_json_new,
)
from p9qemu.qemu import build_automated_install_command, render_command


def _positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def _require_complete_provenance(args: argparse.Namespace) -> tuple[Path, str]:
    if args.install_manifest is None or args.source_commit is None:
        raise P9QemuError(
            "complete installations require --install-manifest and --source-commit"
        )
    return _absolute(args.install_manifest), validate_source_commit(args.source_commit)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Experimental Linux-only answer-driven 9front installer."
    )
    parser.add_argument("--answers", type=Path, required=True)
    parser.add_argument("--iso", type=Path, required=True)
    parser.add_argument("--disk", type=Path, required=True)
    parser.add_argument("--console-log", type=Path, required=True)
    parser.add_argument(
        "--install-manifest",
        type=Path,
        help="new private provenance manifest required for a complete installation",
    )
    parser.add_argument(
        "--source-commit",
        help="complete Git commit required for a complete installation",
    )
    parser.add_argument("--memory", type=_positive_int, default=1024, metavar="MIB")
    parser.add_argument("--accel", choices=("kvm", "tcg"), default="kvm")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--smoke-test",
        action="store_true",
        help="stop safely before responding to the first installer task prompt",
    )
    mode.add_argument(
        "--complete",
        action="store_true",
        help="run the complete destructive installation profile",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and print the command without creating files or starting QEMU",
    )
    return parser


def _absolute(path: Path) -> Path:
    return path.expanduser().resolve()


def _require_existing_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise P9QemuError(f"{label} is not an existing file: {path}")


def _require_new_file(path: Path, label: str) -> None:
    if path.exists():
        raise P9QemuError(f"refusing to replace existing {label}: {path}")
    if not path.parent.is_dir():
        raise P9QemuError(f"{label} parent directory does not exist: {path.parent}")


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        host = current_host()
        if host.system != "Linux":
            raise P9QemuError(
                "the experimental Pexpect installer is supported only on Linux"
            )

        answers_path = _absolute(args.answers)
        iso = _absolute(args.iso)
        disk = _absolute(args.disk)
        console_log = _absolute(args.console_log)
        install_manifest: Path | None = None
        source_commit: str | None = None
        if args.complete:
            install_manifest, source_commit = _require_complete_provenance(args)
        _require_existing_file(answers_path, "answer file")
        _require_existing_file(iso, "installation ISO")
        _require_new_file(disk, "target disk")
        _require_new_file(console_log, "console log")
        if install_manifest is not None:
            _require_new_file(install_manifest, "install manifest")

        answers = load_answers(answers_path)
        profile = build_11554_hjfs_profile(answers)
        actual_iso_digest = sha256_file(iso)
        if actual_iso_digest.lower() != profile.iso_sha256.lower():
            raise P9QemuError(
                f"checksum mismatch for installation ISO {iso}\n"
                f"expected: {profile.iso_sha256}\n"
                f"actual:   {actual_iso_digest.lower()}"
            )

        executables = discover_qemu(host)
        acceleration = resolve_acceleration(args.accel, host)
        command = build_automated_install_command(
            executables.system,
            disk=disk,
            iso=iso,
            console_log=console_log,
            memory_mib=args.memory,
            acceleration=acceleration,
        )
        rendered = render_command(command, system="Linux")

        mode = "smoke test" if args.smoke_test else "complete installation"
        print(f"Experimental mode: {mode}")
        print(f"Answer file: {answers_path}")
        print(f"Verified ISO: {iso}")
        print(f"Fresh target disk: {disk}")
        print(f"Console log: {console_log}")
        if install_manifest is not None:
            print(f"Install manifest: {install_manifest}")
            print(f"Source commit: {source_commit}")
        print(f"Acceleration: {acceleration.name}")
        print("\nWould start QEMU:\n" if args.dry_run else "\nStarting QEMU:\n")
        print(rendered, flush=True)
        if args.dry_run:
            return 0

        started_at = utc_timestamp()
        prepare_disk(
            executables.image,
            disk,
            answers.disk_size,
            progress=print,
        )
        result = run_pexpect_session(
            command,
            profile,
            progress=print,
            stop_before="menu.configfs" if args.smoke_test else None,
        )
        if result.stopped_before is not None:
            print(f"Smoke test passed; stopped safely before {result.stopped_before}.")
        else:
            if install_manifest is None or source_commit is None:
                raise P9QemuError("complete installation provenance was not resolved")
            manifest = build_install_manifest(
                started_at=started_at,
                completed_at=utc_timestamp(),
                source_commit=source_commit,
                answers=answers,
                answers_path=answers_path,
                answers_sha256=sha256_file(answers_path),
                iso_path=iso,
                iso_sha256=actual_iso_digest.lower(),
                image_path=disk,
                image_sha256=sha256_file(disk),
                console_log=console_log,
                console_log_sha256=sha256_file(console_log),
                host=host,
                acceleration=acceleration,
                memory_mib=args.memory,
                qemu_system_version=query_tool_version(executables.system),
                qemu_img_version=query_tool_version(executables.image),
                qemu_command=command,
                rendered_qemu_command=rendered,
                image_info=qemu_img_info(executables.image, disk),
                image_check=qemu_img_check(executables.image, disk),
            )
            write_json_new(install_manifest, manifest)
            print("Automated installation completed and QEMU exited.")
            print(f"Install provenance manifest: {install_manifest}")
        return 0
    except P9QemuError as error:
        print(f"automated_install: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nautomated_install: interrupted", file=sys.stderr)
        return 130


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
