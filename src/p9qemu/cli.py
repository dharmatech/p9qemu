"""Command-line interface and version 1 workflow orchestration."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

from p9qemu import __version__
from p9qemu.constants import (
    DEFAULT_ARCHIVE_SHA256,
    DEFAULT_DISK_NAME,
    DEFAULT_DISK_SIZE,
    DEFAULT_INSTALL_MEMORY_MIB,
    DEFAULT_ISO_URL,
    DEFAULT_START_MEMORY_MIB,
)
from p9qemu.errors import P9QemuError
from p9qemu.host import (
    Acceleration,
    HostInfo,
    current_host,
    discover_qemu,
    query_qemu_accelerators,
    resolve_acceleration,
    user_cache_dir,
)
from p9qemu.instance import inspect_disk, prepare_disk, validate_disk_size
from p9qemu.media import MediaSpec, inspect_media, prepare_media
from p9qemu.qemu import build_install_command, build_start_command, render_command


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive integer") from error
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def _add_runtime_options(parser: argparse.ArgumentParser, *, memory: int) -> None:
    parser.add_argument(
        "--disk",
        type=Path,
        default=Path(DEFAULT_DISK_NAME),
        help=f"disk-image path (default: {DEFAULT_DISK_NAME})",
    )
    parser.add_argument(
        "--memory",
        type=_positive_int,
        default=memory,
        metavar="MIB",
        help=f"guest memory in MiB (default: {memory})",
    )
    parser.add_argument(
        "--accel",
        choices=("auto", "kvm", "whpx", "tcg"),
        default="auto",
        help="acceleration mode (default: auto)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and show planned actions without changing state",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="suppress routine p9qemu output",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="p9qemu",
        description="Install and run transparent 9front QEMU virtual machines.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    install = commands.add_parser(
        "install", help="install 9front into an instance disk"
    )
    _add_runtime_options(install, memory=DEFAULT_INSTALL_MEMORY_MIB)
    install.add_argument(
        "--disk-size",
        default=DEFAULT_DISK_SIZE,
        metavar="SIZE",
        help=f"size of a newly created disk (default: {DEFAULT_DISK_SIZE})",
    )
    install.add_argument(
        "--iso-url",
        default=DEFAULT_ISO_URL,
        metavar="URL",
        help="override the installation archive URL",
    )
    install.add_argument(
        "--iso-sha256",
        metavar="HEX",
        help="SHA-256 digest for an overridden installation archive",
    )

    start = commands.add_parser("start", help="start an installed 9front instance")
    _add_runtime_options(start, memory=DEFAULT_START_MEMORY_MIB)
    return parser


def _absolute(path: Path) -> Path:
    return path.expanduser().resolve()


def _progress(quiet: bool):
    def write(message: str) -> None:
        if not quiet:
            print(message, flush=True)

    return write


def _select_acceleration(
    requested: str, host: HostInfo, executable: str
) -> Acceleration:
    if requested == "whpx" and host.system == "Windows":
        available = query_qemu_accelerators(executable)
        return resolve_acceleration(
            requested,
            host,
            available_accelerators=available,
        )
    return resolve_acceleration(requested, host)


def _run_qemu(command: list[str], *, system: str, dry_run: bool, quiet: bool) -> int:
    if not quiet:
        heading = "Would start QEMU:" if dry_run else "Starting QEMU:"
        print(f"\n{heading}\n", flush=True)
        print(render_command(command, system=system), flush=True)
    if dry_run:
        return 0
    try:
        return subprocess.run(command, check=False).returncode
    except OSError as error:
        raise P9QemuError(f"could not start QEMU: {error}") from error


def _install(args: argparse.Namespace) -> int:
    validate_disk_size(args.disk_size)
    host = current_host()
    executables = discover_qemu(host)
    acceleration = _select_acceleration(args.accel, host, executables.system)
    progress = _progress(args.quiet)
    disk = _absolute(args.disk)
    cache = user_cache_dir(host)

    checksum = args.iso_sha256
    if checksum is None and args.iso_url == DEFAULT_ISO_URL:
        checksum = DEFAULT_ARCHIVE_SHA256
    if checksum is not None:
        checksum = checksum.lower()
        if len(checksum) != 64 or any(
            character not in "0123456789abcdef" for character in checksum
        ):
            raise P9QemuError(
                "ISO archive SHA-256 must be exactly 64 hexadecimal characters"
            )
    elif not args.quiet:
        print(
            "Warning: checksum verification is disabled for the overridden ISO URL.",
            file=sys.stderr,
        )

    media = MediaSpec(url=args.iso_url, archive_sha256=checksum)
    inspect_disk(disk, args.disk_size, progress=lambda _message: None)
    command = build_install_command(
        executables.system,
        disk=disk,
        iso=cache / media.iso_name,
        memory_mib=args.memory,
        acceleration=acceleration,
    )
    if args.dry_run:
        media_paths = inspect_media(cache, media, progress=progress)
        inspect_disk(disk, args.disk_size, progress=progress)
    else:
        media_paths = prepare_media(cache, media, progress=progress)
        prepare_disk(
            executables.image,
            disk,
            args.disk_size,
            progress=progress,
        )

    if not args.quiet:
        progress(f"Acceleration: {acceleration.name}")
    # The preflight command uses this same resolved path. Keep the explicit
    # equality check close to orchestration so future media layouts cannot
    # silently make the displayed command differ from the prepared artifact.
    assert media_paths.iso == cache / media.iso_name
    return _run_qemu(
        command,
        system=host.system,
        dry_run=args.dry_run,
        quiet=args.quiet,
    )


def _start(args: argparse.Namespace) -> int:
    host = current_host()
    executables = discover_qemu(host)
    acceleration = _select_acceleration(args.accel, host, executables.system)
    progress = _progress(args.quiet)
    disk = _absolute(args.disk)
    if not disk.exists():
        raise P9QemuError(
            f"disk image does not exist: {disk}\n"
            "Run p9qemu install in this instance directory first."
        )
    if not disk.is_file():
        raise P9QemuError(f"disk path is not a file: {disk}")
    progress(f"Using disk image: {disk}")
    progress(f"Acceleration: {acceleration.name}")
    command = build_start_command(
        executables.system,
        disk=disk,
        memory_mib=args.memory,
        acceleration=acceleration,
    )
    return _run_qemu(
        command,
        system=host.system,
        dry_run=args.dry_run,
        quiet=args.quiet,
    )


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "install":
            return _install(args)
        if args.command == "start":
            return _start(args)
        raise P9QemuError(f"unknown command: {args.command}")
    except P9QemuError as error:
        print(f"p9qemu: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\np9qemu: interrupted", file=sys.stderr)
        return 130


def main() -> None:
    raise SystemExit(run())
