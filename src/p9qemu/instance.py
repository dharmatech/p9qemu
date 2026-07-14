"""Instance disk validation and safe creation."""

from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path
import re
import subprocess
from uuid import uuid4

from p9qemu.errors import P9QemuError


Progress = Callable[[str], None]
Runner = Callable[..., subprocess.CompletedProcess[object]]

_DISK_SIZE = re.compile(r"^[1-9][0-9]*(?:[KMGTPE])?$", re.IGNORECASE)


def validate_disk_size(size: str) -> None:
    if not _DISK_SIZE.fullmatch(size):
        raise P9QemuError(
            f"invalid disk size {size!r}; use a positive size such as 30G"
        )


def _disk_exists(path: Path, size: str) -> bool:
    validate_disk_size(size)
    if path.exists():
        if not path.is_file():
            raise P9QemuError(f"disk path exists but is not a file: {path}")
        return True
    if not path.parent.is_dir():
        raise P9QemuError(f"disk parent directory does not exist: {path.parent}")
    return False


def inspect_disk(path: Path, size: str, *, progress: Progress) -> bool:
    if _disk_exists(path, size):
        progress(f"Using existing disk image: {path}")
        return True
    progress(f"Would create {size} QCOW2 disk image: {path}")
    return False


def prepare_disk(
    qemu_img: str,
    path: Path,
    size: str,
    *,
    progress: Progress,
    runner: Runner = subprocess.run,
) -> None:
    if _disk_exists(path, size):
        progress(f"Using existing disk image: {path}")
        return

    temporary = path.with_name(f".{path.name}.p9qemu-{uuid4().hex}.part")
    command = [qemu_img, "create", "-f", "qcow2", str(temporary), size]
    progress(f"Creating {size} QCOW2 disk image: {path}")
    try:
        result = runner(command, check=False)
        if result.returncode != 0:
            raise P9QemuError(f"qemu-img exited with status {result.returncode}")
        if not temporary.is_file():
            raise P9QemuError("qemu-img did not create the requested disk image")
        try:
            os.link(temporary, path)
        except FileExistsError as error:
            raise P9QemuError(
                f"refusing to replace disk image created concurrently: {path}"
            ) from error
        except OSError as error:
            raise P9QemuError(
                f"could not publish disk image {path}: {error}"
            ) from error
    except OSError as error:
        raise P9QemuError(f"could not run {qemu_img}: {error}") from error
    finally:
        temporary.unlink(missing_ok=True)


def prepare_validation_overlay(
    qemu_img: str,
    base: Path,
    overlay: Path,
    *,
    progress: Progress,
    runner: Runner = subprocess.run,
) -> None:
    """Create a new QCOW2 overlay without modifying its base image."""

    if not base.is_file():
        raise P9QemuError(f"validation base image is not a file: {base}")
    if overlay.exists():
        raise P9QemuError(f"refusing to replace validation overlay: {overlay}")
    if not overlay.parent.is_dir():
        raise P9QemuError(
            f"validation overlay parent directory does not exist: {overlay.parent}"
        )
    if base.resolve() == overlay.resolve():
        raise P9QemuError("validation overlay must differ from its base image")

    temporary = overlay.with_name(f".{overlay.name}.p9qemu-{uuid4().hex}.part")
    command = [
        qemu_img,
        "create",
        "-f",
        "qcow2",
        "-F",
        "qcow2",
        "-b",
        str(base.resolve()),
        str(temporary),
    ]
    progress(f"Creating disposable QCOW2 validation overlay: {overlay}")
    try:
        result = runner(command, check=False)
        if result.returncode != 0:
            raise P9QemuError(
                f"qemu-img overlay creation exited with status {result.returncode}"
            )
        if not temporary.is_file():
            raise P9QemuError("qemu-img did not create the validation overlay")
        try:
            os.link(temporary, overlay)
        except FileExistsError as error:
            raise P9QemuError(
                f"refusing to replace validation overlay created concurrently: {overlay}"
            ) from error
        except OSError as error:
            raise P9QemuError(
                f"could not publish validation overlay {overlay}: {error}"
            ) from error
    except OSError as error:
        raise P9QemuError(f"could not run {qemu_img}: {error}") from error
    finally:
        temporary.unlink(missing_ok=True)
