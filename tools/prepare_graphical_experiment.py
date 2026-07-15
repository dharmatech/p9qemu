"""Prepare a disposable console-built image for the graphical/serial experiment."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

import pexpect

from p9qemu.errors import P9QemuError
from p9qemu.host import current_host, discover_qemu, resolve_acceleration
from p9qemu.media import sha256_file
from p9qemu.pexpect_validation import PexpectGuestValidationTransport
from p9qemu.provenance import (
    qemu_img_check,
    qemu_img_info,
    query_tool_version,
    utc_timestamp,
    validate_source_commit,
    write_json_new,
    write_text_new,
)
from p9qemu.qemu import build_automated_validation_command, render_command


SHELL_PROMPT = r"term%[ \t]*"
ORIGINAL_SETTINGS = (
    "mouseport=ask",
    "monitor=ask",
    "vgasize=text",
    "console=0",
)
GRAPHICAL_SETTINGS = (
    "mouseport=ps2",
    "monitor=vesa",
    "vgasize=1024x768x16",
    "console=0",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Mutate an explicitly confirmed disposable 9front image while "
            "recording the serial preparation evidence."
        )
    )
    parser.add_argument("--disk", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--expected-input-sha256", required=True)
    parser.add_argument("--memory", type=int, default=2048, metavar="MIB")
    parser.add_argument("--accel", choices=("kvm", "tcg"), default="kvm")
    parser.add_argument(
        "--confirm-disposable-copy",
        action="store_true",
        help="confirm that --disk is expendable and may be changed",
    )
    return parser


def _absolute(path: Path) -> Path:
    return path.expanduser().resolve()


def _require_values(output: str, expected: tuple[str, ...], *, state: str) -> None:
    missing = [value for value in expected if value not in output]
    if missing:
        raise P9QemuError(f"{state} is missing expected values: {missing!r}")


def _terminate(child: pexpect.spawn) -> None:
    if child.isalive():
        child.terminate(force=True)
    child.close(force=True)


def _drive_preparation(command: list[str]) -> tuple[str, str]:
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
        raise P9QemuError(f"could not start preparation QEMU: {error}") from error

    child.delaybeforesend = 0.05
    transport = PexpectGuestValidationTransport(child)
    try:
        transport.wait("boot.bootargs", r"bootargs is .*?\[[^\]\n]+\][ \t]*", 120)
        transport.send_line("")
        transport.wait("boot.user", re.escape("user[glenda]:"), 120)
        transport.send_line("glenda")
        transport.wait("boot.root", re.escape("hjfs: fs is /dev/sd00/fs"), 120)
        transport.wait("boot.shell", SHELL_PROMPT, 120)

        transport.command("guest.mount-9fat", "9fs 9fat", SHELL_PROMPT, 60)
        before = transport.command(
            "guest.plan9-ini-before",
            "cat /n/9fat/plan9.ini",
            SHELL_PROMPT,
            60,
        )
        _require_values(before, ORIGINAL_SETTINGS, state="original plan9.ini")

        rewrite = (
            "sed 's/^mouseport=.*/mouseport=ps2/; "
            "s/^monitor=.*/monitor=vesa/; "
            "s/^vgasize=.*/vgasize=1024x768x16/' "
            "/n/9fat/plan9.ini >/tmp/p9qemu-plan9.ini"
        )
        transport.command("guest.rewrite", rewrite, SHELL_PROMPT, 60)
        staged = transport.command(
            "guest.plan9-ini-staged",
            "cat /tmp/p9qemu-plan9.ini",
            SHELL_PROMPT,
            60,
        )
        _require_values(staged, GRAPHICAL_SETTINGS, state="staged plan9.ini")

        transport.command(
            "guest.install-plan9-ini",
            "cp /tmp/p9qemu-plan9.ini /n/9fat/plan9.ini",
            SHELL_PROMPT,
            60,
        )
        after = transport.command(
            "guest.plan9-ini-after",
            "cat /n/9fat/plan9.ini",
            SHELL_PROMPT,
            60,
        )
        _require_values(after, GRAPHICAL_SETTINGS, state="installed plan9.ini")
        if any(value in after for value in ORIGINAL_SETTINGS[:3]):
            raise P9QemuError("installed plan9.ini retained a replaced setting")
        transport.command(
            "guest.remove-temporary",
            "rm /tmp/p9qemu-plan9.ini",
            SHELL_PROMPT,
            60,
        )
        transport.send_line("fshalt")
        transport.wait("shutdown.fshalt", re.escape("done halting"), 120)
        return before.replace("\r", ""), after.replace("\r", "")
    finally:
        _terminate(child)


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if not args.confirm_disposable_copy:
            raise P9QemuError("--confirm-disposable-copy is required")
        if args.memory <= 0:
            raise P9QemuError("memory must be a positive number of MiB")
        expected_hash = args.expected_input_sha256.lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            raise P9QemuError("--expected-input-sha256 must be 64 hexadecimal digits")

        host = current_host()
        if host.system != "Linux":
            raise P9QemuError("this development experiment is supported only on Linux")
        source_commit = validate_source_commit(args.source_commit)
        disk = _absolute(args.disk)
        output_dir = _absolute(args.output_dir)
        if not disk.is_file():
            raise P9QemuError(f"disk is not an existing file: {disk}")
        if "experiment" not in disk.name:
            raise P9QemuError("refusing to mutate a disk whose name lacks 'experiment'")
        if output_dir.exists():
            raise P9QemuError(f"refusing to replace evidence directory: {output_dir}")
        if not output_dir.parent.is_dir():
            raise P9QemuError(f"evidence parent does not exist: {output_dir.parent}")

        input_hash = sha256_file(disk)
        if input_hash != expected_hash:
            raise P9QemuError(
                f"input disk hash mismatch: expected {expected_hash}, got {input_hash}"
            )

        executables = discover_qemu(host)
        acceleration = resolve_acceleration(args.accel, host)
        console_log = output_dir / "preparation.raw.log"
        command = build_automated_validation_command(
            executables.system,
            overlay=disk,
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
        write_json_new(output_dir / "qemu-img-info-before.json", info_before)

        started_at = utc_timestamp()
        print("Starting disposable graphical-profile preparation:\n")
        print(rendered, flush=True)
        before, after = _drive_preparation(command)
        completed_at = utc_timestamp()

        output_hash = sha256_file(disk)
        check_after = qemu_img_check(executables.image, disk)
        info_after = qemu_img_info(executables.image, disk)
        write_text_new(output_dir / "plan9.ini.before.txt", before)
        write_text_new(output_dir / "plan9.ini.after.txt", after)
        write_text_new(output_dir / "qemu-img-check-after.txt", check_after)
        write_json_new(output_dir / "qemu-img-info-after.json", info_after)
        write_json_new(
            output_dir / "manifest.json",
            {
                "schema": 1,
                "experiment": "graphics-plus-serial-preparation",
                "status": "passed",
                "started_at": started_at,
                "completed_at": completed_at,
                "source_commit": source_commit,
                "driver_sha256": sha256_file(Path(__file__)),
                "disk": disk.name,
                "input_sha256": input_hash,
                "output_sha256": output_hash,
                "original_settings": list(ORIGINAL_SETTINGS),
                "graphical_settings": list(GRAPHICAL_SETTINGS),
                "console_retained": "console=0" in after,
                "acceleration": acceleration.name,
                "memory_mib": args.memory,
                "qemu_version": query_tool_version(executables.system),
                "qemu_img_version": query_tool_version(executables.image),
                "qemu_command": command,
            },
        )
        print(f"Input SHA-256:  {input_hash}")
        print(f"Output SHA-256: {output_hash}")
        print(f"Evidence: {output_dir}")
        return 0
    except P9QemuError as error:
        print(f"prepare_graphical_experiment: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nprepare_graphical_experiment: interrupted", file=sys.stderr)
        return 130


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
