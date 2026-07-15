"""Run a disposable 9front image with SDL and a logged serial console."""

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
from p9qemu.qemu import build_start_command, render_command


SERIAL_MARKER = "P9QEMU_SERIAL_COMMAND_EXECUTED"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Boot an explicitly confirmed disposable image with graphics and "
            "a dedicated logged serial console."
        )
    )
    parser.add_argument("--disk", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--expected-input-sha256", required=True)
    parser.add_argument("--memory", type=int, default=2048, metavar="MIB")
    parser.add_argument("--accel", choices=("kvm", "tcg"), default="kvm")
    parser.add_argument("--display", choices=("gtk", "sdl"), required=True)
    parser.add_argument(
        "--confirm-disposable-copy",
        action="store_true",
        help="confirm that --disk is an expendable experiment disk",
    )
    return parser


def _absolute(path: Path) -> Path:
    return path.expanduser().resolve()


def _terminate(child: pexpect.spawn) -> None:
    if child.isalive():
        child.terminate(force=True)
    child.close(force=True)


def _drive_boot(command: list[str], probe_path: Path) -> tuple[bool, bool]:
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
        raise P9QemuError(f"could not start graphical QEMU: {error}") from error

    child.delaybeforesend = 0.05
    transport = PexpectGuestValidationTransport(child)
    try:
        transport.wait("boot.bootargs", r"bootargs is .*?\[[^\]\n]+\][ \t]*", 120)
        transport.send_line("")
        transport.wait("boot.user", re.escape("user[glenda]:"), 120)
        transport.send_line("glenda")
        transport.wait("boot.root", re.escape("hjfs: fs is /dev/sd00/fs"), 120)
        transport.wait("boot.init", re.escape("init: starting /bin/rc"), 120)

        # VGA initialization can take substantially longer than reaching init.
        # Wait for the independent serial shell instead of probing on a timer.
        serial_shell_prompt = False
        serial_command_executed = False
        try:
            child.expect(r"term%[ \t]*", timeout=120)
            serial_shell_prompt = True
            child.sendline(f"echo {SERIAL_MARKER}")
            try:
                child.expect(rf"(?m)^{re.escape(SERIAL_MARKER)}\r?$", timeout=10)
                serial_command_executed = True
            except pexpect.TIMEOUT:
                pass
        except pexpect.TIMEOUT:
            pass

        write_json_new(
            probe_path,
            {
                "schema": 1,
                "serial_reached_hjfs_root": True,
                "serial_reached_init": True,
                "serial_shell_prompt_after_graphics_init": serial_shell_prompt,
                "probe_command": f"echo {SERIAL_MARKER}",
                "serial_command_executed_after_graphics_init": (
                    serial_command_executed
                ),
                "shell_prompt_timeout_seconds": 120,
                "probe_timeout_seconds": 10,
            },
        )
        print(
            "Serial shell after graphics initialization: "
            f"{'available' if serial_shell_prompt else 'not observed'}",
            flush=True,
        )
        print(
            "Serial command after graphics initialization: "
            f"{'executed' if serial_command_executed else 'no shell response'}",
            flush=True,
        )
        print(
            "Visual checkpoint ready. Use Rio, run fshalt, then close QEMU.",
            flush=True,
        )
        try:
            child.expect(pexpect.EOF, timeout=3600)
        except pexpect.TIMEOUT as error:
            raise P9QemuError("graphical QEMU remained open for one hour") from error
        return serial_shell_prompt, serial_command_executed
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
            raise P9QemuError("refusing to boot a disk whose name lacks 'experiment'")
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
        console_log = output_dir / "graphical-boot.raw.log"
        command = build_start_command(
            executables.system,
            disk=disk,
            memory_mib=args.memory,
            acceleration=acceleration,
            forwards=(),
        )
        command.extend(
            (
                "-display",
                args.display,
                "-monitor",
                "none",
                "-chardev",
                f"stdio,id=serial0,logfile={console_log},logappend=off",
                "-serial",
                "chardev:serial0",
                "-no-reboot",
            )
        )
        rendered = render_command(command, system="Linux")
        check_before = qemu_img_check(executables.image, disk)
        info_before = qemu_img_info(executables.image, disk)

        output_dir.mkdir()
        write_text_new(output_dir / "qemu-command.txt", rendered + "\n")
        write_text_new(output_dir / "qemu-img-check-before.txt", check_before)
        write_json_new(output_dir / "qemu-img-info-before.json", info_before)

        started_at = utc_timestamp()
        print("Starting graphical-plus-serial experiment:\n")
        print(rendered, flush=True)
        serial_shell_prompt, serial_command_executed = _drive_boot(
            command,
            output_dir / "serial-probe.json",
        )
        completed_at = utc_timestamp()

        output_hash = sha256_file(disk)
        check_after = qemu_img_check(executables.image, disk)
        info_after = qemu_img_info(executables.image, disk)
        write_text_new(output_dir / "qemu-img-check-after.txt", check_after)
        write_json_new(output_dir / "qemu-img-info-after.json", info_after)
        log_text = console_log.read_text(encoding="utf-8", errors="replace")
        write_json_new(
            output_dir / "manifest.json",
            {
                "schema": 1,
                "experiment": "graphics-plus-dedicated-serial",
                "status": "host-observation-complete",
                "started_at": started_at,
                "completed_at": completed_at,
                "source_commit": source_commit,
                "driver_sha256": sha256_file(Path(__file__)),
                "disk": disk.name,
                "input_sha256": input_hash,
                "output_sha256": output_hash,
                "acceleration": acceleration.name,
                "memory_mib": args.memory,
                "display": args.display,
                "port_forwards": [],
                "serial_reached_hjfs_root": True,
                "serial_reached_init": True,
                "serial_shell_prompt_after_graphics_init": serial_shell_prompt,
                "serial_command_executed_after_graphics_init": (
                    serial_command_executed
                ),
                "serial_log_contains_done_halting": "done halting" in log_text,
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
        print(f"run_graphics_serial_experiment: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nrun_graphics_serial_experiment: interrupted", file=sys.stderr)
        return 130


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
