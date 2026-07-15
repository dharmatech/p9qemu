"""Validate the graphical runtime experiment through a disposable overlay."""

from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import os
from pathlib import Path
import re
import sys

from p9qemu.answers import load_answers
from p9qemu.errors import P9QemuError
from p9qemu.host import current_host, discover_qemu, resolve_acceleration
from p9qemu.instance import prepare_validation_overlay
from p9qemu.media import sha256_file
from p9qemu.pexpect_validation import run_pexpect_validation
from p9qemu.provenance import (
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
from p9qemu.validation import build_guest_validation_profile


GRAPHICAL_PLAN9_INI_VALUES = (
    "bootargs=local!/dev/sd00/fs -m 147",
    "mouseport=ps2",
    "monitor=vesa",
    "vgasize=1024x768x16",
    "console=0",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the normal serial validator against the read-only graphical "
            "runtime experiment through a disposable QCOW2 overlay."
        )
    )
    parser.add_argument("--answers", type=Path, required=True)
    parser.add_argument("--disk", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--expected-base-sha256", required=True)
    parser.add_argument("--memory", type=int, default=2048, metavar="MIB")
    parser.add_argument("--accel", choices=("kvm", "tcg"), default="kvm")
    return parser


def _absolute(path: Path) -> Path:
    return path.expanduser().resolve()


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.memory <= 0:
            raise P9QemuError("memory must be a positive number of MiB")
        expected_hash = args.expected_base_sha256.lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            raise P9QemuError("--expected-base-sha256 must be 64 hexadecimal digits")

        host = current_host()
        if host.system != "Linux":
            raise P9QemuError("this development experiment is supported only on Linux")
        source_commit = validate_source_commit(args.source_commit)
        answers_path = _absolute(args.answers)
        disk = _absolute(args.disk)
        output_dir = _absolute(args.output_dir)
        if not answers_path.is_file():
            raise P9QemuError(f"answers are not an existing file: {answers_path}")
        if not disk.is_file():
            raise P9QemuError(f"disk is not an existing file: {disk}")
        if os.access(disk, os.W_OK):
            raise P9QemuError("graphical experiment base must be read-only")
        if output_dir.exists():
            raise P9QemuError(f"refusing to replace evidence directory: {output_dir}")
        if not output_dir.parent.is_dir():
            raise P9QemuError(f"evidence parent does not exist: {output_dir.parent}")

        base_hash_before = sha256_file(disk)
        if base_hash_before != expected_hash:
            raise P9QemuError(
                f"base hash mismatch: expected {expected_hash}, got {base_hash_before}"
            )

        answers = load_answers(answers_path)
        profile = replace(
            build_guest_validation_profile(answers),
            profile_id=f"{answers.installer_profile}+graphical-runtime-experiment",
            plan9_ini_values=GRAPHICAL_PLAN9_INI_VALUES,
        )
        executables = discover_qemu(host)
        acceleration = resolve_acceleration(args.accel, host)
        overlay = output_dir / "validation-overlay.qcow2"
        console_log = output_dir / "boot.raw.log"
        command = build_automated_validation_command(
            executables.system,
            overlay=overlay,
            console_log=console_log,
            memory_mib=args.memory,
            acceleration=acceleration,
        )
        rendered = render_command(command, system="Linux")
        check_before = qemu_img_check(executables.image, disk)
        info_before = qemu_img_info(executables.image, disk)

        output_dir.mkdir()
        write_text_new(output_dir / "qemu-command.txt", rendered + "\n")
        write_text_new(output_dir / "qemu-img-check-before.txt", check_before)
        write_json_new(output_dir / "qemu-img-info.json", info_before)

        started_at = utc_timestamp()
        print("Starting headless validation of the graphical runtime image:\n")
        print(rendered, flush=True)
        result = None
        error_text = None
        overlay_removed = False
        check_after = None
        base_hash_after = base_hash_before
        try:
            prepare_validation_overlay(
                executables.image,
                disk,
                overlay,
                progress=lambda message: print(message, flush=True),
            )
            result = run_pexpect_validation(
                command,
                profile,
                network_mode="required",
                progress=lambda message: print(message, flush=True),
            )
            base_hash_after = sha256_file(disk)
            require_unchanged_image(base_hash_before, base_hash_after)
            check_after = qemu_img_check(executables.image, disk)
            overlay.unlink()
            overlay_removed = True
        except (OSError, P9QemuError) as error:
            error_text = str(error)
            base_hash_after = sha256_file(disk)
            try:
                check_after = qemu_img_check(executables.image, disk)
            except P9QemuError:
                check_after = None

        completed_at = utc_timestamp()
        if check_after is not None:
            write_text_new(output_dir / "qemu-img-check-after.txt", check_after)
        write_json_new(
            output_dir / "manifest.json",
            {
                "schema": 1,
                "experiment": "headless-validation-of-graphical-runtime",
                "status": result.status if result is not None else "failed",
                "error": error_text,
                "started_at": started_at,
                "completed_at": completed_at,
                "source_commit": source_commit,
                "driver_sha256": sha256_file(Path(__file__)),
                "answers_sha256": sha256_file(answers_path),
                "base_disk": disk.name,
                "base_sha256_before": base_hash_before,
                "base_sha256_after": base_hash_after,
                "base_unchanged": base_hash_before == base_hash_after,
                "base_read_only": not os.access(disk, os.W_OK),
                "overlay_removed": overlay_removed,
                "overlay_exists": overlay.exists(),
                "runtime_plan9_ini_values": list(GRAPHICAL_PLAN9_INI_VALUES),
                "checks": (
                    [asdict(check) for check in result.checks]
                    if result is not None
                    else []
                ),
                "network_mode": "required",
                "acceleration": acceleration.name,
                "memory_mib": args.memory,
                "qemu_version": query_tool_version(executables.system),
                "qemu_img_version": query_tool_version(executables.image),
                "qemu_command": command,
            },
        )
        if error_text is not None:
            raise P9QemuError(
                f"graphical experiment validation failed; evidence retained in "
                f"{output_dir}: {error_text}"
            )
        print(f"Validation status: {result.status}")
        print(f"Base image remained unchanged: {base_hash_after}")
        print(f"Removed successful overlay: {overlay_removed}")
        print(f"Evidence: {output_dir}")
        return 0
    except P9QemuError as error:
        print(f"validate_graphical_experiment: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nvalidate_graphical_experiment: interrupted", file=sys.stderr)
        return 130


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
